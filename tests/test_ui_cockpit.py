"""Native cockpit navigation and event-backed workspace integration."""
import json

from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.ui.coding_strings import WORKSPACE_VIEWS
from test_ui_coding_workspace import workspace as workspace, settle


def test_keyboard_views_persist_and_bindings_follow_workspace_lifetime(workspace):
    app, view, project, streams = workspace
    settle(app)
    assert tuple(view.coding_tabs._tab_dict) == WORKSPACE_VIEWS
    assert view.coding_tabs.get() == "Etkinlik"
    view.coding_input._textbox.focus_force()
    view.coding_input._textbox.event_generate("<Control-Key-4>")
    settle(app)
    assert view.coding_tabs.get() == "Terminal"
    assert view.project_service.db.get_settings(project.id).coding_preferences["workspace_view"] == "Terminal"
    shortcut = f"<{view.coding_shortcut_modifier}-Key-4>"
    assert app.bind(shortcut)
    view._switch_workspace_tab("overview")
    assert not app.bind(shortcut)
    view._switch_workspace_tab("coding")
    settle(app)
    assert view.coding_tabs.get() == "Terminal"
    view.coding_input._textbox.focus_force()
    view.coding_input._textbox.event_generate("<Control-Key-2>")
    settle(app)
    assert view.coding_tabs.get() == "Plan"
    # Rebuilding the project header replaces its live Git subscription. Native
    # CTk destruction originates from the canvas, so callbacks must not leak.
    listeners = set(app.service.git_state._listeners)
    view.show_workspace_screen(project.id)
    settle(app)
    assert len(app.service.git_state._listeners) == len(listeners) == 1
    assert listeners.isdisjoint(app.service.git_state._listeners)


def test_palette_filters_and_executes_selected_keyboard_action(workspace, monkeypatch):
    app, view, project, streams = workspace
    actions = []
    monkeypatch.setattr(view, "_coding_create_agent", lambda: actions.append("new-agent"))
    dialog = view._coding_palette()
    settle(app)
    dialog.search.insert(0, "Yeni Ajan")
    settle(app)
    assert dialog.results.size() == 1
    assert "Yeni Ajan" in dialog.results.get(0)
    dialog.search._entry.focus_force()
    dialog.search._entry.event_generate("<Return>")
    settle(app)
    assert actions == ["new-agent"]
    assert not dialog.winfo_exists()


def test_live_commands_and_verification_survive_switching_views(workspace):
    app, view, project, streams = workspace
    view._coding_quick_action("Gerçek doğrulama sonuçlarını incele")
    view.coding_send_btn.invoke()
    call = streams[-1]
    def emit(kind, **values):
        call["on_event"](AgentEvent(kind, call["session_id"], **values))
    emit(AgentEventType.TOOL_CALL_STARTED, tool_name="run_tests", arguments={"command": "pytest", "cwd": "."},
         metadata={"tool_call_id": "test-command"})
    emit(AgentEventType.TOOL_CALL_RESULT, tool_name="run_tests",
         content=json.dumps({"command": "pytest", "executed": True, "exit_code": 1, "output": "1 failed"}),
         metadata={"tool_call_id": "test-command", "is_error": True}, duration_ms=120)
    emit(AgentEventType.VERIFICATION, metadata={"evidence_id": "test-evidence", "command": "pytest", "executed": True,
         "passed": False, "exit_code": 1, "output": "1 failed", "duration_ms": 120})
    cockpit = view._coding_state()["cockpit"]
    assert cockpit.terminal[-1]["exit_code"] == 1
    assert cockpit.tests[-1]["exit_code"] == 1
    view.coding_tabs.set("Terminal")
    settle(app)
    assert view.coding_terminal_list.winfo_children()
    view.coding_tabs.set("Testler")
    settle(app)
    assert view.coding_test_list.winfo_children()
    assert len(cockpit.terminal) == 1
    call["on_done"]("Doğrulama başarısız; düzeltme gerekiyor.")
    settle(app)
    assert not view._coding_state()["busy"]
    assert cockpit.summary


def test_inspector_drawer_keeps_composer_visible_on_compact_screen(workspace):
    app, view, project, streams = workspace
    app.geometry("940x620")
    settle(app)
    view._show_coding_inspector("Ajanlar")
    settle(app)
    assert str(view.coding_right) in tuple(map(str, view.coding_panes.panes()))
    assert str(view.coding_left) not in tuple(map(str, view.coding_panes.panes()))
    assert view.coding_input.winfo_width() > 150
    assert view.coding_send_btn.winfo_rootx() + view.coding_send_btn.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    view._focus_coding_file_search()
    settle(app)
    assert str(view.coding_left) in tuple(map(str, view.coding_panes.panes()))
    assert str(view.coding_right) not in tuple(map(str, view.coding_panes.panes()))


def test_composer_autocomplete_resolves_real_project_paths_and_plan_mode(workspace):
    app, view, project, streams = workspace
    view.coding_input.insert(0, "@file:main")
    view._load_coding_composer_suggestions(view.coding_input, project.id)
    settle(app, 0.5)
    assert any(item["insert"] == "@file:main.py " for item in view._coding_suggestions)
    view.coding_input._textbox.focus_force()
    view.coding_input._textbox.event_generate("<Tab>")
    settle(app)
    assert view.coding_input.get() == "@file:main.py "
    view.coding_input.delete(0, "end")
    view.coding_input.insert(0, "/plan @file:main.py Kaynağı incele")
    view.coding_send_btn.invoke()
    call = streams[-1]
    assert call["coding_options"]["mode"] == "plan"
    assert "/plan" not in call["prompt"]
    assert "main.py" in call["prompt"]
    assert any(item.get("path") == "main.py" and item.get("included") for item in view._coding_state()["context_items"])
    call["on_done"]("Plan hazır")
    settle(app)
