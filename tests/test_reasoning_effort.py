"""Model capability gating, request transport, UI and persistence regressions."""
import json
import httpx
import pytest

from test_ui_model_selection import service_factory
from test_ui_coding_workspace import workspace
from evren_agent.core.session import ChatSession
from evren_agent.core.types import Message
from evren_agent.providers.evren import EvrenProvider
from evren_agent.providers.openai_provider import OpenAIProvider
from evren_agent.providers.reasoning import effort_levels, effort_payload


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


@pytest.mark.parametrize("provider_class", [EvrenProvider, OpenAIProvider])
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
    from evren_agent.core.chat_history import ChatHistoryStore
    store = ChatHistoryStore(tmp_path / "history.db")
    session = ChatSession(model="supported", reasoning_effort="none")
    session.messages = [Message(role="user", content="hello")]
    store.save_transcript(session, [{"role": "user", "content": "hello"}])
    record = store.load(session.session_id)
    assert store.restore_session(record).reasoning_effort == "none"


def test_chat_ui_model_switch_and_reload(service_factory, monkeypatch):
    import customtkinter as ctk
    from evren_agent.ui.app import _ensure_tk_environment
    from evren_agent.ui.views.chat_workspace import ChatWorkspace
    from test_ui_chat import pump
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("900x700")
    service = service_factory()
    service._model_metadata = {"glm-5.3": metadata(["low", "high"]),
                               "mimo": metadata(["none", "minimal", "xhigh"]),
                               "gemma": metadata(supported=False)}
    monkeypatch.setattr(service, "fetch_models_async", lambda on_success: on_success(list(service._model_metadata)))
    view = ChatWorkspace(root, service)
    view.pack(fill="both", expand=True)
    try:
        pump(root)
        chat = view.current
        selector = chat.effort_selector
        assert selector.combo.cget("state") == "readonly"
        selector.combo.cget("command")("Yüksek (high)")
        assert chat.session.reasoning_effort == "high"
        chat._on_model_selected("mimo")
        assert chat.session.reasoning_effort is None
        assert selector.levels == ["none", "minimal", "xhigh"]
        selector.combo.cget("command")("Kapalı (none)")
        assert chat.session.reasoning_effort == "none"
        chat._on_model_selected("gemma")
        assert selector.combo.cget("state") == "disabled"
        chat._on_model_selected("mimo")
        assert selector.combo.get() == "Kapalı (none)"
        chat.is_streaming = True
        chat._refresh_effort()
        assert selector.combo.cget("state") == "disabled"
        for width in (700, 900, 1200):
            root.geometry(f"{width}x700")
            pump(root)
            assert selector.winfo_x() + selector.winfo_width() <= selector.master.winfo_width()
    finally:
        root.destroy()


def test_coding_effort_persists_and_disables_during_run(workspace):
    app, view, project, streams = workspace
    service = view.service
    service._model_metadata = {view._coding_state()["session"].model: metadata(["low", "high"])}
    view._refresh_coding_effort()
    selector = view.coding_effort_selector
    assert selector.combo.cget("state") == "readonly"
    selector.combo.cget("command")("Yüksek (high)")
    assert view._coding_state()["session"].reasoning_effort == "high"
    assert view.project_service.db.get_settings(project.id).coding_preferences["reasoning_effort"] == "high"
    view._save_coding_preferences({"another_preference": True})
    assert view.project_service.db.get_settings(project.id).coding_preferences["reasoning_effort"] == "high"
    view._coding_state()["busy"] = True
    view._refresh_coding_display()
    assert selector.combo.cget("state") == "disabled"

@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_agent_uses_session_effort_and_resets_next_request(monkeypatch, stream):
    from evren_agent.core.agent import Agent
    agent = Agent(config={"providers": {}, "mcp_servers": {},
                          "plugins": {"autoload_builtins": False}, "skills": {"active_skills": []}})
    agent._is_initialized = True
    provider = EvrenProvider(default_model="supported")
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
    provider = EvrenProvider(default_model="supported", base_url=service.base_url)
    service.configure_model_metadata(provider)
    provider.reasoning_effort = "high"
    assert provider.reasoning_options() == {"reasoning_effort": "high"}
    service.base_url = "https://other.invalid/v1"
    assert service.model_effort_levels("supported") == []
    provider = EvrenProvider(default_model="supported", base_url=service.base_url)
    service.configure_model_metadata(provider)
    provider.reasoning_effort = "high"
    assert provider.reasoning_options() == {}
