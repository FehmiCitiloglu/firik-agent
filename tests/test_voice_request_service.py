"""Voice uses the real service/runtime/history and session tool gate."""
from concurrent.futures import CancelledError
import json
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.session import SessionToolFilter
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, ToolCall, ToolCallFunction, ToolDefinition
from firik_agent.voice.execution import VoiceToolFilter
from firik_agent.voice.intent_router import VoiceRequestRouter
from firik_agent.voice.requests import VoiceScope
from test_chat_streams import service


def voice(session_id, text="Run permitted workspace tests", *, confirmed=True):
    router = VoiceRequestRouter(lambda request: True)
    request = router.create_request(text, VoiceScope(session_id))
    return router.confirm(request) if confirmed else request


def call(tool_name, **arguments):
    return ToolCall(id="call_" + tool_name, function=ToolCallFunction(name=tool_name, arguments=json.dumps(arguments)))


class UIQueue:
    def __init__(self):
        self.queue = queue.Queue()
        self.thread = threading.get_ident()
    def __call__(self, callback, *args):
        self.queue.put((callback, args))
    def drain(self):
        assert threading.get_ident() == self.thread
        while not self.queue.empty():
            callback, arguments = self.queue.get_nowait()
            callback(*arguments)
    def until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.drain()
            time.sleep(.005)
        self.drain()
        assert predicate()


class ExistingAgent:
    def __init__(self, script):
        self.script, self.tools = script, ToolRegistry()
        self.calls, self.results = [], []
        self.received_session = self.received_filter = None
        self.model = None
        provider = SimpleNamespace(name="test", base_url="https://test.invalid/v1", model_metadata={},
                                   set_model=lambda model: setattr(self, "model", model))
        self.providers = SimpleNamespace(get_active=lambda: provider)
        for name, source in (("read_file", "meta:agent"), ("activate_skill", "meta:agent"),
                             ("plugin_operation", "plugin:existing"), ("write_file", "meta:agent")):
            def handler(name=name, **arguments):
                self.calls.append((name, arguments))
                return "existing handler completed"
            self.tools.register(ToolDefinition(name=name, description="registered existing tool", source=source), handler)
    async def initialize(self):
        pass
    async def run_stream(self, prompt, session, session_tool_filter, cancel_event):
        self.received_session, self.received_filter = session, session_tool_filter
        self.schemas = session_tool_filter.get_schemas(session)
        session.messages.append(Message(role="user", content=prompt))
        yield AgentEvent(AgentEventType.MODEL_REQUEST_STARTED, session.session_id)
        for tool_call in self.script:
            yield AgentEvent(AgentEventType.TOOL_CALL_STARTED, session.session_id, tool_name=tool_call.function.name,
                             arguments=json.loads(tool_call.function.arguments))
            result = await session_tool_filter.execute(tool_call, session)
            self.results.append(result)
            yield AgentEvent(AgentEventType.TOOL_CALL_RESULT, session.session_id,
                             tool_name=tool_call.function.name, content=result.content)
        session.messages.append(Message(role="assistant", content="Existing agent response"))
        yield AgentEvent(AgentEventType.DONE, session.session_id, content="Existing agent response")


def wire(service, monkeypatch, script):
    ui = UIQueue()
    service._dispatch = ui
    session = service.get_or_create_session()
    session.model = "existing-selected-model"
    session.active_mcp_servers = set()
    agent = ExistingAgent(script)
    monkeypatch.setattr(service, "get_agent", lambda session_id: agent)
    return ui, session, agent


def test_voice_stalls_for_exact_ui_then_uses_same_session_mcp_plugins_and_skills(service, monkeypatch):
    script = [call("activate_skill", name="existing-skill"), call("plugin_operation", value=3),
              call("chosen_tool", path="existing.txt"), call("denied_tool", value="must not run")]
    ui, session, agent = wire(service, monkeypatch, script)
    session.active_mcp_servers = {"chosen"}
    acquired, mcp_calls, proposed, events = [], [], [], []
    async def acquire(session_id, server):
        acquired.append((session_id, server))
        return True
    monkeypatch.setattr(service.mcp_manager, "acquire_for_session", acquire)
    for name, source in (("chosen_tool", "mcp:chosen"), ("denied_tool", "mcp:not-selected")):
        service.mcp_manager.tool_registry.register(ToolDefinition(name=name, description="existing MCP", source=source),
            lambda **arguments: mcp_calls.append(arguments) or "MCP result")
    request = voice(session.session_id)
    def on_approval(payload, decide):
        assert threading.get_ident() == ui.thread
        proposed.append(payload)
        assert payload["origin"] == "voice" and payload["session_id"] == session.session_id
        decide(True)
    future = service.chat_agent_stream_async(session.session_id, request.request_text,
        voice_request=request, on_approval=on_approval, on_event=events.append)
    ui.until(future.done)
    future.result(timeout=1)
    assert agent.received_session is session
    assert agent.model == "existing-selected-model"
    assert isinstance(agent.received_filter, VoiceToolFilter)
    assert isinstance(agent.received_filter.delegate, SessionToolFilter)
    assert [item["tool_name"] for item in proposed] == ["activate_skill", "plugin_operation", "chosen_tool"]
    assert proposed[0]["arguments"] == {"name": "existing-skill"}
    assert acquired == [(session.session_id, "chosen")]
    assert mcp_calls == [{"path": "existing.txt"}]
    assert agent.results[-1].is_error
    assert {item["function"]["name"] for item in agent.schemas}.issuperset({"activate_skill", "plugin_operation", "chosen_tool"})
    assert "denied_tool" not in {item["function"]["name"] for item in agent.schemas}
    assert len(service.sessions) == 1
    assert session.messages[0].execution["utterance_id"] == request.utterance_id
    assert "origin" not in session.messages[0].to_dict()
    assert "execution" not in session.messages[0].to_dict()
    assert len([message for message in session.messages if message.role == "user"]) == 1
    assert any(event.type == AgentEventType.APPROVAL_REQUIRED for event in events)
    request_events = [event for event in events if event.type not in {
        AgentEventType.MCP_CONNECTING, AgentEventType.MCP_CONNECTED, AgentEventType.MCP_ERROR}]
    assert all(event.metadata["origin"] == "voice" for event in request_events)


