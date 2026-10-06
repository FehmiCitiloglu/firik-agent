"""Real runtime concurrency, targeted cancellation and completion-only writes."""
import asyncio
from concurrent.futures import CancelledError
from types import SimpleNamespace
import threading

import pytest

from evren_agent.core.events import AgentEvent, AgentEventType
from evren_agent.core.tools import ToolRegistry
from evren_agent.core.types import Message, ToolCall, ToolCallFunction, ToolDefinition
from evren_agent.ui.service import EvrenService


class GatedAgent:
    def __init__(self):
        self.tools = ToolRegistry()
        self.started = threading.Event()
        self.release = threading.Event()
        self.model = None
        provider = SimpleNamespace(name="test", base_url="https://test.invalid/v1",
                                   model_metadata={}, set_model=self.set_model)
        self.providers = SimpleNamespace(get_active=lambda: provider)

    def set_model(self, model):
        self.model = model

    async def initialize(self):
        self.tools.register(ToolDefinition(name="where", description="cwd", parameters={}, source="builtin"),
                            lambda: str(self.default_cwd))

    async def run_stream(self, prompt, session, session_tool_filter, cancel_event):
        session.messages.append(Message(role="user", content=prompt))
        self.started.set()
        yield AgentEvent(AgentEventType.TEXT_DELTA, session.session_id, content="Başladı")
        while not self.release.is_set():
            await asyncio.sleep(0.005)
        result = await session_tool_filter.execute(ToolCall(id="where", function=ToolCallFunction(name="where", arguments="{}")), session)
        content = f"{prompt}:{self.model}:{result.content}"
        session.messages.append(Message(role="assistant", content=content))
        yield AgentEvent(AgentEventType.DONE, session.session_id, content=content)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    value = EvrenService()
    try:
        yield value
    finally:
        value.shutdown()
        value.projects.scheduler.stop()


def test_sessions_stream_concurrently_with_isolated_models_cwd_and_tools(service, monkeypatch):
    a, b = service.get_or_create_session(), service.get_or_create_session()
    a.model, b.model = "model-a", "model-b"
    agents = {a.session_id: GatedAgent(), b.session_id: GatedAgent()}
    monkeypatch.setattr(service, "get_agent", lambda sid: agents[sid])
    saved = []
    original = service.chat_history.save_context
    monkeypatch.setattr(service.chat_history, "save_context", lambda s: (saved.append(s.session_id), original(s)))
    for session in (a, b):
        service.save_chat_transcript(session, [{"role": "user", "content": session.model}])
    first = service.chat_agent_stream_async(a.session_id, "A", cwd="project-a")
    second = service.chat_agent_stream_async(b.session_id, "B", cwd="project-b")
    assert agents[a.session_id].started.wait(3)
    assert agents[b.session_id].started.wait(3)
    assert not saved
    agents[b.session_id].release.set()
    second.result(timeout=3)
    assert not first.done()
    assert b.messages[-1].content == "B:model-b:project-b"
    assert saved == [b.session_id]
    agents[a.session_id].release.set()
    first.result(timeout=3)
    assert a.messages[-1].content == "A:model-a:project-a"
    assert saved == [b.session_id, a.session_id]
    assert not service._streams


def test_cancelling_one_session_keeps_the_other_running(service, monkeypatch):
    a, b = service.get_or_create_session(), service.get_or_create_session()
    agents = {a.session_id: GatedAgent(), b.session_id: GatedAgent()}
    monkeypatch.setattr(service, "get_agent", lambda sid: agents[sid])
    first = service.chat_agent_stream_async(a.session_id, "A")
    second = service.chat_agent_stream_async(b.session_id, "B")
    assert agents[a.session_id].started.wait(3)
    assert agents[b.session_id].started.wait(3)
    with pytest.raises(ValueError, match="zaten"):
        service.chat_agent_stream_async(b.session_id, "Tekrar")
    service.cancel_active_stream(a.session_id)
    with pytest.raises(CancelledError):
        first.result(timeout=3)
    assert not second.done()
    assert b.session_id in service._streams
    agents[b.session_id].release.set()
    second.result(timeout=3)


def test_done_callback_can_send_next_question_without_old_cleanup_clearing_it(service, monkeypatch):
    session = service.get_or_create_session()
    agent = GatedAgent()
    agent.release.set()
    monkeypatch.setattr(service, "get_agent", lambda sid: agent)
    service.save_chat_transcript(session, [{"role": "user", "content": "İlk soru"}])
    following = []
    def on_event(event):
        if event.type == AgentEventType.DONE:
            agent.release.clear()
            following.append(service.chat_agent_stream_async(session.session_id, "İkinci soru"))
    first = service.chat_agent_stream_async(session.session_id, "İlk soru", on_event=on_event)
    first.result(timeout=3)
    assert len(following) == 1
    assert not following[0].done()
    assert session.session_id in service._streams
    assert service._stream_futures[session.session_id] is following[0]
    agent.release.set()
    following[0].result(timeout=3)
    assert session.messages[-1].content.startswith("İkinci soru:")


def test_desktop_agent_instances_do_not_share_bound_builtin_handlers(service):
    a, b = service.get_agent("a"), service.get_agent("b")
    assert a is not b
    assert a.tools is not b.tools
    assert a.providers.get_active() is not b.providers.get_active()
    assert a.config["mcp_servers"] == b.config["mcp_servers"] == {}
    assert service.get_agent("a") is a


def test_default_computer_use_is_selected_and_removal_is_persisted(service):
    session = service.get_or_create_session()
    assert "computer-use" in session.active_mcp_servers
    conn = service.mcp_manager.get_connection("computer-use")
    assert conn.config.command == "builtin:computer-use"
    finished = threading.Event()
    errors = []
    service.remove_mcp_server_async("computer-use", on_success=finished.set,
                                    on_error=lambda error: (errors.append(error), finished.set()))
    assert finished.wait(3)
    assert not errors
    assert "computer-use" not in session.active_mcp_servers
    from evren_agent.config import load_config
    from evren_agent.mcp.defaults import add_desktop_defaults
    saved = load_config(service.config_path)
    add_desktop_defaults(saved)
    assert "computer-use" not in saved["mcp_servers"]


def test_computer_use_emergency_stop_survives_connect_and_explicit_restart_resumes(service):
    service.stop_computer_use()
    service.runtime.run_sync(service.mcp_manager.connect_server("computer-use"), timeout=3)
    conn = service.mcp_manager.get_connection("computer-use")
    assert conn.client._builtin_server.service.security.is_stopped()
    finished = threading.Event()
    errors = []
    service.restart_mcp_async("computer-use", on_success=finished.set,
                              on_error=lambda error: (errors.append(error), finished.set()))
    assert finished.wait(3)
    assert not errors
    assert not conn.client._builtin_server.service.security.is_stopped()
