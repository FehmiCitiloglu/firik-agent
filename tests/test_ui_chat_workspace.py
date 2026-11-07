"""Conversation navigation must never interrupt another conversation's stream."""
import customtkinter as ctk
import pytest

from firik_agent.core.events import AgentEventType
from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.views.chat_workspace import ChatWorkspace
from test_ui_chat import ChatServiceStub, emit, pump, send


@pytest.fixture
def workspace(tmp_path):
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("1120x760")
    service = ChatServiceStub(tmp_path / "chats.db")
    view = ChatWorkspace(root, service)
    view.pack(fill="both", expand=True)
    pump(root)
    try:
        yield root, view, service
    finally:
        root.destroy()


def test_two_streams_keep_messages_drafts_and_cancellation_separate(workspace):
    root, view, service = workspace
    first = view.current
    a = send(first, service, "İlk soru")
    emit(root, a, AgentEventType.TEXT_DELTA, content="İlk cevap ")
    first.input_textbox.insert("1.0", "Birinci taslak")
    view.new_chat()
    second = view.current
    b = send(second, service, "İkinci soru")
    assert not service.cancelled
    assert first.is_streaming and second.is_streaming
    emit(root, a, AgentEventType.TEXT_DELTA, content="arka planda sürüyor.")
    emit(root, b, AgentEventType.TEXT_DELTA, content="İkinci cevap")
    assert second.current_bot_bubble.raw_content == "İkinci cevap"
    assert first.current_bot_bubble.raw_content == "İlk cevap arka planda sürüyor."
    second._on_send_pressed()
    assert service.cancelled_sessions == [second.session.session_id]
    assert first.is_streaming and not second.is_streaming
    view.open_chat(first.session.session_id)
    assert view.current is first
    assert first.input_textbox.get("1.0", "end-1c") == "Birinci taslak"
    emit(root, a, AgentEventType.DONE, content="İlk cevap arka planda sürüyor.")
    assert not first.is_streaming
    records = service.load_chat_transcript(first.session.session_id)
    assert records[-1]["content"] == "İlk cevap arka planda sürüyor."


def test_deltas_do_not_write_history_and_hidden_completion_is_marked(workspace):
    root, view, service = workspace
    first = view.current
    stream = send(first, service, "Yanıtı beklemeden geç")
    writes = service.writes
    view.new_chat()
    for text in ("Bir ", "iki ", "üç."):
        emit(root, stream, AgentEventType.TEXT_DELTA, content=text)
    pump(root, 0.8)
    assert service.writes == writes
    assert first.history_status.cget("text") == ""
    emit(root, stream, AgentEventType.DONE, content="Bir iki üç.")
    assert service.writes == writes + 1
    assert first.session.session_id in view._unread
    assert any("Yeni yanıt" in child.cget("text") for child in view.history_list.winfo_children())
    assert view.current is not first
    view.open_chat(first.session.session_id)
    assert first.session.session_id not in view._unread


def test_history_search_and_delete_do_not_resurrect_chat(workspace, monkeypatch):
    root, view, service = workspace
    page = view.current
    stream = send(page, service, "Aranabilir başlık")
    emit(root, stream, AgentEventType.DONE, content="Yanıt")
    old_id = page.session.session_id
    view.search.insert(0, "bulunmayan")
    view.refresh_history()
    assert not view.history_list.winfo_children()
    view.search.delete(0, "end")
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *args: True)
    page._delete_chat()
    pump(root)
    assert old_id not in view.pages
    assert service.chat_history.load(old_id) is None
    assert view.current.session.session_id != old_id


def test_workspace_toolbar_fits_small_window(workspace):
    root, view, _ = workspace
    root.geometry("720x760")  # 940 px app minus the outer 220 px navigation.
    pump(root)
    page = view.current
    for widget in (page.model_combo, page.mcp_btn, page.toggle_params_btn, page.send_btn):
        assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()