def test_pending_exact_tool_approval_cannot_execute_until_ui_button_decides(service, monkeypatch):
    ui, session, agent = wire(service, monkeypatch, [call("write_file", path="exact.py", content="shown")])
    proposed = []
    request = voice(session.session_id, "Create exact.py")
    future = service.chat_agent_stream_async(session.session_id, request.request_text,
        voice_request=request, on_approval=lambda payload, decide: proposed.append((payload, decide)))
    ui.until(lambda: bool(proposed))
    assert proposed[0][0]["tool_name"] == "write_file"
    assert proposed[0][0]["arguments"] == {"path": "exact.py", "content": "shown"}
    assert not future.done() and not agent.calls
    proposed[0][1](True)
    ui.until(future.done)
    future.result(timeout=1)
    assert agent.calls == [("write_file", {"path": "exact.py", "content": "shown"})]


def test_service_cancel_while_exact_approval_pending_never_dispatches(service, monkeypatch):
    ui, session, agent = wire(service, monkeypatch, [call("write_file", path="never.py", content="no")])
    proposed = []
    request = voice(session.session_id)
    future = service.chat_agent_stream_async(session.session_id, request.request_text,
        voice_request=request, on_approval=lambda payload, decide: proposed.append((payload, decide)))
    ui.until(lambda: bool(proposed))
    service.cancel_active_stream(session.session_id)
    with pytest.raises(CancelledError):
        future.result(timeout=1)
    proposed[0][1](True)
    ui.drain()
    assert not agent.calls and request.cancellation_token.is_set()


def test_no_ui_callback_fails_closed_and_typed_execution_remains_same(service, monkeypatch):
    ui, session, agent = wire(service, monkeypatch, [call("write_file", path="same.py", content="same")])
    request = voice(session.session_id)
    first = service.chat_agent_stream_async(session.session_id, request.request_text, voice_request=request)
    ui.until(first.done)
    first.result(timeout=1)
    assert agent.results[-1].is_error and not agent.calls
    second = service.chat_agent_stream_async(session.session_id, "Typed request")
    ui.until(second.done)
    second.result(timeout=1)
    assert agent.calls == [("write_file", {"path": "same.py", "content": "same"})]
    assert isinstance(agent.received_filter, SessionToolFilter)


def test_restoration_uses_existing_history_and_unknown_voice_scope_cannot_create_chat(service):
    session = service.get_or_create_session()
    service.save_chat_transcript(session, [{"role": "user", "content": "Existing persisted conversation"}])
    known = voice(session.session_id)
    service.sessions.clear()
    with pytest.raises(ValueError):
        service.validate_voice_request(known)
    restored = service.restore_chat_session(session.session_id)
    assert restored.session_id == known.session_id
    service.validate_voice_request(known)
    before = set(service.sessions)
    unknown = voice("not-a-real-chat")
    with pytest.raises(ValueError):
        service.chat_agent_stream_async(unknown.session_id, unknown.request_text, voice_request=unknown)
    assert set(service.sessions) == before
    with pytest.raises(KeyError):
        service.restore_chat_session("not-a-real-chat")


def test_direct_service_rejects_unreviewed_action_request_before_agent_execution(service, monkeypatch):
    ui, session, agent = wire(service, monkeypatch, [])
    pending = voice(session.session_id, "Delete exact.py", confirmed=False)
    with pytest.raises(ValueError):
        service.chat_agent_stream_async(session.session_id, pending.request_text, voice_request=pending)
    assert not service._streams and agent.received_session is None


@pytest.mark.parametrize("unknown_session", [False, True])
def test_coding_voice_bridge_cannot_invent_project_or_session_ownership(service, tmp_path, unknown_session):
    existing = service.get_or_create_session()
    sid = "not-an-owned-session" if unknown_session else existing.session_id
    router = VoiceRequestRouter(lambda request: True)
    issued = router.create_request("Run permitted tests", VoiceScope(sid, project_id="not-an-owned-project", target="coding"))
    request = router.confirm(issued)
    before = set(service.sessions)
    with pytest.raises(ValueError):
        service.chat_agent_stream_async(sid, request.request_text, cwd=str(tmp_path),
            coding_options={"project_id": request.project_id, "voice_request": request})
    assert set(service.sessions) == before and not service._streams


@pytest.mark.asyncio
async def test_approval_helper_uses_literal_ui_decision_and_cancellation(service):
    ui = UIQueue()
    service._dispatch = ui
    pending = []
    cancel = threading.Event()
    import asyncio
    task = asyncio.create_task(service._voice_tool_approval({"tool_name": "write_file"},
        lambda payload, decide: pending.append(decide), cancel))
    await asyncio.sleep(0)
    ui.drain()
    assert pending and not task.done()
    pending[0]("yes")
    assert await task is False
    task = asyncio.create_task(service._voice_tool_approval({}, lambda payload, decide: pending.append(decide), cancel))
    await asyncio.sleep(0)
    ui.drain()
    cancel.set()
    assert await task is False
