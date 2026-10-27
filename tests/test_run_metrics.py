"""Accounting must never turn unknown charges into a complete zero total."""
import json

import pytest

from firik_agent.core.metrics import RunMetrics
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.projects.models import CodingRunEvent


def event(kind, identity="request-1", **metadata):
    return {"type": kind, "metadata": {"request_id": identity, **metadata}}


def finish(recorder, identity="request-1", usage=None, status="completed", **extra):
    recorder.observe(event("model_request_finished", identity, usage=usage or {}, status=status, **extra))


def test_cumulative_frames_aliases_cache_and_final_event_do_not_double_count():
    metrics = RunMetrics()
    metrics.observe(event("model_request_started"))
    metrics.observe(event("model_usage", usage={"input_tokens": 3, "output_tokens": 1}))
    metrics.observe(event("model_usage", usage={"input_tokens": 5, "output_tokens": 2,
        "input_tokens_details": {"cached_tokens": 2}, "cost_usd": 0.1}))
    finish(metrics, usage={"input_tokens": 5, "output_tokens": 2}, metrics={"latency_ms": 10, "ttft_ms": 2})
    # Replaying a final request snapshot is safe.
    finish(metrics, usage={"input_tokens": 5, "output_tokens": 2})
    result = metrics.snapshot()
    assert result["request_count"] == result["finished_requests"] == 1
    assert result["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cached_tokens": 2, "cost": .1}
    assert result["usage_complete"] and result["cost_complete"]
    assert result["latency_ms"] == [10] and result["ttft_ms"] == [2]


def test_unknown_and_partial_failed_requests_preserve_reported_subtotals():
    metrics = RunMetrics()
    finish(metrics, usage={"total_tokens": 9, "cost": .2})
    metrics.observe(event("model_request_started", "missing"))
    result = metrics.snapshot()
    assert result["usage"]["total_tokens"] is None and result["usage"]["cost"] is None
    assert result["reported_usage"]["total_tokens"] == 9
    finish(metrics, "missing", {"total_tokens": 3}, status="failed", metrics={"retry_count": 1})
    result = metrics.snapshot()
    assert not result["usage_complete"] and not result["cost_complete"]
    assert result["reported_usage"]["total_tokens"] == 12 and result["failed_requests"] == 1
    assert result["recovery_attempts"] == 1


def test_zero_usage_requires_actual_evidence_and_price_is_independent():
    metrics = RunMetrics()
    assert metrics.snapshot()["usage"]["total_tokens"] is None
    finish(metrics, usage={"total_tokens": 0})
    assert metrics.snapshot()["usage"]["total_tokens"] == 0
    assert metrics.snapshot()["usage"]["cost"] is None
    assert metrics.snapshot()["usage"]["cached_tokens"] is None


