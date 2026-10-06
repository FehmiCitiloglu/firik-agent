"""Real Tk cockpit views must retain bounded, attributable runtime evidence."""
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.projects.workspace import capture_workspace
from firik_agent.ui.coding_evidence import (
    VISIBLE_EVENTS, agent_depth, context_preference_update,
    terminal_records, test_records as recorded_tests, timeline_records,
)
from test_ui_coding_workspace import workspace as workspace, settle


def test_legacy_evidence_is_bounded_and_does_not_claim_commands_were_executed():
    state = {"activities": [{"tool": "read_file", "status": "Tamamlandı", "output": str(index)} for index in range(1000)]}
    assert len(timeline_records(state)) == VISIBLE_EVENTS
    assert timeline_records(state)[0]["result"] == "940"
    assert terminal_records(state) == []
    assert recorded_tests(state) == []


def test_context_exclusion_removes_conflicting_inclusion_and_summary_policy():
    state = {"context_paths": ["src.py"], "preferences": {
        "pinned_files": ["src.py"], "always_include_files": ["src.py"],
        "summarized_files": ["src.py"], "excluded_files": [],
    }}
    values = context_preference_update(state, "src.py", "never")
    assert values["excluded_files"] == ["src.py"]
    assert all(not values[key] for key in ("context_paths", "pinned_files", "always_include_files", "summarized_files"))
    # Computation is pure; the UI commits only after user chooses the action.
    assert state["context_paths"] == ["src.py"]
    included = context_preference_update({"preferences": values}, "src.py", "pin")
    assert included["pinned_files"] == ["src.py"] and included["excluded_files"] == []


def test_agent_parent_cycles_are_bounded():
    agents = {"parent": {"parent_id": "child"}, "child": {"parent_id": "parent"}}
    assert agent_depth("child", agents) == 1


def emit(call, kind, **kwargs):
    call["on_event"](AgentEvent(type=kind, session_id=call["session_id"], **kwargs))


def test_native_terminal_and_test_evidence_preserve_failures_and_staleness(workspace):
    app, view, project, streams = workspace
    view._run_coding_prompt("Komutu ve doğrulama kanıtını incele")
    call = streams[-1]
    emit(call, AgentEventType.TOOL_CALL_STARTED, tool_name="run_command", arguments={"command": "python check.py", "cwd": "."},
         metadata={"tool_call_id": "command-1"})
    emit(call, AgentEventType.TOOL_CALL_RESULT, tool_name="run_command",
         content='{"exit_code": 3, "output": "Actual failure", "duration_ms": 42}', metadata={"tool_call_id": "command-1"})
    emit(call, AgentEventType.VERIFICATION, metadata={"kind": "test", "evidence_id": "test-1", "command": "pytest", "passed": True,
                                                    "exit_code": 0, "output": "1 passed"})
    assert terminal_records(view._coding_state())[-1]["exit_code"] == 3
    assert recorded_tests(view._coding_state())[-1]["passed"] is True
    emit(call, AgentEventType.FILE_CHANGED, metadata={"path": "main.py", "after_hash": "changed"})
    assert recorded_tests(view._coding_state())[-1]["stale"] is True
    view._toggle_coding_evidence("terminal", "command-1")
    settle(app)
    card = view._coding_evidence_cache["terminal"]["command-1"][1]
    import customtkinter as ctk
    boxes = [child for child in card.winfo_children() if isinstance(child, ctk.CTkTextbox)]
    assert boxes and "Actual failure" in boxes[0].get("1.0", "end")
    call["on_done"]("Kanıtlar raporlandı")


def test_native_timeline_widgets_and_expansion_are_bounded(workspace):
    app, view, project, streams = workspace
    state = view._coding_state()
    # Populate the real projection in bulk, then render once; a replay does not
    # allocate one widget for every durable audit event.
    cockpit = state["cockpit"]
    for index in range(900):
        cockpit.apply({"type": "file_changed", "metadata": {"path": f"file-{index}.py"}})
    view._refresh_coding_evidence()
    settle(app)
    assert len(view._coding_evidence_cache["activity"]) == VISIBLE_EVENTS
    first_ids = set(view._coding_evidence_cache["activity"])
    first_card = next(iter(view._coding_evidence_cache["activity"].values()))[1]
    view._refresh_coding_evidence()
    assert next(iter(view._coding_evidence_cache["activity"].values()))[1] is first_card
    cockpit.apply({"type": "mcp_error", "server_name": "GitHub", "content": "Connection refused", "metadata": {"status": "failed"}})
    view._refresh_coding_evidence()
    assert len(view._coding_evidence_cache["activity"]) == VISIBLE_EVENTS
    assert len(first_ids - set(view._coding_evidence_cache["activity"])) == 1


