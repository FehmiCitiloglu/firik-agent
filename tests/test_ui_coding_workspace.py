"""Native Tk integration checks for Coding Agent 2.0."""
import time
from pathlib import Path

import pytest

from evren_agent.core.events import AgentEvent, AgentEventType
from evren_agent.projects.workspace import capture_workspace, compare_workspaces
from evren_agent.ui.app import EvrenApp
from evren_agent.ui.service import EvrenService


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(EvrenApp, "_check_initial_status", lambda self: None)
    monkeypatch.setattr(EvrenService, "fetch_models_async", lambda self, on_success, **kwargs: on_success([self.default_model]))
    def inspect(self, root, on_success, on_error, before=None):
        snapshot = capture_workspace(root)
        if before:
            snapshot.changes = compare_workspaces(before, snapshot)
        on_success(snapshot)
    def prepare(self, project_id, prompt, on_success, on_error, **options):
        on_success(self.projects.coding.prepare_coding_prompt(project_id, prompt, **options))
    monkeypatch.setattr(EvrenService, "inspect_workspace_async", inspect)
    monkeypatch.setattr(EvrenService, "prepare_coding_async", prepare)
    root = tmp_path / "source"
    root.mkdir()
    (root / "main.py").write_text("print('Türkçe')\n")
    app = EvrenApp()
    app.show_view("projects")
    view = app.views["projects"]
    project = view.project_service.create_project("Türkçe proje", str(root))
    view.show_workspace_screen(project.id)
    view._switch_workspace_tab("coding")
    streams = []
    monkeypatch.setattr(app.service, "chat_agent_stream_async", lambda **kwargs: streams.append(kwargs))
    yield app, view, project, streams
    app.destroy()