def test_browser_reclaims_history_space_and_wraps_controls_at_default_app_width(workspace):
    root, view, service = workspace
    # Hosted desktops can report a screen narrower than the wide-layout case.
    # Allow the test window to reach that size instead of Tk clamping it.
    root.maxsize(2000, 1200)
    service.session_browser_settings = lambda sid: {"enabled": False, "screenshots": False}
    service.browser_command_async = lambda *args, **kwargs: None
    # Reproduce the 1120 px application with its 220 px outer navigation.
    view.pack_configure(padx=(220, 0))
    pump(root, 0.1)
    assert view.sidebar.winfo_ismapped()
    page = view.current
    page._open_browser()
    pump(root, 0.15)
    assert view._history_collapsed and not view.sidebar.winfo_ismapped()
    assert page._compact_toolbar
    body_left = page._chat_body.winfo_rootx()
    body_right = body_left + page._chat_body.winfo_width()
    for widget in (page.model_combo, page.mcp_btn, page.browser_btn, page.toggle_params_btn,
                   page.effort_selector, page._export_button, page._delete_button, page.send_btn):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() >= body_left
        assert widget.winfo_rootx() + widget.winfo_width() <= body_right
    assert page.model_combo.winfo_rooty() + page.model_combo.winfo_height() <= page.browser_btn.winfo_rooty()
    panel = page._browser_panel
    panel_right = panel.winfo_rootx() + panel.winfo_width()
    for widget in (panel.url, panel.go_button, panel.tab_selector, panel.control_button):
        assert widget.winfo_rootx() >= panel.winfo_rootx()
        assert widget.winfo_rootx() + widget.winfo_width() <= panel_right
    assert panel_right <= root.winfo_rootx() + root.winfo_width()
    root.geometry("940x760")  # The application minimum leaves 720 px here.
    pump(root, 0.1)
    assert not view.sidebar.winfo_ismapped()
    for widget in (page.model_combo, page.mcp_btn, page.browser_btn, page.toggle_params_btn, page.send_btn):
        assert widget.winfo_rootx() + widget.winfo_width() <= page._chat_body.winfo_rootx() + page._chat_body.winfo_width()
    for widget in (panel.url, panel.go_button, panel.tab_selector, panel.control_button):
        assert widget.winfo_rootx() + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width()
    assert panel.winfo_rootx() + panel.winfo_width() <= root.winfo_rootx() + root.winfo_width()
    panel._result("browser_request_handoff", {"state": "manual_control"})
    pump(root)
    assert panel.mode.cget("text") == "Ajan duraklatıldı"
    assert panel.mode.winfo_rootx() + panel.mode.winfo_width() <= root.winfo_rootx() + root.winfo_width()
    root.geometry("1700x760")
    pump(root, 0.1)
    assert view.sidebar.winfo_ismapped()
    root.geometry("1120x760")
    pump(root, 0.1)
    assert not view.sidebar.winfo_ismapped()
    page._close_browser_panel()
    pump(root, 0.1)
    assert view.sidebar.winfo_ismapped() and not view._history_collapsed


def test_sidebar_reports_each_background_chats_actual_phase(workspace):
    root, view, service = workspace
    first = view.current
    stream = send(first, service)
    view.new_chat()
    emit(root, stream, AgentEventType.TOOL_CALL_STARTED, tool_name="run_command",
         arguments={"command": "python scene.py"})
    assert any("Komut çalışıyor" in child.cget("text") for child in view.history_list.winfo_children())
    emit(root, stream, AgentEventType.TOOL_CALL_RESULT, tool_name="run_command", content="OK")
    assert any("Model yanıtı bekleniyor" in child.cget("text") for child in view.history_list.winfo_children())
    assert first.live_activity.indicator.running
    view.open_chat(first.session.session_id)
    pump(root)
    assert first.live_activity.winfo_ismapped()
    assert "Komutlar tamamlandı" in first.live_activity.detail.cget("text")