def test_manual_command_approval_is_bound_to_original_project(workspace, monkeypatch):
    app, view, project, streams = workspace
    calls = []
    monkeypatch.setattr(app.service, "run_coding_command_async", lambda *args, **kwargs: calls.append((args, kwargs)))
    original_state = view._coding_state()
    original_session = original_state["session"].session_id
    view._execute_coding_command("pytest", kind="test")
    args, callbacks = calls[-1]
    assert args == (original_session, "pytest")
    assert callbacks["kind"] == "test" and original_state["busy"]
    callbacks["on_event"](AgentEvent(AgentEventType.APPROVAL_REQUIRED, original_session,
        tool_name="run_tests", arguments={"command": "pytest"}, metadata={"request_id": "manual-approval"}))
    assert original_state["pending_approvals"]["manual-approval"]["arguments"]["command"] == "pytest"
    other = view.project_service.create_project("Other", str(project.local_path) + "-other")
    view.show_workspace_screen(other.id)
    view._switch_workspace_tab("coding")
    other_state = view._coding_state()
    callbacks["on_success"]({"exit_code": 0, "output": "1 passed"})
    assert not original_state["busy"]
    assert not other_state["busy"] and other_state["pending_approvals"] == {}
    assert other_state["status"] == "Hazır"


def test_command_preparation_cannot_dispatch_into_newly_selected_project(workspace, monkeypatch):
    app, view, project, streams = workspace
    preparations, commands = [], []
    monkeypatch.setattr(app.service, "inspect_workspace_async", lambda root, on_success, on_error, **kwargs:
                        preparations.append(on_success))
    monkeypatch.setattr(app.service, "run_coding_command_async", lambda *args, **kwargs: commands.append((args, kwargs)))
    original_session = view._coding_state()["session"].session_id
    view._execute_coding_command("python check.py")
    original_ready = preparations[0]
    other = view.project_service.create_project("Different", str(project.local_path) + "-different")
    view.show_workspace_screen(other.id)
    view._switch_workspace_tab("coding")
    original_ready(capture_workspace(project.local_path))
    assert commands[-1][0] == (original_session, "python check.py")
    assert commands[-1][0][0] != view._coding_state()["session"].session_id
    commands[-1][1]["on_success"]({"exit_code": 0})


def children(root):
    for child in root.winfo_children():
        yield child
        yield from children(child)


def test_idle_spawn_captures_dialog_choices_before_background_inspection(workspace, monkeypatch):
    import customtkinter as ctk
    app, view, project, streams = workspace
    preparations, spawned = [], []
    monkeypatch.setattr(app.service, "inspect_workspace_async", lambda root, on_success, on_error, **kwargs:
                        preparations.append(on_success))
    monkeypatch.setattr(app.service, "spawn_coding_agent_async", lambda *args, **kwargs: spawned.append((args, kwargs)))
    dialog = view._new_coding_agent()
    task = next(child for child in dialog.winfo_children() if isinstance(child, ctk.CTkTextbox))
    task.insert("end", "Inspect main.py")
    button = next(child for child in dialog.winfo_children() if isinstance(child, ctk.CTkButton) and child.cget("text") == "Ajanı başlat")
    button.invoke()
    assert not dialog.winfo_exists()
    preparations[0](capture_workspace(project.local_path))
    assert spawned[-1][0][1:] == ("researcher", "Inspect main.py")
    assert spawned[-1][1]["provider"] == app.service.provider
    assert callable(spawned[-1][1]["on_event"])
    spawned[-1][1]["on_success"]({"status": "completed"})


def test_checkpoint_restore_scope_contains_only_visible_reviewed_files(workspace, monkeypatch):
    import customtkinter as ctk
    app, view, project, streams = workspace
    paths = [f"file-{index}.py" for index in range(61)]
    preview = {"changes": [{"path": path, "added": 1, "removed": 0, "revision": "revision", "hunks": []} for path in paths],
               "expected_hashes": {path: "hash" for path in paths}, "revision": "revision"}
    restored = []
    monkeypatch.setattr(app.service, "compare_coding_checkpoint_async", lambda *args, **kwargs: kwargs["on_success"](preview))
    monkeypatch.setattr(app.service, "restore_coding_checkpoint_async", lambda *args, **kwargs: restored.append((args, kwargs)))
    view._compare_coding_checkpoint("checkpoint-1")
    dialog = view.coding_checkpoint_dialog
    # Large file diffs are absent until an individual file is expanded.
    assert not any(isinstance(child, ctk.CTkTextbox) for child in children(dialog))
    button = next(child for child in children(dialog) if isinstance(child, ctk.CTkButton) and child.cget("text") == "Gösterilen dosyaları geri yükle")
    button.invoke()
    assert set(restored[-1][0][2]) == set(paths[:60])
    assert paths[-1] not in restored[-1][0][2]
