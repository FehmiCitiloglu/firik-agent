"""Diagnostic report counts observed dispatches, not merely proposed retries."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def report_module(monkeypatch):
    spec = importlib.util.spec_from_file_location("phase2_recovery_report", Path(__file__).parents[1] / "scripts/phase2_recovery_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "validate_report", lambda report: None)
    return module


def event(kind, **metadata):
    return {"type": kind, "metadata": metadata}


def metrics(module, *trace):
    return module.trace_metrics({"results": [{"trace": trace}]})


def test_backoff_decision_without_dispatch_is_not_counted_as_retry(report_module):
    result = metrics(report_module, event("recovery", action="backoff", safe_to_repeat=True, delay=1))
    assert result["recovery_decisions"] == 1
    assert result["retry_requests"] == 0
    assert result["successful_retry_rate"] is None


def test_successful_retry_and_false_retry_are_distinguished(report_module):
    result = metrics(report_module,
        event("recovery", action="backoff", failure_kind="rate_limited", request_phase="submitted_no_output", safe_to_repeat=True),
        event("model_request_started", request_id="retry-1", attempt=1),
        event("model_request_finished", request_id="retry-1", status="completed"),
        event("recovery", action="retry", failure_kind="policy_refusal", safe_to_repeat=True),
        event("model_request_started", request_id="unsafe-1", attempt=1))
    assert result["retry_requests"] == 2
    assert result["successful_retry_requests"] == 1
    assert result["successful_retry_rate"] == .5
    assert result["false_retry_count"] == 1


def test_partial_response_is_false_retry_even_if_policy_says_safe(report_module):
    result = metrics(report_module,
        event("recovery", action="retry", failure_kind="transport_error", request_phase="partial_response", safe_to_repeat=True),
        event("model_request_started", request_id="unsafe", attempt=1))
    assert result["false_retry_count"] == 1


def test_unknown_external_mutation_repeat_is_visible(report_module):
    result = metrics(report_module,
        event("tool_call_started", fingerprint="abc", side_effect_risk="unknown"),
        event("tool_call_error", fingerprint="abc", request_phase="tool_dispatched", reconciliation_status="required"),
        event("recovery", action="reconcile", request_phase="tool_dispatched", reconciliation_status="required"),
        event("tool_call_started", fingerprint="abc", side_effect_risk="unknown"))
    assert result["uncertain_tool_reconciliation_decisions"] == 1
    assert result["uncertain_tool_operations_observed"] == 1
    assert result["uncertain_tool_operations_not_replayed"] == 0
    assert result["repeated_mutation_dispatches_after_unknown"] == 1


def test_known_local_interface_fallback_is_separate_and_not_false_retry(report_module):
    result = metrics(report_module,
        event("recovery", action="same_provider_nonstream", failure_kind="unsupported_capability", request_phase="not_submitted", safe_to_repeat=True),
        event("model_request_started", request_id="fallback", attempt=1))
    assert result["interface_fallback_requests"] == 1
    assert result["false_retry_count"] == 0


def test_intermediate_compaction_request_retains_retry_authorization(report_module):
    result = metrics(report_module,
        event("recovery", action="compact_context", failure_kind="context_overflow", request_phase="submitted_no_output", safe_to_repeat=True),
        event("model_request_started", request_id="summary", request_kind="compaction", attempt=0),
        event("model_request_started", request_id="retry", attempt=1))
    assert result["false_retry_count"] == 0
    assert result["unclassified_retry_requests"] == 0


def test_recorded_failed_tool_result_is_not_an_unknown_outcome(report_module):
    result = metrics(report_module,
        event("tool_call_started", fingerprint="read", side_effect_risk="pure_read"),
        event("tool_call_error", fingerprint="read", request_phase="tool_dispatched", reconciliation_status="not_required"))
    assert result["uncertain_tool_operations_observed"] == 0
