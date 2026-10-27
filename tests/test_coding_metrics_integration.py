"""Durable and desktop accounting uses the same passive request evidence."""
from types import SimpleNamespace

from test_coding_bridge import bridge as bridge, start, tool

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.types import Message, StreamChunk
from firik_agent.projects.cockpit import CockpitState
from firik_agent.ui.coding_evidence import (
    CodingEvidenceMixin, completion_accounting, reported_agent_usage, update_accounting_state,
)
from firik_agent.ui.coding_history import restore_state_history


def request(kind, identity, usage=None, **metadata):
    return {"type": kind, "metadata": {"request_id": identity, "iteration": 1,
        **({"usage": usage} if usage is not None else {}), **metadata}}


def test_cockpit_repeated_frames_and_child_requests_have_complete_totals():
    cockpit = CockpitState()
    cockpit.start()
    cockpit.apply(request("model_request_started", "main"))
    cockpit.apply(request("model_usage", "main", {"total_tokens": 3}))
    cockpit.apply(request("model_usage", "main", {"total_tokens": 7, "cost": .01}))
    cockpit.apply(request("model_request_finished", "main", status="completed"))
    for kind in ("model_request_started", "model_usage", "model_request_finished"):
        detail = request(kind, "child", {"total_tokens": 5, "cost": .02}, status="completed")
        cockpit.apply({"type": "subagent_event", "metadata": {"agent_id": "child-a",
            "child_event_type": kind, "child_metadata": detail["metadata"]}})
    cockpit.finish("completed")
    metrics = cockpit.summary["harness_metrics"]
    assert metrics["request_count"] == metrics["finished_requests"] == 2
    assert metrics["usage_complete"] and metrics["cost_complete"]
    assert cockpit.summary["usage"]["total_tokens"] == 12
    assert cockpit.summary["cost"] == .03


def test_incomplete_request_cannot_reuse_previous_known_usage():
    cockpit = CockpitState()
    cockpit.apply(request("model_request_started", "known"))
    cockpit.apply(request("model_request_finished", "known", {"total_tokens": 7}, status="completed"))
    cockpit.apply(request("model_request_started", "unknown"))
    cockpit.apply(request("model_request_finished", "unknown", status="failed",
        metrics={"latency_ms": 12, "ttft_ms": None}))
    cockpit.finish("failed")
    metrics = cockpit.summary["harness_metrics"]
    assert cockpit.summary["usage"]["total_tokens"] is None
    assert metrics["reported_usage"]["total_tokens"] == 7
    assert metrics["failed_requests"] == 1 and metrics["latency_ms"] == [12]
    assert cockpit.agents["orchestrator"]["metrics"]["latency_ms"] == 12


def test_bridge_persists_complete_totals_and_null_cost_without_provider_pricing(bridge):
    service, _, _ = bridge
    session, future = start(bridge)
    future.result(timeout=5)
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    metrics = record.usage["harness_metrics"]
    assert record.tokens_used == record.usage["total_tokens"] == 6
    assert metrics["request_count"] == metrics["finished_requests"] == 1
    assert metrics["usage_complete"] and metrics["usage"]["total_tokens"] == 6
    assert not metrics["cost_complete"] and metrics["usage"]["cost"] is None
    assert len(metrics["latency_ms"]) == len(metrics["ttft_ms"]) == 1


def test_unknown_request_usage_remains_unknown_after_truncated_history_restore(bridge):
    service, script, _ = bridge
    async def incomplete(iteration, messages):
        if iteration == 1:
            yield StreamChunk(tool_calls=[tool("read_file", path="main.py")])
        else:
            yield StreamChunk(delta_content="İncelendi", usage={"total_tokens": 6})
    script["script"] = incomplete
    session, future = start(bridge)
    future.result(timeout=5)
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    metrics = record.usage["harness_metrics"]
    assert record.tokens_used == 6  # Legacy compatibility field is unchanged.
    assert metrics["request_count"] == 2 and metrics["tool_calls"] == 1
    assert metrics["usage"]["total_tokens"] is None
    assert metrics["reported_usage"]["total_tokens"] == 6
    state = {"session": session, "preferences": {}, "capabilities": {}}
    restore_state_history(state, record, service.coding_store.list_events(record.id)[-1:])
    assert state["cockpit"].summary["harness_metrics"] == metrics
    assert state["cockpit"].summary["usage"]["total_tokens"] is None
    usage, displayed = completion_accounting(state)
    assert displayed["request_count"] == 2 and usage["total_tokens"] is None
    assert not state["busy"] and state["future"] is None


def test_partial_provider_failure_retains_attempt_metrics_without_claiming_total_usage(bridge):
    service, script, _ = bridge
    async def failed(iteration, messages):
        yield StreamChunk(delta_content="Kısmi yanıt", usage={"total_tokens": 9})
        raise ConnectionError("failed stream")
    script["script"] = failed
    session, future = start(bridge, on_error=lambda error: None)
    future.result(timeout=5)
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    metrics = record.usage["harness_metrics"]
    assert record.status == "blocked" and metrics["request_count"] == 1
    assert metrics["failed_requests"] == 1 and metrics["finished_requests"] == 1
    assert metrics["usage"]["total_tokens"] is None
    assert metrics["reported_usage"]["total_tokens"] == 9
    assert len(metrics["latency_ms"]) == 1


