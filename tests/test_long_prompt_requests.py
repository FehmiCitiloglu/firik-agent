"""Long desktop/CLI requests use endpoint limits before local compaction."""
import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.compaction import CompactionState
from firik_agent.core.events import AgentEventType as E
from firik_agent.core.failures import FailureKind, RuntimeFailure
from firik_agent.core.recovery import RecoveryBudget
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message
from firik_agent.providers.firik import FirikProvider


MODEL = "mimo-v2.6-pro"
LONG_PROMPT = "\n".join(f"Requirement {index}: preserve this complete implementation instruction." for index in range(550))
assert len(LONG_PROMPT.split()) > 2730


def runtime(monkeypatch, handler):
    agent = Agent(config={"agent": {"recovery": {"base_delay": 0}}, "providers": {}, "plugins": {}, "skills": {}})
    agent._is_initialized = True
    agent._build_full_system_prompt = lambda: "Follow the user request."
    provider = FirikProvider(api_key="fixture-key", base_url="https://catalogue.invalid/v1", default_model=MODEL)
    agent.providers.register("fixture", provider, set_active=True)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return agent, provider


def completion(request):
    body = json.loads(request.content)
    assert request.method == "POST" and request.url.path == "/v1/chat/completions"
    usage = {"prompt_tokens": 10000, "completion_tokens": 1, "total_tokens": 10001}
    if body["stream"]:
        frame = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}], "usage": usage}
        return httpx.Response(200, text=f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n")
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "done"}}], "usage": usage})


def catalogue(*models):
    return httpx.Response(200, json={"data": [
        {"id": model, "context_length": 1_048_576, "default_max_output_tokens": 16_384,
         "capabilities": {"tools": True, "thinking": True, "vision": True, "reasoning_effort": True}}
        for model in (models or (MODEL,))]})


async def execute(agent, streaming, prompt=LONG_PROMPT, *, session=None, cancel_event=None):
    events = []
    session = session or ChatSession()
    if streaming:
        events = [event async for event in agent.run_stream(prompt, session=session, cancel_event=cancel_event)]
    else:
        result = await agent.run(prompt, session=session, on_event=events.append, cancel_event=cancel_event)
        assert result == "done"
    return events, session


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_long_prompt_discovers_mimo_capacity_before_one_verbatim_submission(monkeypatch, streaming):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            assert request.url.path == "/v1/models" and not request.content
            return catalogue()
        return completion(request)

    agent, provider = runtime(monkeypatch, handler)
    events, session = await execute(agent, streaming)
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/v1/models"), ("POST", "/v1/chat/completions")]
    payload = json.loads(requests[-1].content)
    assert payload["model"] == MODEL
    assert payload["messages"][-1] == {"role": "user", "content": LONG_PROMPT}
    assert session.messages[0].content == LONG_PROMPT and session.compaction.cursor == 0
    assert not any(event.type in {E.ERROR, E.CONTEXT_COMPACTION_STARTED, E.CONTEXT_COMPACTED} for event in events)
    finished = next(event for event in events if event.type == E.MODEL_REQUEST_FINISHED)
    assert finished.metadata["resource_budget"]["metadata_requests_used"] == 1
    assert provider.model_metadata[MODEL]["context_window"] == 1_048_576

    # A new session on the same agent reuses the endpoint's established limit.
    second, _ = await execute(agent, streaming)
    assert [r.method for r in requests] == ["GET", "POST", "POST"]
    assert not any(event.type == E.ERROR for event in second)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("known", [False, True])