def settle(app, seconds=0.15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.update()
        time.sleep(0.005)


def test_modes_multiline_cards_approvals_and_project_identity(workspace, monkeypatch):
    app, view, project, streams = workspace
    view._coding_mode_selected("Planla")
    view.coding_input.insert(0, "Plan oluştur\nKaynakları incele")
    view.coding_send_btn.invoke()
    call = streams[-1]
    assert call["coding_options"]["mode"] == "plan"
    assert call["prompt"] == "Plan oluştur\nKaynakları incele"
    emit = lambda kind, **kwargs: call["on_event"](AgentEvent(type=kind, session_id=call["session_id"], **kwargs))
    emit(AgentEventType.TEXT_DELTA, content="Türkçe açıklama\n```python\nprint('Merhaba')\n```\n")
    emit(AgentEventType.TOOL_CALL_STARTED, tool_name="read_file", arguments={"path": "main.py"})
    assert "[Araç:" not in view.coding_chat_box.get()
    emit(AgentEventType.APPROVAL_REQUIRED, metadata={"request_id": "approval-1", "tool_name": "edit_file",
                                                   "arguments": {"path": "main.py", "old_text": "old", "new_text": "new"}})
    requests = view._coding_state()["pending_approvals"]
    assert requests["approval-1"]["tool_name"] == "edit_file"
    assert requests["approval-1"]["arguments"]["path"] == "main.py"
    assert view.coding_approval_frame.winfo_children()
    decisions = []
    monkeypatch.setattr(app.service, "coding_control", lambda *args: decisions.append(args) or True)
    view._decide_coding("approval-1", False)
    assert decisions[-1] == (call["session_id"], "approve", "approval-1", False)
    emit(AgentEventType.PLAN_UPDATED, metadata={"steps": [{"title": "İncele", "status": "pending"}]})
    assert view._coding_state()["plan"][0]["title"] == "İncele"
    call["on_done"]("Plan hazır")
    settle(app)
    bodies = [body for _, body in view.coding_chat_box.cards.values()]
    assert any(body.code_sources for body in bodies)
    code = next(body for body in bodies if body.code_sources)
    assert any(block._textbox.tag_ranges("syntax-string") for block in code.blocks)
    first_session = view._coding_state()["session"].session_id
    tasks = view.project_service.db.list_tasks(project.id)
    view._start_task_with_agent(tasks[0].id)
    assert view._coding_state()["session"].session_id != first_session
    streams[-1]["on_done"]("Görev incelendi")
    other = view.project_service.create_project("İkinci", str(Path(project.local_path).parent / "other"))
    view.show_workspace_screen(other.id)
    view._switch_workspace_tab("coding")
    state = view._coding_state()
    old_content = state["text"]
    emit(AgentEventType.TEXT_DELTA, content="Yanlış projede görünmemeli")
    assert state["text"] == old_content


def test_panel_preferences_and_long_conversation_are_bounded(workspace):
    app, view, project, streams = workspace
    view._save_coding_preferences({"font_size": 17, "show_left": False, "left_width": 260})
    assert view.project_service.db.get_settings(project.id).coding_preferences["font_size"] == 17
    messages = [{"role": "assistant", "content": f"Mesaj {i}"} for i in range(200)]
    messages[-1]["content"] = "Uzun kod " * 8000
    view.coding_chat_box.set_messages(messages)
    settle(app)
    assert len(view.coding_chat_box.cards) == 40
    assert max(len(body.source) for _, body in view.coding_chat_box.cards.values()) < 24200
    for width, height in ((940, 620), (1120, 760), (1600, 900)):
        app.geometry(f"{width}x{height}")
        settle(app, 0.05)
        assert view.coding_input.winfo_width() > 150
        assert view.coding_send_btn.winfo_rootx() + view.coding_send_btn.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    view._switch_workspace_tab("overview")
    view._switch_workspace_tab("coding")
    assert view.coding_input._font.cget("size") == 17


def test_native_drop_line_references_and_compact_approval_review(workspace, monkeypatch):
    import customtkinter as ctk
    app, view, project, streams = workspace
    root = Path(project.local_path)
    spaced = root / "Türkçe dosya.py"
    spaced.write_text("print('Merhaba')\n", encoding="utf-8")
    assert view.coding_drop_available
    textbox = view.coding_input._textbox
    assert textbox.tk.call("package", "present", "tkdnd")
    binding = textbox.tk.call("bind", str(textbox), "<<Drop>>")
    command = textbox.tk.splitlist(binding)[0]
    # Invoke the registered native TkDND binding with a Tcl file list, including spaces.
    data = textbox.tk.call("list", str(root / "main.py"), str(spaced))
    args = ["copy", "copy", "1", "", "", "", "", data, "<<Drop>>", "", "", "", "DND_Files", "", "", str(textbox), "0", "0"]
    assert textbox.tk.call(command, *args) == "copy"
    assert {"main.py", "Türkçe dosya.py"} <= set(view._coding_state()["context_paths"])
    references = []
    monkeypatch.setattr(view, "_preview_coding_file", lambda path, line_number=1: references.append((path, line_number)))
    view._open_coding_reference("main.py:12")
    view._open_coding_reference("main.py#L8")
    explorer = view.coding_explorer
    node = explorer.tree.insert("", "end", text="main.py:7")
    explorer.nodes[node] = {"path": "main.py", "line": 7, "is_dir": False}
    explorer.tree.selection_set(node)
    explorer.open_selected()
    assert references == [("main.py", 12), ("main.py", 8), ("main.py", 7)]
    app.geometry("940x620")
    state = view._coding_state()
    argument = "uzun parametre " * 400
    state["pending_approvals"] = {"a1": {"tool_name": "run_shell", "arguments": {"command": argument}}}
    view._refresh_coding_control()
    settle(app)
    assert view.coding_pending_btn.winfo_ismapped()
    view._inspect_coding_approvals()
    dialogs = [w for w in view.winfo_children() if isinstance(w, ctk.CTkToplevel)]
    dialog = dialogs[-1]
    box = next(w for w in dialog.winfo_children() if isinstance(w, ctk.CTkTextbox))
    assert argument in box.get("1.0", "end")
    dialog.destroy()


def test_history_export_selects_visible_run_and_preserves_json(workspace, monkeypatch, tmp_path):
    import json
    app, view, project, streams = workspace
    store = app.service.coding_store
    first = store.create_run(project.id, session_id="export-old", user_request="İlk çalışma")
    store.record_message(first.id, "user", 'api_key="example-secret"\nTürkçe açıklama')
    store.record_test(first.id, {"command": "pytest", "executed": True, "exit_code": 0, "output": "1 passed"})
    store.finish_run(first.id, summary="İlk çalışma tamamlandı")
    second = store.create_run(project.id, session_id="export-new", user_request="İkinci çalışma")
    store.finish_run(second.id, summary="İkinci çalışma")
    assert view._load_coding_history(first.id)
    assert view._coding_state()["activities"][0]["status"] == "Tamamlandı"
    target = tmp_path / "record.json"
    monkeypatch.setattr("tkinter.filedialog.asksaveasfilename", lambda **kwargs: str(target))
    view._export_coding()
    content = target.read_text(encoding="utf-8")
    payload = json.loads(content)
    assert payload["id"] == first.id
    assert "example-secret" not in content
    assert "Türkçe açıklama" in content


def test_stop_during_context_preparation_prevents_model_start(workspace, monkeypatch):
    from concurrent.futures import Future
    app, view, project, streams = workspace
    pending = {}
    future = Future()
    def prepare(project_id, prompt, on_success, on_error, **options):
        pending["ready"] = on_success
        return future
    monkeypatch.setattr(app.service, "prepare_coding_async", prepare)
    view._run_coding_prompt("Dosyayı incele")
    view._control_coding("cancel")
    assert future.cancelled()
    pending["ready"]({"system_prompt": "Türkçe", "visibility_report": []})
    assert not streams
    assert not view._coding_state()["busy"]
