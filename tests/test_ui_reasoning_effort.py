"""Native reasoning controls run with isolated Tcl/Tk state on Windows."""
from test_reasoning_effort import metadata
from test_ui_model_selection import service_factory
from test_ui_coding_workspace import workspace


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

