from __future__ import annotations
import json
import httpx
import pytest
from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message
from firik_agent.providers.firik import FirikProvider, DEFAULT_FIRIK_DIRECT_URL, DEFAULT_LLMTR_GATEWAY_URL
from firik_agent.providers.registry import ProviderRegistry


def test_firik_direct_provider_init():
    prov = FirikProvider(
        api_key="test_key_123",
        base_url=DEFAULT_FIRIK_DIRECT_URL,
        default_model="glm-5.3",
    )
    assert prov.name == "firik"
    assert prov.is_gateway is False
    assert prov.current_model == "glm-5.3"
    headers = prov._get_headers()
    assert headers["Authorization"] == "Bearer test_key_123"
    assert headers["X-API-Key"] == "test_key_123"


def test_firik_gateway_provider_init():
    prov = FirikProvider(
        api_key="llmtr_key_456",
        base_url=DEFAULT_LLMTR_GATEWAY_URL,
    )
    assert prov.is_gateway is True
    assert prov.current_model == "evren/glm-5.3-fp8"
    headers = prov._get_headers()
    assert headers["Authorization"] == "Bearer llmtr_key_456"
    assert "X-API-Key" not in headers


def test_firik_payload_formatting():
    prov = FirikProvider(api_key="key", default_model="glm-5.3")
    messages = [
        Message(role="system", content="You are a helpful assistant."),
        Message(role="user", content="Hello Firik!"),
    ]
    tools = [
        {
            "type": "function",
            "function": {"name": "test_func", "parameters": {}},
        }
    ]

    payload = prov._prepare_payload(messages=messages, tools=tools, stream=True)
    assert payload["model"] == "glm-5.3"
    assert len(payload["messages"]) == 2
    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"
    assert payload["stream"] is True


@pytest.mark.asyncio
async def test_firik_known_models(monkeypatch):
    native_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: native_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)), **kwargs))
    prov = FirikProvider(api_key="key", base_url=DEFAULT_FIRIK_DIRECT_URL)
    models = await prov.list_models()
    model_ids = [m.id for m in models]
    assert "glm-5.3" in model_ids
    assert "deepseek-v4-flash" in model_ids
    assert "mimo-v2.6-pro" in model_ids
    assert all(not model.startswith("evren/") for model in model_ids)
    gateway = FirikProvider(base_url=DEFAULT_LLMTR_GATEWAY_URL)
    assert all(model.id.startswith("evren/") for model in await gateway.list_models())


def test_direct_context_defaults_are_scoped_and_do_not_enable_other_capabilities():
    from firik_agent.projects.model_capabilities import model_capabilities
    provider = FirikProvider(default_model="mimo-v2.6-pro")
    caps = model_capabilities(provider.model_metadata[provider.current_model])
    assert caps["context_window"] == 1048576
    assert caps["source"] == "verified_config"
    assert caps["supports_tools"] is None
    assert caps["max_output_tokens"] is None
    assert model_capabilities(provider.model_metadata["glm-5.3"])["context_window"] == 524288
    assert model_capabilities(provider.model_metadata["auto"])["context_window"] == 32768
    assert "unrecognized-model" not in provider.model_metadata
    for endpoint in ("http://localhost:8080/v1", "https://other.invalid/v1",
                     DEFAULT_FIRIK_DIRECT_URL + "/other", DEFAULT_LLMTR_GATEWAY_URL):
        assert not FirikProvider(base_url=endpoint).model_metadata


def test_desktop_uses_context_defaults_without_overriding_live_or_host_evidence():
    from firik_agent.ui.service import FirikService
    from firik_agent.projects.model_capabilities import model_capabilities
    service = FirikService.__new__(FirikService)
    service.provider, service.base_url = "firik", DEFAULT_FIRIK_DIRECT_URL
    service._model_metadata_url = service.base_url
    service._model_metadata, service.config = {}, {}
    provider = FirikProvider(default_model="mimo-v2.6-pro")
    service.configure_model_metadata(provider)
    assert model_capabilities(provider.model_metadata[provider.current_model])["context_window"] == 1048576
    service._model_metadata = {provider.current_model: {"source": "provider", "context_length": 65536}}
    service.configure_model_metadata(provider)
    assert model_capabilities(provider.model_metadata[provider.current_model])["context_window"] == 65536
    service._model_metadata[provider.current_model]["verified"] = False
    service.configure_model_metadata(provider)
    assert model_capabilities(provider.model_metadata[provider.current_model])["context_window"] is None
    service.config = {"providers": {"firik": {"model_capabilities": {
        provider.current_model: {"source": "verified_config", "context_window": 32768}}}}}
    service.configure_model_metadata(provider)
    assert model_capabilities(provider.model_metadata[provider.current_model])["context_window"] == 32768


@pytest.mark.asyncio
async def test_catalogue_replaces_static_capacity_and_preserves_revocation(monkeypatch):
    from firik_agent.projects.model_capabilities import model_capabilities
    row = {"id": "mimo-v2.6-pro", "context_length": 32768}
    native_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: native_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [row]})), **kwargs))
    provider = FirikProvider(default_model=row["id"])
    await provider.list_models()
    assert model_capabilities(provider.model_metadata[row["id"]])["context_window"] == 32768
    row["verified"] = False
    await provider.list_models()
    assert model_capabilities(provider.model_metadata[row["id"]])["context_window"] is None


def test_provider_registry():
    registry = ProviderRegistry()
    prov1 = FirikProvider(name="firik", default_model="glm-5.3")
    prov2 = FirikProvider(name="llmtr", base_url=DEFAULT_LLMTR_GATEWAY_URL)

    registry.register("firik", prov1)
    registry.register("llmtr", prov2)

    assert registry.active_name == "firik"
    assert registry.get_active() == prov1

    registry.set_active("llmtr")
    assert registry.active_name == "llmtr"
    assert registry.get_active() == prov2


@pytest.mark.asyncio
async def test_nested_usage_frames_do_not_restart_model_or_repeat_tools(monkeypatch):
    """MIMO usage tails must not trigger fallback after a complete tool call."""
    usage = {"prompt_tokens": 304, "completion_tokens": 45, "total_tokens": 349,
             "prompt_tokens_details": {"cached_tokens": 0, "created_cache_tokens": 304},
             "completion_tokens_details": {"reasoning_tokens": 45}}
    requests = []
    executions = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["stream"]  # A fallback request would fail this assertion.
        if body["messages"][-1]["role"] == "user":
            frames = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "usage-check",
                        "function": {"name": "calculate", "arguments": '{"expression":"42 * 2"}'}}]},
                        "finish_reason": "tool_calls"}]}]
        else:
            frames = [{"choices": [{"delta": {"content": "84"}, "finish_reason": "stop"}]}]
        frames.append({"choices": [], "usage": usage})
        text = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text)
    native_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: native_client(
        transport=httpx.MockTransport(respond), **kwargs))
    agent = Agent(config={"plugins": {"autoload_builtins": False}, "mcp_servers": {}})
    agent.providers.register("usage-check", FirikProvider(api_key="local-test", base_url="http://local-test/v1"), set_active=True)
    await agent.initialize()
    agent.tools._handlers["calculate"] = lambda expression: (executions.append(expression) or "84")
    try:
        events = [event async for event in agent.run_stream("Hesapla", session=ChatSession())]
        assert len(requests) == 2
        assert executions == ["42 * 2"]
        assert events[-1].type == AgentEventType.DONE
        assert events[-1].content == "84"
        assert sum(event.type == AgentEventType.TOOL_CALL_STARTED for event in events) == 1
    finally:
        await agent.close()
