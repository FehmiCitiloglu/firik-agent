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
