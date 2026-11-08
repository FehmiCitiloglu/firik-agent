"""Native inline browser controls; a graphical desktop is required."""
from __future__ import annotations

import io
from types import SimpleNamespace

import customtkinter as ctk
from PIL import Image
import pytest

from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.components.browser_preview import BrowserPanel, browser_address
from test_ui_coding_workspace import workspace, settle
from test_ui_chat import chat


@pytest.fixture
def panel():
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("1050x800")
    events = []
    tabs = [{"tab_id": "tab-a", "title": "Example", "url": "https://example.org", "active": True}]
    image = Image.new("RGB", (1280, 800), "blue")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    state = {"value": "ready"}
    def command(sid, operation, arguments=None, **kwargs):
        events.append((sid, operation, arguments))
        if operation == "browser_request_handoff":
            state["value"] = "manual_control"
        elif operation == "browser_resume_after_handoff":
            state["value"] = "ready"
        data = {"state": state["value"], "active_tab_id": "tab-a"}
        if operation == "browser_list_tabs":
            data["tabs"] = tabs
        kwargs["on_done"](data)
    def host_input(sid, kind, arguments, **kwargs):
        events.append((sid, kind, arguments))
        kwargs["on_done"]({"state": "manual_control"})
    service = SimpleNamespace(
        session_browser_settings=lambda sid: {"enabled": False, "origins": [], "private_origins": [], "screenshots": False},
        set_session_browser=lambda sid, **kwargs: events.append((sid, "configure", kwargs)),
        browser_command_async=command,
        browser_input_async=host_input,
        browser_preview_async=lambda sid, **kwargs: kwargs["on_done"](buffer.getvalue()),
        stop_browser_async=lambda sid: events.append((sid, "stop", {})),
    )
    view = BrowserPanel(root, service, "chat-a")
    view.pack(fill="both", expand=True)
    root.update()
    try:
        yield root, view, service, events
    finally:
        root.destroy()


def test_address_bar_accepts_domains_searches_and_preserves_policy_checks():
    assert browser_address("example.org/path") == "https://example.org/path"
    assert browser_address("firik browser") == "https://www.google.com/search?q=firik+browser"
    assert browser_address("http://example.org") == "http://example.org"
    assert browser_address("file:///etc/passwd") == "file:///etc/passwd"
    with pytest.raises(ValueError):
        browser_address("https://example.org\n/secret")


def test_navigate_applies_grant_and_starts_inline_without_manual_domain_list(panel):
    root, view, service, events = panel
    assert not view.enabled.get() and not view.screenshots.get()
    assert view.live.get()
    assert not hasattr(view, "origins")
    view.url.insert(0, "example.org")
    view._navigate()
    actions = [operation for _, operation, _ in events]
    assert actions.index("configure") < actions.index("browser_start") < actions.index("browser_navigate")
    started = next(arguments for _, operation, arguments in events if operation == "browser_start")
    assert started["headless"] is True
    scale = view._get_widget_scaling()
    assert started["width"] == max(320, min(2560, int(view.preview.winfo_width()/scale) - 8))
    assert started["height"] == max(240, min(1440, int(view.preview.winfo_height()/scale) - 8))
    assert next(arguments for _, operation, arguments in events if operation == "browser_navigate") == {"url": "https://example.org"}
    assert view.enabled.get() and view.screenshots.get()
    assert all(arguments["origins"] == [] for _, operation, arguments in events if operation == "configure")
    view._stop()
    assert events[-1] == ("chat-a", "stop", {})
    assert view._image is None and view._state == "closed"


def test_inline_handoff_keeps_host_view_and_serializes_input(panel):
    root, view, service, events = panel
    view._command("browser_start")
    root.update()
    assert view._image is not None
    view._toggle_control()
    root.update()
    assert view._state == "manual_control" and view.live.get()
    assert view.control_button.cget("text") == "Ajana geri ver"
    view._input("click", {"x": 100, "y": 120})
    view._input("text", {"text": "Türkçe"})
    assert [(operation, args) for _, operation, args in events if operation in {"click", "text"}] == [
        ("click", {"x": 100, "y": 120}), ("text", {"text": "Türkçe"})]
    view._toggle_control()
    view._input("text", {"text": "should not run"})
    assert view._state == "ready"
    assert not any((args or {}).get("text") == "should not run" for _, _, args in events)


