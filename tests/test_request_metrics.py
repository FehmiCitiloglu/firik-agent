"""Actual runtime request accounting, with offline scripted providers only."""
import asyncio
import threading

import httpx
import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.compaction import CompactionState, ContextCompactor
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message, StreamChunk
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter


class ReplayProvider:
    name = "replay"
    current_model = "fixture-v1"
    model_metadata = {}

    async def chat_stream(self, **kwargs):
        yield StreamChunk(delta_content="Answer", usage={"prompt_tokens": 10})
        yield StreamChunk(usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})

    async def chat(self, **kwargs):
        return Message(role="assistant", content="Summary", usage={"prompt_tokens": 20, "completion_tokens": 4,
                                                                    "total_tokens": 24})


def runtime(tmp_path, provider):
    agent = Agent(config={"agent": {"context_compaction": {"enabled": False}},
                          "providers": {}, "skills": {}, "plugins": {}})
    agent._is_initialized = True
    agent.providers.register("replay", provider, set_active=True)
    session = ChatSession(model=provider.current_model)
    control = CodingRunControl(session.session_id, "fixture", mode="ask")
    control.capabilities = {"source": "verified_config", "input_cost_per_million": 1, "output_cost_per_million": 2}
    return agent, session, control, CodingToolFilter(tmp_path, control)


async def execute(parts, **kwargs):
    agent, session, control, tools = parts
    return [event async for event in agent.run_stream("Read only", session=session, run_control=control,
                                                     session_tool_filter=tools, **kwargs)]


def requests(events, kind=AgentEventType.MODEL_REQUEST_FINISHED):
    return [event.metadata for event in events if event.type == kind]


@pytest.mark.asyncio
async def test_successful_request_has_identity_and_snapshot_usage(tmp_path):
    events = await execute(runtime(tmp_path, ReplayProvider()))
    starts, finishes = requests(events, AgentEventType.MODEL_REQUEST_STARTED), requests(events)
    assert len(starts) == len(finishes) == 1
    assert starts[0]["request_id"] == finishes[0]["request_id"]
    finish = finishes[0]
    assert finish["request_kind"] == "model" and finish["provider"] == "replay"
    assert finish["model"] == "fixture-v1" and finish["status"] == "completed"
    assert finish["usage"]["total_tokens"] == 12 and finish["usage_complete"]
    assert finish["usage"]["cost"] == pytest.approx(14 / 1_000_000)
    assert 0 <= finish["metrics"]["ttft_ms"] <= finish["metrics"]["latency_ms"]
    assert finish["metrics"]["retry_count"] == 0 and finish["error_type"] is None


@pytest.mark.asyncio
async def test_sparse_usage_frames_retain_cache_evidence_and_discount(tmp_path):
    class Sparse(ReplayProvider):
        async def chat_stream(self, **kwargs):
            yield StreamChunk(delta_content="answer", usage={"prompt_tokens": 10,
                                                           "prompt_tokens_details": {"cached_tokens": 4}})
            yield StreamChunk(usage={"completion_tokens": 2, "prompt_tokens_details": {"audio_tokens": 0}})
    parts = runtime(tmp_path, Sparse())
    parts[2].capabilities["cached_input_cost_per_million"] = .25
    finish, = requests(await execute(parts))
    assert finish["usage"]["cached_tokens"] == 4
    assert finish["usage"]["cost"] == pytest.approx(11 / 1_000_000)


