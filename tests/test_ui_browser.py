"""Native Tk browser controls; requires a graphical desktop, no browser launch."""
from __future__ import annotations

import io
from types import SimpleNamespace

import customtkinter as ctk
from PIL import Image
import pytest

from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.components.browser_preview import BrowserPanel
from test_ui_coding_workspace import workspace, settle


@pytest.fixture
def panel():
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("1050x800")
    events = []
    def command(sid, operation, arguments=None, **kwargs):
        events.append((sid, operation, arguments))
        kwargs["on_done"]({"state": "ready"})
    service = SimpleNamespace(
        session_browser_settings=lambda sid: {"enabled": False, "origins": [], "private_origins": [], "screenshots": False},
        set_session_browser=lambda sid, **kwargs: events.append((sid, "configure", kwargs)),
        browser_command_async=command,
        close_browser_async=lambda sid: events.append((sid, "close", {})),
        stop_browser_async=lambda sid: events.append((sid, "stop", {})),
    )
    view = BrowserPanel(root, service, "chat-a")
    view.pack(fill="both", expand=True)
    root.update()
    try:
        yield root, view, service, events
    finally:
        root.destroy()


def test_explicit_scope_and_preview_defaults(panel):
    root, view, service, events = panel
    assert not view.enabled.get() and not view.screenshots.get() and not view.live.get()
    view.enabled.set(True)
    view.origins.insert(0, "http://localhost:3000")
    view.private_origins.insert(0, "http://localhost:3000")
    view._apply_permissions()
    assert events == [("chat-a", "configure", {"enabled": True, "origins": ["http://localhost:3000"],
        "private_origins": ["http://localhost:3000"], "screenshots": False})]
    view._command("browser_start")
    assert events[-1] == ("chat-a", "browser_start", {"headless": False})
    view._stop()
    assert events[-1] == ("chat-a", "stop", {})
    assert "güncel değil" in view.preview.cget("text")


def test_preview_and_handoff_do_not_capture_authentication(panel):
    root, view, service, events = panel
    image = Image.new("RGB", (400, 200), "blue")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    service.browser_preview_async = lambda sid, **kwargs: kwargs["on_done"](buffer.getvalue())
    view._preview()
    root.update()
    assert view._image is not None
    view.live.set(True)
    view._result("browser_request_handoff", {"state": "manual_control"})
    assert not view.live.get() and view._image is None
    assert "duraklatıldı" in view.preview.cget("text")


def test_coding_browser_tab_saves_project_grant_without_launch(workspace):
    app, view, project, streams = workspace
    view.coding_tabs.set("Tarayıcı")
    settle(app)
    panel = view.coding_browser_panel
    assert panel.winfo_ismapped()
    assert not panel.enabled.get() and not view._coding_state()["permissions"]["allow_browser"]
    panel.enabled.set(True)
    panel.origins.insert(0, "http://localhost:5173")
    panel.private_origins.insert(0, "http://localhost:5173")
    panel._apply_permissions()
    preferences = view.project_service.db.get_settings(project.id).coding_preferences
    assert preferences["permissions"]["allow_browser"] is True
    assert preferences["browser_origins"] == ["http://localhost:5173"]
    assert preferences["browser_private_origins"] == ["http://localhost:5173"]
    assert app.service.browser_manager.sessions(panel.service._chat_browser_adapter(
        app.service.get_or_create_session("independent-chat")).identity) == []
    assert not streams
