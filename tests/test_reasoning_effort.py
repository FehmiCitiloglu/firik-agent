"""Model capability gating, request transport, UI and persistence regressions."""
import json
import httpx
import pytest

from test_ui_model_selection import service_factory

from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message
from firik_agent.providers.firik import FirikProvider
from firik_agent.providers.openai_provider import OpenAIProvider
from firik_agent.providers.reasoning import effort_levels, effort_payload


def metadata(values=None, aliases=None, supported=True):
    return {"capabilities": {"reasoning_effort": supported, "reasoning_effort_values": values,
                             "reasoning_effort_aliases": aliases}}


def test_explicit_support_lists_and_aliases():
    assert effort_levels({}) == []
    assert effort_levels(metadata(["high"], supported=False)) == []
    assert effort_levels(metadata()) == ["low", "medium", "high"]
    assert effort_levels(metadata(["none", "minimal", "xhigh"])) == ["none", "minimal", "xhigh"]
    assert effort_payload(metadata(aliases={"medium": "high"}), "medium") == {"reasoning_effort": "high"}
    assert effort_payload(metadata(["low"]), "high") == {}
    assert effort_payload(metadata(), None) == {}
    assert effort_payload(metadata(["none"]), "none") == {"reasoning_effort": "none"}


@pytest.mark.parametrize("provider_class", [FirikProvider, OpenAIProvider])
@pytest.mark.asyncio
async def test_stream_and_nonstream_transport_gate_by_current_model(provider_class, monkeypatch):
    provider = provider_class(default_model="supported")
    provider.model_metadata = {"supported": metadata(["none", "high"]), "unsupported": metadata(supported=False)}
    provider.reasoning_effort = "high"
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["stream"]:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    messages = [Message(role="user", content="hello")]
    await provider.chat(messages)
    assert [chunk async for chunk in provider.chat_stream(messages)]
    provider.set_model("unsupported")
    await provider.chat(messages)
    provider.set_model("unknown")
    assert [chunk async for chunk in provider.chat_stream(messages)]
    assert [r.get("reasoning_effort") for r in requests] == ["high", "high", None, None]


def test_chat_effort_survives_history(tmp_path):
    from firik_agent.core.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path / "history.db")
    session = ChatSession(model="supported", reasoning_effort="none")
    session.messages = [Message(role="user", content="hello")]
    store.save_transcript(session, [{"role": "user", "content": "hello"}])
    record = store.load(session.session_id)
    assert store.restore_session(record).reasoning_effort == "none"





@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_agent_uses_session_effort_and_resets_next_request(monkeypatch, stream):
    from firik_agent.core.agent import Agent
    agent = Agent(config={"providers": {}, "mcp_servers": {},
                          "plugins": {"autoload_builtins": False}, "skills": {"active_skills": []}})
    agent._is_initialized = True
    provider = FirikProvider(default_model="supported")
    provider.model_metadata = {"supported": metadata(["high"])}
    agent.providers.register("test", provider, set_active=True)
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["stream"]:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    for selected in ("high", None):
        session = ChatSession(model="supported", reasoning_effort=selected)
        if stream:
            assert [event async for event in agent.run_stream("hello", session=session)]
        else:
            assert await agent.run("hello", session=session)
    assert [r.get("reasoning_effort") for r in requests] == ["high", None]


def test_service_metadata_stays_scoped_to_server(service_factory):
    service = service_factory()
    service._model_metadata = {"supported": metadata(["high"])}
    provider = FirikProvider(default_model="supported", base_url=service.base_url)
    service.configure_model_metadata(provider)
    provider.reasoning_effort = "high"
    assert provider.reasoning_options() == {"reasoning_effort": "high"}
    service.base_url = "https://other.invalid/v1"
    assert service.model_effort_levels("supported") == []
    provider = FirikProvider(default_model="supported", base_url=service.base_url)
    service.configure_model_metadata(provider)
    provider.reasoning_effort = "high"
    assert provider.reasoning_options() == {}