def test_sparse_snapshot_recomputes_total_and_invalidates_stale_cost():
    metrics = RunMetrics()
    metrics.observe(event("model_usage", usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": .1}))
    metrics.observe(event("model_usage", usage={"output_tokens": 5}))
    finish(metrics)
    result = metrics.snapshot()
    assert result["usage"]["total_tokens"] == 15
    assert result["usage"]["cost"] is None
    assert result["usage"]["completion_tokens"] == 5


def test_sparse_snapshot_with_unknown_input_invalidates_previous_total():
    metrics = RunMetrics()
    metrics.observe(event("model_usage", usage={"total_tokens": 12}))
    metrics.observe(event("model_usage", usage={"output_tokens": 5}))
    finish(metrics)
    assert metrics.snapshot()["usage"]["total_tokens"] is None
    assert metrics.snapshot()["usage"]["completion_tokens"] == 5


def test_explicit_unknown_latest_components_invalidate_old_evidence():
    metrics = RunMetrics()
    metrics.observe(event("model_usage", usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": .1}))
    metrics.observe(event("model_usage", usage={"input_tokens": None, "output_tokens": 5, "cost": None}))
    finish(metrics)
    result = metrics.snapshot()
    assert result["usage"]["prompt_tokens"] is None
    assert result["usage"]["total_tokens"] is None and result["usage"]["cost"] is None
    assert result["usage"]["completion_tokens"] == 5


def test_non_usd_costs_are_not_summed_or_displayed_as_dollars():
    metrics = RunMetrics()
    finish(metrics, usage={"total_tokens": 5, "cost": 20, "currency": "TRY"})
    assert metrics.snapshot()["usage"]["cost"] is None
    assert metrics.snapshot()["reported_usage"]["cost"] is None
    assert metrics.snapshot()["usage"]["total_tokens"] == 5
    explicit_usd = RunMetrics()
    finish(explicit_usd, usage={"cost": 20, "currency": "TRY", "cost_usd": .5})
    assert explicit_usd.snapshot()["usage"]["cost"] == .5


def test_raw_cache_alias_changes_replace_earlier_counts():
    metrics = RunMetrics()
    metrics.observe(event("model_usage", usage={"total_tokens": 12, "prompt_tokens_details": {"cached_tokens": 2}}))
    metrics.observe(event("model_usage", usage={"cache_read_input_tokens": 5}))
    finish(metrics)
    assert metrics.snapshot()["usage"]["cached_tokens"] == 5
    metrics.observe(event("model_usage", usage={"input_tokens_details": {"cached_tokens": 3}}))
    assert metrics.snapshot()["usage"]["cached_tokens"] == 3


def test_child_and_compaction_identities_replay_from_sqlite():
    metrics = RunMetrics()
    finish(metrics, usage={"total_tokens": 2}, iteration=1)
    finish(metrics, "summary", usage={"total_tokens": 4}, request_kind="compaction", iteration=1)
    child = {"type": "subagent_event", "metadata": {"agent_id": "child", "request_iteration": 1,
        "child_event_type": "model_request_finished", "child_metadata": {
            "request_id": "request-1", "status": "completed", "usage": {"total_tokens": 3}}}}
    metrics.observe(CodingRunEvent(id="e", run_id="r", session_id="s", project_id="p", sequence=1,
                                  event_type="subagent_event", data=child))
    result = metrics.snapshot()
    assert result["request_count"] == 3 and result["compaction_requests"] == 1
    assert result["usage"]["total_tokens"] == 9


def test_legacy_usage_is_partial_evidence_and_bounded_state_is_explicit():
    metrics = RunMetrics(max_requests=2)
    metrics.observe({"type": "model_usage", "metadata": {"iteration": 1, "usage": {"total_tokens": 5}}})
    assert metrics.snapshot()["reported_usage"]["total_tokens"] == 5
    assert metrics.snapshot()["usage"]["total_tokens"] is None
    finish(metrics, "second", usage={"total_tokens": 2})
    finish(metrics, "overflow", usage={"total_tokens": 3})
    assert len(metrics._requests) == 2
    assert metrics.snapshot()["accounting_limited"]
    assert metrics.snapshot()["usage"]["total_tokens"] is None


def test_invalid_metric_values_and_sensitive_payloads_are_not_retained():
    metrics = RunMetrics()
    metrics.observe(AgentEvent(type=AgentEventType.TOOL_CALL_STARTED, session_id="s",
                              arguments={"token": "sensitive-value"}))
    metrics.observe(AgentEvent(type=AgentEventType.TOOL_CALL_ERROR, session_id="s", duration_ms=4,
                              content="password=never-retain"))
    finish(metrics, usage={"prompt_tokens": True, "total_tokens": float("nan"), "cost": -1},
           metrics={"latency_ms": float("inf"), "ttft_ms": -1})
    result = metrics.snapshot()
    assert result["usage"]["total_tokens"] is None and result["usage"]["cost"] is None
    assert result["latency_ms"] == result["ttft_ms"] == []
    assert result["tool_calls"] == result["tool_errors"] == 1 and result["tool_duration_ms"] == 4
    serialized = json.dumps(result, allow_nan=False)
    assert "sensitive-value" not in serialized and "never-retain" not in serialized


@pytest.mark.parametrize("limit", [0, -1, True, 100001])
def test_invalid_resource_bounds_are_rejected(limit):
    with pytest.raises(ValueError):
        RunMetrics(max_requests=limit)


def test_sparse_different_requests_have_additive_known_token_lower_bound():
    metrics = RunMetrics()
    finish(metrics, "total-only", usage={"total_tokens": 60})
    finish(metrics, "input-only", usage={"prompt_tokens": 60})
    result = metrics.snapshot()
    assert result["reported_token_lower_bound"] == 120
    assert result["usage"]["total_tokens"] is None
    assert result["usage"]["cost"] is None


def test_fractional_token_reports_do_not_become_exact_counts():
    metrics = RunMetrics()
    finish(metrics, usage={"total_tokens": 1.5, "cached_tokens": 0.5, "cost": 0.01})
    result = metrics.snapshot()
    assert result["usage"]["total_tokens"] is None
    assert result["usage"]["cached_tokens"] is None
    assert result["usage"]["cost"] == 0.01