@pytest.mark.asyncio
@pytest.mark.parametrize("first,updated,incoming,outgoing,total,absent", [
    ({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
     {"completion_tokens": 5}, 10, 5, 15, "output_tokens"),
    ({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
     {"output_tokens": 5}, 10, 5, 15, "completion_tokens"),
    ({"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
     {"prompt_tokens": 20, "completion_tokens": 3}, 20, 3, 23, "input_tokens"),
    ({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
     {"completion_tokens": 5, "total_tokens": 19}, 10, 5, 19, "output_tokens"),
])
async def test_sparse_component_updates_replace_stale_totals_and_aliases_in_real_stream(
        tmp_path, first, updated, incoming, outgoing, total, absent):
    from firik_agent.core.metrics import RunMetrics
    class Sparse(ReplayProvider):
        async def chat_stream(self, **kwargs):
            yield StreamChunk(delta_content="answer", usage=first)
            yield StreamChunk(usage=updated)
    events = await execute(runtime(tmp_path, Sparse()))
    finish, = requests(events)
    assert finish["usage"]["total_tokens"] == total
    assert absent not in finish["usage"]
    ledger = RunMetrics()
    for event in events:
        ledger.observe(event)
    snapshot = ledger.snapshot()
    assert snapshot["usage"]["prompt_tokens"] == incoming
    assert snapshot["usage"]["completion_tokens"] == outgoing
    assert snapshot["usage"]["total_tokens"] == total
    assert snapshot["usage_complete"]


@pytest.mark.asyncio
async def test_new_detail_alias_replaces_old_cache_count_in_real_stream(tmp_path):
    from firik_agent.core.metrics import RunMetrics
    class Sparse(ReplayProvider):
        async def chat_stream(self, **kwargs):
            yield StreamChunk(delta_content="answer", usage={"prompt_tokens": 10, "completion_tokens": 2,
                                                           "prompt_tokens_details": {"cached_tokens": 4}})
            yield StreamChunk(usage={"input_tokens_details": {"cached_tokens": 8}})
    parts = runtime(tmp_path, Sparse())
    parts[2].capabilities["cached_input_cost_per_million"] = .25
    events = await execute(parts)
    finish, = requests(events)
    assert "prompt_tokens_details" not in finish["usage"]
    assert finish["usage"]["cached_tokens"] == 8
    assert finish["usage"]["cost"] == pytest.approx(8 / 1_000_000)
    ledger = RunMetrics()
    for event in events:
        ledger.observe(event)
    assert ledger.snapshot()["usage"]["cached_tokens"] == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("first,updated,expected_cache", [
    ({"prompt_tokens_details": {"cached_tokens": 4, "audio_tokens": 2}}, {"cached_tokens": 8}, 8),
    ({"prompt_tokens_details": {"cached_tokens": 4, "audio_tokens": 2}}, {"cache_read_input_tokens": 8}, 8),
    ({"cached_tokens": 4, "prompt_tokens_details": {"audio_tokens": 2}},
     {"prompt_tokens_details": {"cached_tokens": 8}}, 8),
    ({"cache_read_input_tokens": 4, "prompt_tokens_details": {"audio_tokens": 2}},
     {"input_tokens_details": {"cached_tokens": 8}}, 8),
    ({"prompt_tokens_details": {"cached_tokens": 4, "audio_tokens": 2}}, {"cached_tokens": None}, None),
    ({"cached_tokens": 4, "prompt_tokens_details": {"audio_tokens": 2}},
     {"prompt_tokens_details": {"cached_tokens": -1}}, None),
])
async def test_cache_alias_updates_revoke_previous_shape_and_reprice_real_stream(tmp_path, first, updated, expected_cache):
    from firik_agent.core.metrics import RunMetrics
    class Sparse(ReplayProvider):
        async def chat_stream(self, **kwargs):
            yield StreamChunk(delta_content="answer", usage={"prompt_tokens": 10, "completion_tokens": 2,
                                                           "cost": .99, **first})
            yield StreamChunk(usage=updated)
    parts = runtime(tmp_path, Sparse())
    parts[2].capabilities["cached_input_cost_per_million"] = .25
    events = await execute(parts)
    finish, = requests(events)
    assert finish["usage"]["cached_tokens"] == expected_cache
    assert "cache_read_input_tokens" not in finish["usage"]
    details = finish["usage"].get("prompt_tokens_details") or finish["usage"].get("input_tokens_details")
    assert details["audio_tokens"] == 2 and "cached_tokens" not in details
    # Unknown cache usage uses the existing explicitly labelled standard-price
    # estimate; it cannot retain the earlier provider-reported cost.
    expected_cost = ((10 - (expected_cache or 0)) + (expected_cache or 0) * .25 + 4) / 1_000_000
    assert finish["usage"]["cost"] == pytest.approx(expected_cost)
    assert finish["cost_report"]["estimate_only"]
    ledger = RunMetrics()
    for event in events:
        ledger.observe(event)
    snapshot = ledger.snapshot()
    assert snapshot["usage"]["cached_tokens"] == expected_cache
    assert snapshot["usage"]["cost"] == pytest.approx(expected_cost)


@pytest.mark.asyncio
async def test_fallback_logger_records_class_without_exception_message(tmp_path, caplog):
    class Unsupported(ReplayProvider):
        async def chat_stream(self, **kwargs):
            raise NotImplementedError("private fixture detail must not enter logs")
            yield
    await execute(runtime(tmp_path, Unsupported()))
    assert "NotImplementedError" in caplog.text
    assert "private fixture detail" not in caplog.text


@pytest.mark.asyncio
async def test_unknown_failed_request_is_recorded_without_retry(tmp_path):
    class Failure(ReplayProvider):
        async def chat_stream(self, **kwargs):
            raise ConnectionError("token=do-not-store-this")
            yield
    parts = runtime(tmp_path, Failure())
    parts[2].permit_provider_fallback = False
    events = await execute(parts)
    finish, = requests(events)
    assert finish["status"] == "failed" and finish["usage"] == {}
    assert not finish["usage_complete"] and finish["error_type"] == "ConnectionError"
    assert "do-not-store-this" not in str(finish)
    assert finish["metrics"]["ttft_ms"] is None


@pytest.mark.asyncio
async def test_partial_failure_keeps_usage_but_marks_incomplete_and_never_replays(tmp_path):
    class Partial(ReplayProvider):
        calls = 0
        async def chat_stream(self, **kwargs):
            self.calls += 1
            yield StreamChunk(delta_content="partial", usage={"prompt_tokens": 5})
            raise ConnectionError("closed")
        async def chat(self, **kwargs):
            raise AssertionError("partial stream cannot be replayed")
    provider = Partial()
    events = await execute(runtime(tmp_path, provider))
    finish, = requests(events)
    assert provider.calls == 1 and finish["status"] == "failed"
    assert finish["usage"]["prompt_tokens"] == 5 and not finish["usage_complete"]
    assert finish["metrics"]["ttft_ms"] is not None


@pytest.mark.asyncio
async def test_fallback_is_a_separate_accounted_request(tmp_path):
    class Fallback(ReplayProvider):
        async def chat_stream(self, **kwargs):
            raise NotImplementedError("stream unsupported")
            yield
    events = await execute(runtime(tmp_path, Fallback()))
    failed, completed = requests(events)
    assert failed["request_id"] != completed["request_id"]
    assert completed["parent_request_id"] == failed["request_id"]
    assert failed["status"] == "failed" and failed["usage"] == {}
    assert completed["status"] == "completed" and completed["usage"]["total_tokens"] == 24
    assert completed["metrics"]["retry_count"] == 1
    assert completed["metrics"]["ttft_ms"] is None  # Nonstream response has no TTFT evidence.


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ConnectionError("connection closed"), RuntimeError("unknown failure"), PermissionError("policy refusal"),
    *[httpx.HTTPStatusError(f"HTTP {status}", request=httpx.Request("POST", "https://fixture.invalid/v1"),
                           response=httpx.Response(status)) for status in (400, 401, 403, 500)],
])
async def test_pre_frame_failure_never_replays_transport_auth_refusal_or_unknown(tmp_path, error):
    class Failed(ReplayProvider):
        chat_calls = 0
        async def chat_stream(self, **kwargs):
            raise error
            yield
        async def chat(self, **kwargs):
            self.chat_calls += 1
            return await super().chat(**kwargs)
    provider = Failed()
    events = await execute(runtime(tmp_path, provider))
    assert provider.chat_calls == 0
    assert len(requests(events, AgentEventType.MODEL_REQUEST_STARTED)) == 1
    finish, = requests(events)
    assert finish["status"] == "failed" and finish["usage"] == {}
    assert events[-1].type == AgentEventType.ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("control_cancel", [False, True])
async def test_cancellation_at_fallback_boundary_never_starts_second_request(tmp_path, control_cancel):
    cancel_event = threading.Event()
    class Unsupported(ReplayProvider):
        chat_calls = 0
        async def chat_stream(self, **kwargs):
            if control_cancel:
                parts[2].cancel()
            else:
                cancel_event.set()
            raise NotImplementedError("stream unsupported")
            yield
        async def chat(self, **kwargs):
            self.chat_calls += 1
            return await super().chat(**kwargs)
    provider, events = Unsupported(), []
    parts = runtime(tmp_path, provider)
    async def consume():
        agent, session, control, tools = parts
        async for event in agent.run_stream("cancel", session=session, run_control=control,
                                            session_tool_filter=tools, cancel_event=cancel_event):
            events.append(event)
    if control_cancel:
        with pytest.raises(asyncio.CancelledError):
            await consume()
    else:
        await consume()
    assert provider.chat_calls == 0
    assert len(requests(events, AgentEventType.MODEL_REQUEST_STARTED)) == 1
    assert len(requests(events)) == 1


@pytest.mark.asyncio
async def test_task_cancellation_finalizes_in_flight_request(tmp_path):
    started = asyncio.Event()
    class Waiting(ReplayProvider):
        async def chat_stream(self, **kwargs):
            started.set()
            await asyncio.Event().wait()
            yield
    parts, events = runtime(tmp_path, Waiting()), []
    async def consume():
        agent, session, control, tools = parts
        async for event in agent.run_stream("wait", session=session, run_control=control, session_tool_filter=tools):
            events.append(event)
    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    finish, = requests(events)
    assert finish["status"] == "cancelled" and not finish["usage_complete"]
    assert finish["error_type"] == "CancelledError" and finish["metrics"]["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_boolean_cancellation_records_usage_before_terminating(tmp_path):
    cancelled = threading.Event()
    class Cancelled(ReplayProvider):
        async def chat_stream(self, **kwargs):
            cancelled.set()
            yield StreamChunk(delta_content="ignored", usage={"prompt_tokens": 5})
    events = await execute(runtime(tmp_path, Cancelled()), cancel_event=cancelled)
    finish, = requests(events)
    assert finish["status"] == "cancelled" and finish["usage"]["prompt_tokens"] == 5
    assert not finish["usage_complete"] and events[-1].type == AgentEventType.DONE


def history():
    return [Message(role=role, content=f"turn {i} " + "x" * 500)
            for i in range(30) for role in ("user", "assistant")]


@pytest.mark.asyncio
async def test_compaction_accounts_for_each_real_summary_request():
    observations = []
    compactor = ContextCompactor({"fallback_input_tokens": 3000, "summary_max_tokens": 256})
    details = await compactor.compact(CompactionState(), history(), [], [], 3000, ReplayProvider(),
                                     on_request_event=lambda kind, metadata: observations.append((kind, metadata)))
    starts = [metadata for kind, metadata in observations if kind == "model_request_started"]
    finishes = [metadata for kind, metadata in observations if kind == "model_request_finished"]
    assert len(starts) == len(finishes) == details["summary_requests"] > 1
    assert {item["request_id"] for item in starts} == {item["request_id"] for item in finishes}
    assert all(item["request_kind"] == "compaction" and item["usage"]["total_tokens"] == 24 for item in finishes)
    assert all(item["metrics"]["ttft_ms"] is None for item in finishes)


@pytest.mark.asyncio
async def test_coding_stream_summary_costs_enter_the_same_request_ledger(tmp_path):
    from firik_agent.core.metrics import RunMetrics
    parts = runtime(tmp_path, ReplayProvider())
    parts[0].context_compactor = ContextCompactor({"fallback_input_tokens": 3000, "summary_max_tokens": 256})
    parts[1].messages = history()
    events = await execute(parts)
    summaries = [metadata for metadata in requests(events) if metadata["request_kind"] == "compaction"]
    assert len(summaries) > 1
    assert all(metadata["usage"]["cost"] == pytest.approx(28 / 1_000_000) for metadata in summaries)
    ledger = RunMetrics()
    for event in events:
        ledger.observe(event)
    snapshot = ledger.snapshot()
    assert snapshot["compaction_requests"] == len(summaries)
    assert snapshot["request_count"] == len(summaries) + 1
    assert snapshot["usage"]["total_tokens"] == len(summaries) * 24 + 12
    assert snapshot["usage"]["cost"] == pytest.approx((len(summaries) * 28 + 14) / 1_000_000)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_summary_still_finalizes(cancel):
    observations = []
    class Failure(ReplayProvider):
        async def chat(self, **kwargs):
            if cancel:
                raise asyncio.CancelledError()
            raise ConnectionError("failed summary")
    compactor = ContextCompactor({"fallback_input_tokens": 3000, "summary_max_tokens": 256})
    expected = asyncio.CancelledError if cancel else ConnectionError
    with pytest.raises(expected):
        await compactor.compact(CompactionState(), history(), [], [], 3000, Failure(),
                                on_request_event=lambda kind, metadata: observations.append((kind, metadata)))
    assert [kind for kind, metadata in observations] == ["model_request_started", "model_request_finished"]
    finish = observations[-1][1]
    assert finish["status"] == ("cancelled" if cancel else "failed")
    assert finish["usage"] == {} and not finish["usage_complete"]


def test_message_accounting_never_enters_model_visible_history():
    message = Message(role="assistant", content="hello", usage={"total_tokens": 42})
    assert message.usage == {"total_tokens": 42}
    assert "usage" not in message.to_dict() and "usage" not in message.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", ["openai", "firik"])
async def test_nonstream_providers_preserve_usage_without_sending_it_back(monkeypatch, provider_class):
    from firik_agent.providers.firik import FirikProvider
    from firik_agent.providers.openai_provider import OpenAIProvider
    observed = []
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    def respond(request):
        observed.append(request.content.decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}], "usage": usage})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    provider = (OpenAIProvider if provider_class == "openai" else FirikProvider)(api_key="fixture-only")
    message = await provider.chat([Message(role="assistant", content="history", usage=usage)])
    assert message.usage == usage
    assert '"usage"' not in observed[0]
