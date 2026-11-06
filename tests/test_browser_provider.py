"""Offline browser image/tool protocol and uncertain-effect regressions."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from firik_agent.core.failures import FailureKind, RequestPhase, RuntimeFailure
from firik_agent.core.recovery import (AdaptiveRecoveryEngine, RecoveryAction, RecoveryBudget,
    RecoveryContext, SideEffectRisk, tool_semantics)
from firik_agent.core.types import ContentPart, Message, ToolCall, ToolCallFunction, ToolDefinition, ToolResult
from firik_agent.core.tools import ToolRegistry
from firik_agent.providers.firik import FirikProvider
from firik_agent.providers.openai_provider import OpenAIProvider


PROVIDERS = [FirikProvider, OpenAIProvider]
IMAGE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


@pytest.mark.parametrize("metadata,disabled", [
    ({}, False),
    ({"supports_tools": False}, False),
    ({"source": "provider", "supports_tools": False}, True),
    ({"source": "verified_config", "supports_tools": True}, False),
    ({"source": "verified_config", "supports_tools": False, "verified": False}, False),
])
def test_no_tools_guard_preserves_unknown_capability(metadata, disabled):
    from firik_agent.core.agent import _tools_disabled
    provider = SimpleNamespace(current_model="model", model_metadata={"model": metadata})
    assert _tools_disabled(provider) is disabled


def browser_result(*, delivery=True):
    return ToolResult(tool_call_id="capture", name="browser_screenshot", content="Screenshot artifact ready.",
        parts=[ContentPart(type="image", data=IMAGE, mime_type="image/png")],
        metadata={"browser_model_image": delivery, "artifact_id": "private-artifact", "browser_session_id": "owned"})


def authorize_vision(provider):
    provider.model_metadata[provider.current_model] = {"source": "verified_config", "supports_vision": True}
    provider.browser_image_transport_verified = True


@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("missing", ["vision", "transport", "permission", "unverified"])
def test_browser_images_require_all_independent_host_checks(provider_type, missing):
    # Names previously classified as vision-capable cannot authorize delivery.
    provider = provider_type(default_model="gpt-4o-vision-vl", api_key="unused")
    authorize_vision(provider)
    if missing == "vision":
        provider.model_metadata[provider.current_model]["supports_vision"] = False
    elif missing == "transport":
        provider.browser_image_transport_verified = False
    elif missing == "unverified":
        provider.model_metadata[provider.current_model] = {"supports_vision": True}
    payload = provider._prepare_payload([browser_result(delivery=missing != "permission").to_message()])
    assert len(payload["messages"]) == 1
    assert payload["messages"][0] == {"role": "tool", "name": "browser_screenshot",
        "tool_call_id": "capture", "content": "Screenshot artifact ready."}
    assert IMAGE not in json.dumps(payload)


@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_outbound_browser_image_is_user_input_after_complete_tool_batch(monkeypatch, provider_type, stream):
    provider = provider_type(default_model="explicit-model", api_key="unused", base_url="https://offline.invalid/v1")
    authorize_vision(provider)
    captured = []
    def respond(request):
        captured.append(json.loads(request.content))
        if stream:
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Observed"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": "Observed"}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    calls = [ToolCall(id="capture", function=ToolCallFunction(name="browser_screenshot", arguments="{}")),
             ToolCall(id="inspect", function=ToolCallFunction(name="browser_snapshot", arguments="{}"))]
    messages = [Message(role="user", content="Inspect the page"), Message(role="assistant", tool_calls=calls),
                browser_result().to_message(),
                ToolResult(tool_call_id="inspect", name="browser_snapshot", content="button Open").to_message()]
    if stream:
        _ = [chunk async for chunk in provider.chat_stream(messages)]
    else:
        assert (await provider.chat(messages)).content == "Observed"
    payload = captured[0]
    assert [row["role"] for row in payload["messages"]] == ["user", "assistant", "tool", "tool", "user"]
    assert isinstance(payload["messages"][2]["content"], str)
    assert payload["messages"][4]["content"][1] == {
        "type": "image_url", "image_url": {"url": "data:image/png;base64," + IMAGE}}
    assert "private-artifact" not in json.dumps(payload)
    assert "browser_model_image" not in json.dumps(payload)
    assert provider.current_model == "explicit-model"


@pytest.mark.parametrize("provider_type", PROVIDERS)
def test_browser_image_observation_precedes_next_assistant_message(provider_type):
    provider = provider_type(default_model="explicit-model")
    authorize_vision(provider)
    rows = provider._prepare_payload([browser_result().to_message(),
        Message(role="assistant", content="Finished")])["messages"]
    assert [row["role"] for row in rows] == ["tool", "user", "assistant"]


@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.asyncio
async def test_browser_stream_partial_arguments_never_become_callable(monkeypatch, provider_type):
    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"click","function":{"name":"browser_click","arguments":"{"}}]}}]}\n\n'
            raise httpx.ReadTimeout("private page detail")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Interrupted())), **kw))
    emitted = []
    with pytest.raises(RuntimeFailure) as error:
        async for chunk in provider_type(api_key="unused").chat_stream([Message(role="user", content="Click")]):
            emitted.append(chunk)
    assert error.value.kind == FailureKind.STREAM_INTERRUPTED
    assert error.value.phase == RequestPhase.PARTIAL_RESPONSE
    assert not any(chunk.tool_calls for chunk in emitted)
    assert "private page detail" not in str(error.value)


@pytest.mark.parametrize("name", ["browser_status", "browser_snapshot", "browser_verify", "browser_screenshot", "browser_get_console",
    "browser_get_page_errors", "browser_get_network_errors", "browser_extract_text", "browser_list_tabs"])
def test_explicit_browser_read_operations_allow_bounded_safe_retry(name):
    definition = ToolDefinition(name=name, description="", source="native:browser")
    risk = tool_semantics(name, definition)
    assert risk == SideEffectRisk.STATE_INSPECTION
    context = RecoveryContext("run", "attempt", FailureKind.TOOL_TIMEOUT, RequestPhase.TOOL_DISPATCHED,
        risk, {}, RecoveryBudget(), operation="tool")
    assert AdaptiveRecoveryEngine(jitter=lambda: 0).decide(context).safe_to_repeat


@pytest.mark.parametrize("name", ["browser_navigate", "browser_back", "browser_reload", "browser_click", "browser_fill",
    "browser_press", "browser_upload", "browser_download", "browser_undocumented"])
def test_browser_mutations_and_unknown_tools_require_reconciliation(name):
    risk = tool_semantics(name, ToolDefinition(name=name, description="", source="native:browser"))
    assert risk not in {SideEffectRisk.PURE_READ, SideEffectRisk.STATE_INSPECTION}
    context = RecoveryContext("run", "attempt", FailureKind.TOOL_TIMEOUT, RequestPhase.TOOL_DISPATCHED,
        risk, {}, RecoveryBudget(), operation="tool")
    decision = AdaptiveRecoveryEngine().decide(context)
    assert decision.action == RecoveryAction.RECONCILE
    assert not decision.safe_to_repeat


@pytest.mark.parametrize("source", ["mcp:browser", "plugin:browser", "skill:browser", "", "untrusted"])
def test_foreign_browser_name_does_not_prove_read_safety(source):
    assert tool_semantics("browser_snapshot", ToolDefinition(name="browser_snapshot", description="", source=source)) == SideEffectRisk.UNKNOWN


def test_cancelled_browser_mutation_is_never_repeated():
    context = RecoveryContext("run", "attempt", FailureKind.CANCELLED, RequestPhase.TOOL_DISPATCHED,
        SideEffectRisk.EXTERNAL, {}, RecoveryBudget(), operation="tool", cancellation_status=True)
    decision = AdaptiveRecoveryEngine().decide(context)
    assert decision.action == RecoveryAction.STOP
    assert not decision.safe_to_repeat


@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("name,arguments,dispatched", [
    ("browser_snapshot", "{}", True),
    ("browser_snapshot", "{broken", False),
    ("browser_unknown", "{}", False),
])
@pytest.mark.asyncio
async def test_mocked_provider_tool_call_result_continuation(monkeypatch, provider_type, name, arguments, dispatched):
    registry = ToolRegistry()
    observed = []
    async def snapshot():
        observed.append(True)
        return {"untrusted": True, "semantic_content": "button Open"}
    registry.register(ToolDefinition(name="browser_snapshot", description="Inspect a fixture",
        source="native:browser", execution_boundary="host"), snapshot)
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"id": "read",
                "function": {"name": name, "arguments": arguments}}]}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Result received"}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    provider = provider_type(default_model="fixture", api_key="unused", base_url="https://offline.invalid/v1")
    messages = [Message(role="user", content="Inspect the browser")]
    assistant = await provider.chat(messages, tools=registry.get_schemas())
    assert len(assistant.tool_calls) == 1
    messages.append(assistant)
    result = await registry.execute(assistant.tool_calls[0])
    assert result.is_error is not dispatched
    assert bool(observed) is dispatched
    messages.append(result.to_message())
    assert (await provider.chat(messages, tools=registry.get_schemas())).content == "Result received"
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert requests[1]["messages"][-1]["tool_call_id"] == "read"
    assert "execution_boundary" not in requests[1]["messages"][-1]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_existing_native_loop_uses_browser_session_gate_and_continuation(streaming, enabled):
    from firik_agent.browser.models import BrowserGrant, BrowserIdentity
    from firik_agent.browser.tool_adapter import BrowserToolAdapter
    from firik_agent.core.agent import Agent
    from firik_agent.core.events import AgentEventType
    from firik_agent.core.session import ChatSession, SessionToolFilter
    from firik_agent.core.types import StreamChunk

    dispatched = []
    class Manager:
        config = SimpleNamespace(enabled=True)
        async def dispatch(self, identity, grant, operation, arguments, **kwargs):
            dispatched.append((identity, operation))
            return {"untrusted": True, "revision": 1, "semantic_content": "button Open"}
    class Provider:
        name, current_model = "fixture", "offline-browser"
        model_metadata = {"offline-browser": {"source": "verified_config", "supports_tools": True}}
        def __init__(self):
            self.calls = []
        def next(self, kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                schemas = {row["function"]["name"] for row in (kwargs.get("tools") or [])}
                assert ("browser_snapshot" in schemas) is enabled
                return Message(role="assistant", tool_calls=[ToolCall(id="observe", function=ToolCallFunction(
                    name="browser_snapshot", arguments="{}"))])
            results = [message for message in kwargs["messages"] if message.role == "tool"]
            assert len(results) == 1 and results[0].tool_call_id == "observe"
            assert ("button Open" in results[0].content) is enabled
            return Message(role="assistant", content="Observation received")
        async def chat(self, **kwargs):
            return self.next(kwargs)
        async def chat_stream(self, **kwargs):
            result = self.next(kwargs)
            yield StreamChunk(delta_content=result.content, tool_calls=result.tool_calls)

    agent = Agent(config={"agent": {"context_compaction": {"enabled": False}}, "providers": {},
                         "plugins": {}, "skills": {}, "self_improvement": {"mode": "off"}})
    agent._is_initialized = True
    provider = Provider()
    agent.providers.register("fixture", provider, set_active=True)
    session = ChatSession(model=provider.current_model, learning_enabled=False)
    session.browser_enabled = enabled
    identity = BrowserIdentity(session.session_id)
    adapter = BrowserToolAdapter(Manager(), identity, lambda: BrowserGrant(enabled=True))
    gate = SessionToolFilter(agent.tools, browser_adapter=adapter)
    try:
        if streaming:
            events = [event async for event in agent.run_stream("Inspect page", session=session, session_tool_filter=gate)]
            assert events[-1].type == AgentEventType.DONE
            assert events[-1].content == "Observation received"
        else:
            assert await agent.run("Inspect page", session=session, session_tool_filter=gate) == "Observation received"
        assert bool(dispatched) is enabled
        if enabled:
            assert dispatched[0] == (identity, "browser_snapshot")
        assert len(provider.calls) == 2
    finally:
        await agent.close()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_normal_chat_no_tools_model_cannot_dispatch_browser_call(streaming):
    from firik_agent.core.agent import Agent
    from firik_agent.core.events import AgentEventType
    from firik_agent.core.session import ChatSession
    from firik_agent.core.types import StreamChunk

    dispatched = []
    class Provider:
        name, current_model = "fixture", "explicit-no-tools"
        model_metadata = {"explicit-no-tools": {"source": "verified_config", "supports_tools": False}}
        async def chat(self, **kwargs):
            assert not kwargs.get("tools")
            return Message(role="assistant", tool_calls=[ToolCall(id="forbidden", function=ToolCallFunction(
                name="browser_snapshot", arguments="{}"))])
        async def chat_stream(self, **kwargs):
            result = await self.chat(**kwargs)
            yield StreamChunk(tool_calls=result.tool_calls)
    agent = Agent(config={"agent": {"context_compaction": {"enabled": False}}, "providers": {},
                         "plugins": {}, "skills": {}, "self_improvement": {"mode": "off"}})
    agent._is_initialized = True
    agent.providers.register("fixture", Provider(), set_active=True)
    agent.tools.register(ToolDefinition(name="browser_snapshot", description="fixture", source="native:browser"),
        lambda: dispatched.append(True))
    session = ChatSession(model="explicit-no-tools", learning_enabled=False)
    try:
        if streaming:
            events = [event async for event in agent.run_stream("Inspect page", session=session)]
            assert events[-1].type == AgentEventType.ERROR
            assert events[-1].metadata["failure_kind"] == "unsupported_capability"
        else:
            with pytest.raises(RuntimeFailure) as error:
                await agent.run("Inspect page", session=session)
            assert error.value.kind == FailureKind.UNSUPPORTED_CAPABILITY
        assert dispatched == []
        assert not any(message.role == "tool" for message in session.messages)
    finally:
        await agent.close()