async def test_small_unknown_or_known_capacity_avoids_metadata_request(monkeypatch, streaming, known):
    requests = []

    def handler(request):
        requests.append(request)
        return completion(request)

    agent, provider = runtime(monkeypatch, handler)
    if known:
        provider.model_metadata[MODEL] = {"source": "provider", "context_window": 1_048_576}
    events, _ = await execute(agent, streaming, LONG_PROMPT if known else "Short request")
    assert [r.method for r in requests] == ["POST"]
    assert not any(event.type == E.ERROR for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("outcome", ["unknown", "unavailable", "revoked"])
async def test_failed_or_revoked_discovery_does_not_invent_capacity_or_submit_prompt(monkeypatch, streaming, outcome):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"  # No paid completion or summary may be sent.
        return httpx.Response(503) if outcome == "unavailable" else httpx.Response(200, json={"data": [{"id": MODEL}]})

    agent, provider = runtime(monkeypatch, handler)
    if outcome == "revoked":
        provider.model_metadata[MODEL] = {"source": "provider", "verified": False, "context_length": 1_048_576}
    for _ in range(2):
        session = ChatSession()
        if streaming:
            events, _ = await execute(agent, True, session=session)
            assert events[-1].type == E.ERROR and "bağlam bütçesine" in events[-1].content
        else:
            with pytest.raises(ValueError, match="bağlam bütçesine"):
                await execute(agent, False, session=session)
        assert session.messages[0].content == LONG_PROMPT and session.compaction.cursor == 0
    # Unavailable capacity is retried on a new user run, never by enlarging the
    # unknown-model budget or repeatedly submitting the current prompt.
    assert [r.method for r in requests] == ([] if outcome == "revoked" else ["GET", "GET"])


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_switching_models_discovers_only_selected_model_without_replaying(monkeypatch, streaming):
    requests = []

    def handler(request):
        requests.append(request)
        return catalogue(MODEL, "another-model") if request.method == "GET" else completion(request)

    agent, provider = runtime(monkeypatch, handler)
    await execute(agent, streaming)
    assert "another-model" not in provider.model_metadata
    provider.set_model("another-model")
    events, _ = await execute(agent, streaming)
    assert [r.method for r in requests] == ["GET", "POST", "GET", "POST"]
    assert [json.loads(r.content)["model"] for r in requests if r.method == "POST"] == [MODEL, "another-model"]
    assert not any(event.type == E.ERROR for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_failed_lookup_can_retry_after_endpoint_changes(monkeypatch, streaming):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(503) if request.url.host == "catalogue.invalid" else catalogue()
        return completion(request)

    agent, provider = runtime(monkeypatch, handler)
    if streaming:
        events, _ = await execute(agent, True)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(ValueError):
            await execute(agent, False)
    provider.base_url = "https://replacement.invalid/v1"
    events, _ = await execute(agent, streaming)
    assert [(r.method, r.url.host) for r in requests] == [
        ("GET", "catalogue.invalid"), ("GET", "replacement.invalid"), ("POST", "replacement.invalid")]
    assert not any(event.type == E.ERROR for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_cancel_during_capacity_lookup_stops_without_submission(monkeypatch, streaming):
    started, stopped = asyncio.Event(), asyncio.Event()
    cancellation = threading.Event()
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "POST":
            return completion(request)
        if len(requests) > 1:
            return catalogue()
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    agent, _ = runtime(monkeypatch, handler)
    task = asyncio.create_task(execute(agent, streaming, cancel_event=cancellation))
    await asyncio.wait_for(started.wait(), timeout=1)
    cancellation.set()
    if streaming:
        events, _ = await asyncio.wait_for(task, timeout=1)
        assert events[-1].type == E.DONE and "durduruldu" in events[-1].content
        assert not any(event.type == E.MODEL_REQUEST_STARTED for event in events)
    else:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
    assert stopped.is_set()
    assert [request.method for request in requests] == ["GET"]
    cancellation.clear()
    events, _ = await execute(agent, streaming, cancel_event=cancellation)
    assert [request.method for request in requests] == ["GET", "GET", "POST"]
    assert not any(event.type == E.ERROR for event in events)


async def resolve_window(agent, provider, *, control=None, budget=None):
    return await agent._resolve_context_window(provider, CompactionState(),
        [Message(role="user", content=LONG_PROMPT)], [Message(role="system", content="Instructions")], [], 2048,
        run_control=control, recovery_budget=budget or RecoveryBudget())


@pytest.mark.asyncio
async def test_revoked_provider_capacity_invalidates_stale_run_control_window(monkeypatch):
    agent, provider = runtime(monkeypatch, lambda request: pytest.fail("Revoked capacity must not trigger HTTP"))
    provider.model_metadata[MODEL] = {"source": "provider", "verified": False, "context_window": 1_048_576}
    control = SimpleNamespace(capabilities={"source": "provider", "context_window": 1_048_576})
    assert await resolve_window(agent, provider, control=control) is None
    assert control.capabilities["context_window"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["result_model", "selected_model", "endpoint"])
async def test_lookup_does_not_adopt_result_for_different_model_or_endpoint(monkeypatch, changed):
    agent, provider = runtime(monkeypatch, lambda request: pytest.fail("Scripted refresh must not trigger HTTP"))
    control = SimpleNamespace(capabilities={"context_window": None})

    async def refresh():
        await asyncio.sleep(0)
        if changed == "selected_model":
            provider.current_model = "different-model"
        elif changed == "endpoint":
            provider.base_url = "https://changed.invalid/v1"
        return {"status": "refreshed", "model": "different-model" if changed == "result_model" else MODEL,
                "capabilities": {"source": "provider", "context_window": 1_048_576}}

    provider.refresh_capabilities = refresh
    assert await resolve_window(agent, provider, control=control) is None
    assert control.capabilities["context_window"] is None


@pytest.mark.asyncio
async def test_unknown_capacity_lookup_is_bounded_inside_run_but_retries_next_run(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503)

    agent, provider = runtime(monkeypatch, handler)
    budget = RecoveryBudget()
    assert await resolve_window(agent, provider, budget=budget) is None
    assert await resolve_window(agent, provider, budget=budget) is None
    assert len(requests) == 1 and budget.metadata_requests == 1
    assert await resolve_window(agent, provider, budget=RecoveryBudget()) is None
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_provider_overflow_with_no_compaction_progress_does_not_replay_or_claim_success(monkeypatch, streaming):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        return httpx.Response(400, json={"error": {"code": "context_length_exceeded", "message": "context length exceeded"}})

    agent, provider = runtime(monkeypatch, handler)
    provider.model_metadata[MODEL] = {"source": "provider", "context_window": 1_048_576}
    session, events = ChatSession(), []
    if streaming:
        events, _ = await execute(agent, True, "Short user request", session=session)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(RuntimeFailure) as caught:
            await agent.run("Short user request", session=session, on_event=events.append)
        assert caught.value.kind == FailureKind.CONTEXT_OVERFLOW
    assert len(requests) == 1 and session.compaction.cursor == 0
    assert not any(event.type == E.CONTEXT_COMPACTED for event in events)
    assert len([event for event in events if event.type == E.MODEL_REQUEST_STARTED]) == 1