def test_compaction_request_start_preserves_uncommitted_partial_transcript(bridge, monkeypatch):
    service, _, _ = bridge
    async def interrupted(self, prompt, *, session, **kwargs):
        session.messages.append(Message(role="user", content=prompt))
        yield AgentEvent(AgentEventType.MODEL_REQUEST_STARTED, session.session_id,
            metadata={"request_id": "main", "request_kind": "model", "iteration": 1})
        yield AgentEvent(AgentEventType.TEXT_DELTA, session.session_id, content="Kısmi metin")
        yield AgentEvent(AgentEventType.MODEL_REQUEST_STARTED, session.session_id,
            metadata={"request_id": "summary", "request_kind": "compaction", "iteration": 2})
        yield AgentEvent(AgentEventType.ERROR, session.session_id, content="Özetleme kesildi")
    monkeypatch.setattr(Agent, "run_stream", interrupted)
    session, future = start(bridge, on_error=lambda error: None)
    future.result(timeout=5)
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert record.status == "failed"
    assert any(message.get("partial") and message.get("content") == "Kısmi metin" for message in record.messages)
    assert record.usage["harness_metrics"]["compaction_requests"] == 1


def test_legacy_completion_usage_is_preserved_without_new_metric_evidence():
    usage = {"total_tokens": 42, "cost": .5}
    state = {"usage": usage, "record": SimpleNamespace(usage=usage)}
    assert completion_accounting(state) == (usage, {})


def test_completion_view_labels_incomplete_coverage_and_actual_attempt_counts():
    displayed = {}
    metrics = {"usage": {"total_tokens": None, "cost": None}, "request_count": 2,
        "tool_calls": 3, "failed_requests": 1, "tool_errors": 1,
        "usage_complete": False, "cost_complete": False}
    state = {"busy": False, "last_prompt": "İstek", "status": "Hata", "harness_metrics": metrics}
    view = SimpleNamespace(_coding_state=lambda: state,
        coding_completion_label=SimpleNamespace(configure=lambda **values: displayed.update(values)),
        coding_completion_actions=SimpleNamespace(grid=lambda: None))
    CodingEvidenceMixin._refresh_coding_completion(view)
    assert "Model isteği: 2" in displayed["text"] and "Araç çağrısı: 3" in displayed["text"]
    assert "Token: Bilinmiyor" in displayed["text"] and "Maliyet bilinmiyor" in displayed["text"]
    assert "Kullanım/maliyet kaydı eksik" in displayed["text"] and "$0.0000" not in displayed["text"]


def test_streaming_text_does_not_recompute_aggregate_metrics():
    def unexpected():
        raise AssertionError("Streaming text must not scan the request history")
    state = {"cockpit": SimpleNamespace(metrics_snapshot=unexpected)}
    for kind, metadata in (("text_delta", {}), ("reasoning_delta", {}),
        ("tool_call_arguments", {}), ("subagent_event", {"child_event_type": "text_delta"})):
        update_accounting_state(state, SimpleNamespace(type=kind, metadata=metadata))


def test_partial_agent_subtotal_is_labelled_as_reported_and_incomplete():
    cockpit = CockpitState()
    cockpit.apply(request("model_request_started", "known"))
    cockpit.apply(request("model_request_finished", "known", {"total_tokens": 7}, status="completed"))
    cockpit.apply(request("model_request_started", "unknown"))
    cockpit.apply(request("model_request_finished", "unknown", status="failed"))
    label = reported_agent_usage(cockpit.agents["orchestrator"])
    assert label == "Bildirilen token: 7 · Kapsam eksik olabilir"
    cockpit.finish("failed")
    assert cockpit.summary["usage"]["total_tokens"] is None


def test_model_inspector_uses_authoritative_unknown_totals_from_restored_record(monkeypatch):
    labels = []
    class Label:
        def __init__(self, parent, **kwargs):
            labels.append(kwargs.get("text", ""))
        def pack(self, **kwargs):
            pass
    monkeypatch.setattr("firik_agent.ui.coding_evidence.ctk.CTkLabel", Label)
    accounting = {"usage": {"total_tokens": None, "cost": None}, "usage_complete": False, "cost_complete": False}
    state = {"usage": {"total_tokens": 7, "cost": .01, "harness_metrics": accounting},
        "record": SimpleNamespace(usage={"total_tokens": 7, "harness_metrics": accounting}),
        "session": SimpleNamespace(model="replay"), "preferences": {}}
    def render(key, parent, records, renderer, empty):
        renderer(None, records[0], records[0]["id"], False)
    view = SimpleNamespace(_coding_state=lambda: state, service=SimpleNamespace(provider="replay"),
        _sync_evidence_cards=render, coding_model_list=None, _evidence_text=lambda value, limit: value[:limit])
    CodingEvidenceMixin._refresh_coding_model_inspector(view)
    rendered = "\n".join(labels)
    assert "Token kapsamı eksik; toplam bilinmiyor." in rendered
    assert "Toplam: 7" not in rendered and "$0.0100" not in rendered
    assert "Maliyet bilinmiyor" in rendered