def test_click_coordinates_exclude_letterbox_and_map_to_page(panel):
    root, view, service, events = panel
    view._command("browser_start")
    root.update()
    center = SimpleNamespace(x_root=view.preview.winfo_rootx() + view.preview.winfo_width()/2,
        y_root=view.preview.winfo_rooty() + view.preview.winfo_height()/2)
    point = view._page_point(center)
    assert abs(point["x"] - 640) <= 1 and abs(point["y"] - 400) <= 1
    outside = SimpleNamespace(x_root=view.preview.winfo_rootx()-1, y_root=center.y_root)
    assert view._page_point(outside) is None


def test_native_preview_bindings_send_click_and_keyboard_only_after_takeover(panel):
    root, view, service, events = panel
    # Synthetic clicks do not activate an unfocused native window. Establish
    # desktop focus before testing the preview's own click-to-focus binding.
    root.focus_force()
    settle(root)
    view._command("browser_start")
    root.update()
    label = view.preview._label
    label.event_generate("<Button-1>", x=label.winfo_width()//2, y=label.winfo_height()//2)
    root.update()
    assert not any(operation == "click" for _, operation, _ in events)
    view._toggle_control()
    label.event_generate("<Button-1>", x=label.winfo_width()//2, y=label.winfo_height()//2)
    root.update()
    assert any(operation == "click" for _, operation, _ in events)
    assert root.focus_get() is label
    label.event_generate("<KeyPress>", keysym="a")
    root.update()
    assert any(operation == "text" and arguments == {"text": "a"} for _, operation, arguments in events)


def test_disabling_preview_discards_an_inflight_frame(panel):
    root, view, service, events = panel
    view._command("browser_start")
    root.update()
    pending = []
    service.browser_preview_async = lambda sid, **callbacks: pending.append(callbacks)
    view._preview()
    assert pending
    view.screenshots.set(False)
    view._apply_permissions()
    pending[0]["on_done"](b"a stale screenshot must not even be decoded")
    assert view._image is None
    assert "kapalı" in view.preview.cget("text")


def test_failed_start_keeps_navigation_available_for_retry(panel):
    root, view, service, events = panel
    original = service.browser_command_async
    failed = {"once": True}
    def command(sid, operation, arguments=None, **kwargs):
        if operation == "browser_start" and failed["once"]:
            failed["once"] = False
            kwargs["on_done"]({"error": "browser_launch_failed", "message": "launch failed"})
        else:
            original(sid, operation, arguments, **kwargs)
    service.browser_command_async = command
    view.url.insert(0, "example.org")
    view._navigate()
    assert "açılamadı" in view.status.cget("text")
    view._retry()
    assert any(operation == "browser_navigate" for _, operation, _ in events)


def test_coding_browser_tab_saves_project_grant_without_launch(workspace):
    app, view, project, streams = workspace
    view.coding_tabs.set("Tarayıcı")
    settle(app)
    panel = view.coding_browser_panel
    assert panel.winfo_ismapped()
    assert not panel.enabled.get() and not view._coding_state()["permissions"]["allow_browser"]
    panel.enabled.set(True)
    panel.screenshots.set(True)
    panel._apply_permissions()
    preferences = view.project_service.db.get_settings(project.id).coding_preferences
    assert preferences["permissions"]["allow_browser"] is True
    assert preferences["browser_origins"] == []
    assert preferences["browser_private_origins"] == []
    assert not streams


def test_browser_opens_beside_chat_and_closes_without_new_window(chat):
    root, view, service = chat
    service.session_browser_settings = lambda sid: {"enabled": False, "screenshots": False}
    service.browser_command_async = lambda *args, **kwargs: None
    root.geometry("1400x850")
    view._open_browser()
    settle(root)
    panel = view._browser_panel
    assert panel is not None and panel.winfo_ismapped()
    assert panel.winfo_toplevel() is root
    assert panel.winfo_rootx() >= view.chat_scroll.winfo_rootx() + view.chat_scroll.winfo_width()
    assert len(view._split.panes()) == 2
    view._close_browser_panel()
    settle(root)
    assert view._browser_panel is None and len(view._split.panes()) == 1


def test_opening_chat_browser_grants_only_this_chat_without_launching(chat):
    from unittest.mock import Mock

    from firik_agent.browser.config import BrowserConfig
    from firik_agent.core.session import ChatSession, SessionToolFilter
    from firik_agent.core.tools import ToolRegistry
    from firik_agent.ui.browser_bridge import BrowserBridge

    root, view, service = chat
    service.browser_manager = SimpleNamespace(config=BrowserConfig())
    for name in ("session_browser_settings", "set_session_browser", "_browser_grant"):
        setattr(service, name, getattr(BrowserBridge, name).__get__(service))
    service.browser_command_async = Mock()
    service.browser_preview_async = Mock()
    session = view.session
    session.browser_origins = ("https://example.org", "http://127.0.0.1:3000")
    session.browser_private_origins = ("http://127.0.0.1:3000",)
    other = ChatSession()
    service.sessions[other.session_id] = other

    view._open_browser()
    settle(root)

    assert session.browser_enabled and session.browser_screenshots
    assert session.browser_origins == ("https://example.org", "http://127.0.0.1:3000")
    assert session.browser_private_origins == ("http://127.0.0.1:3000",)
    assert not other.browser_enabled and not other.browser_screenshots
    assert service.browser_manager.config.enabled
    assert view._browser_panel._state == "uninitialized"
    assert view.browser_btn.cget("text") == "Tarayıcı açık"
    # UI polling may ask for status; opening must not launch or navigate.
    assert all(call.args[1] == "browser_status" for call in service.browser_command_async.call_args_list)
    service.browser_preview_async.assert_not_called()
    adapter = BrowserBridge._chat_browser_adapter(service, session)
    gate = SessionToolFilter(ToolRegistry(), browser_adapter=adapter)
    names = {schema["function"]["name"] for schema in gate.get_schemas(session)}
    assert {"browser_status", "browser_start", "browser_list_tabs", "browser_snapshot"} <= names
    assert not gate.get_schemas(other)


def test_opening_chat_browser_does_not_claim_access_when_grant_fails(chat):
    from unittest.mock import Mock

    root, view, service = chat
    service.session_browser_settings = lambda sid: {"enabled": False, "screenshots": False}
    service.set_session_browser = Mock(side_effect=PermissionError("Bu sohbetin tarayıcı izni değiştirilemedi."))
    service.browser_command_async = Mock()

    view._open_browser()
    settle(root)

    assert not view.session.browser_enabled
    assert view.browser_btn.cget("text") == "Tarayıcı"
    assert "izni değiştirilemedi" in view._browser_panel.status.cget("text")
    service.browser_command_async.assert_not_called()
    # Clicking the same panel's button retries the explicit grant.
    view._open_browser()
    assert service.set_session_browser.call_count == 2
    assert view.browser_btn.cget("text") == "Tarayıcı"


def test_focusing_and_reopening_chat_browser_preserves_preview_opt_out(chat):
    from unittest.mock import Mock

    from firik_agent.browser.config import BrowserConfig
    from firik_agent.ui.browser_bridge import BrowserBridge

    root, view, service = chat
    service.browser_manager = SimpleNamespace(config=BrowserConfig())
    service.session_browser_settings = BrowserBridge.session_browser_settings.__get__(service)
    service.set_session_browser = Mock(wraps=BrowserBridge.set_session_browser.__get__(service))
    service.browser_command_async = Mock()
    service.browser_preview_async = Mock()

    view._open_browser()
    panel = view._browser_panel
    assert view.session.browser_enabled and view.session.browser_screenshots
    panel.screenshots.set(False)
    assert panel._apply_permissions()
    calls_after_opt_out = service.set_session_browser.call_count

    view._open_browser()
    assert view._browser_panel is panel
    assert view.session.browser_enabled and not view.session.browser_screenshots
    assert not panel.screenshots.get()
    assert service.set_session_browser.call_count == calls_after_opt_out

    view._close_browser_panel()
    view._open_browser()
    settle(root)
    assert view._browser_panel is not panel
    assert view.session.browser_enabled and not view.session.browser_screenshots
    assert not view._browser_panel.screenshots.get()
    assert view.browser_btn.cget("text") == "Tarayıcı açık"
    assert service.set_session_browser.call_count == calls_after_opt_out
    service.browser_preview_async.assert_not_called()


def test_tab_strip_routes_selection_and_inactive_close_by_id(panel):
    root, view, service, events = panel
    view._result("browser_status", {"state": "ready", "active_tab_id": "tab-b", "tabs": [
        {"tab_id": "tab-a", "title": "Aynı başlık", "url": "https://example.org/a"},
        {"tab_id": "tab-b", "title": "Aynı başlık", "url": "https://example.org/b", "active": True},
    ]}, quiet=True)
    assert view._active_tab == "tab-b" and len(view._tabs) == 2
    assert view.url.get() == "https://example.org/b"
    # Keep completion asynchronous so the test observes the actual dispatch,
    # rather than the fixture's unrelated default single-tab refresh.
    service.browser_command_async = lambda sid, op, args=None, **callbacks: events.append((sid, op, args))
    events.clear()
    view._close_tab("tab-a")
    view._select_tab("tab-b")
    assert [(op, args) for _, op, args in events if op != "configure"] == [
        ("browser_close_tab", {"tab_id": "tab-a"})]
    view._select_tab("tab-a")
    assert events[-1] == ("chat-a", "browser_select_tab", {"tab_id": "tab-a"})


def test_tab_strip_tracks_host_active_tab_and_last_tab_closure(panel):
    root, view, service, events = panel
    tabs = [
        {"tab_id": "tab-a", "title": "Birinci", "url": "https://example.org/a", "active": False},
        {"tab_id": "tab-b", "title": "İkinci", "url": "https://example.org/b", "active": True},
    ]
    view._result("browser_list_tabs", {"state": "ready", "tabs": tabs}, quiet=True)
    assert view._active_tab == "tab-b"
    assert view.url.get() == "https://example.org/b"
    view._result("browser_status", {"state": "ready", "active_tab_id": "", "tabs": []}, quiet=True)
    assert view._tabs == {} and view._active_tab == ""
    assert view.url.get() == ""
    events.clear()
    view._close_tab("tab-b")
    assert not events


def test_new_tab_button_creates_only_one_initial_tab(panel):
    root, view, service, events = panel
    view._new_tab()
    assert any(op == "browser_start" for _, op, _ in events)
    assert not any(op == "browser_new_tab" for _, op, _ in events)
    events.clear()
    view._new_tab()
    assert any(op == "browser_new_tab" for _, op, _ in events)
    view._stop()
    assert view._tabs == {} and view._active_tab == ""


def test_tab_changes_discard_stale_preview_and_queued_page_input(panel):
    root, view, service, events = panel
    view._command("browser_start")
    assert view._image is not None
    pending = []
    service.browser_preview_async = lambda sid, **callbacks: pending.append(callbacks)
    view._preview()
    assert len(pending) == 1
    view._state = "manual_control"
    view._input_busy = True
    view._input("text", {"text": "old page input"})
    assert view._input_queue
    view._result("browser_status", {"state": "manual_control", "active_tab_id": "tab-b", "tabs": [
        {"tab_id": "tab-b", "title": "New page", "url": "https://example.org/b"},
    ]}, quiet=True)
    assert not view._input_queue
    assert view._image is None and view._viewport is None
    pending[0]["on_done"](b"old screenshot must not be decoded")
    assert view._image is None and len(pending) == 2
    view._result("browser_status", {"state": "manual_control", "active_tab_id": "", "tabs": []}, quiet=True)
    pending[1]["on_done"](b"last closed tab must not be decoded")
    assert len(pending) == 2
    assert "Açık sekme yok" in view.preview.cget("text")
    view._input("text", {"text": "no current page"})
    assert not view._input_queue


def test_new_tab_discards_queued_input_before_host_changes_active_tab(panel):
    root, view, service, events = panel
    view._command("browser_start")
    view._state = "manual_control"
    view._input_busy = True
    view._input("text", {"text": "old tab's typing"})
    assert view._input_queue
    service.browser_command_async = lambda *args, **callbacks: None
    view._new_tab()
    assert view._busy and not view._input_queue
    assert view._image is None and view._viewport is None
