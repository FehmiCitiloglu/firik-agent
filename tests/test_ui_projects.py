"""UI tests for Firik Agent Projects Workspace View."""
import tempfile
from pathlib import Path
import pytest

from firik_agent.ui.app import FirikApp
from firik_agent.ui.service import FirikService
from firik_agent.ui.views.projects_view import ProjectsView


@pytest.fixture(autouse=True)
def synchronous_workspace_inspection(monkeypatch):
    from firik_agent.projects.workspace import capture_workspace, compare_workspaces

    def inspect(self, path, on_success, on_error, before=None):
        try:
            snapshot = capture_workspace(path)
            if before is not None:
                snapshot.changes = compare_workspaces(before, snapshot)
        except Exception as error:
            on_error(str(error))
        else:
            on_success(snapshot)

    monkeypatch.setattr(FirikService, "inspect_workspace_async", inspect)
    def prepare(self, project_id, prompt, on_success, on_error, **options):
        try:
            data = self.projects.coding.prepare_coding_prompt(project_id, prompt, **options)
        except Exception as error:
            on_error(str(error))
        else:
            on_success(data)
    monkeypatch.setattr(FirikService, "prepare_coding_async", prepare)


def test_projects_view_full_lifecycle(monkeypatch):
    original_cwd = Path.cwd()
    monkeypatch.setattr("tkinter.messagebox.showinfo", lambda *a, **k: None)
    monkeypatch.setattr("tkinter.messagebox.showwarning", lambda *a, **k: None)
    monkeypatch.setattr("tkinter.messagebox.showerror", lambda *a, **k: None)
    monkeypatch.setattr(FirikApp, "_check_initial_status", lambda self: None)
    monkeypatch.setattr(
        FirikService, "fetch_models_async",
        lambda self, on_success, **kwargs: on_success([self.default_model]),
    )

    with tempfile.TemporaryDirectory() as tmpdir:

        tmp_path = Path(tmpdir)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        (tmp_path / "main.py").write_text("class TestClass:\n    pass\n")

        app = None
        try:
            app = FirikApp()
            streams = []
            monkeypatch.setattr(app.service, "chat_agent_stream_async", lambda **kwargs: streams.append(kwargs))
            # 1. Switch to projects view
            app.show_view("projects")
            assert app.active_tab == "projects"
            pv: ProjectsView = app.views["projects"]
            assert isinstance(pv, ProjectsView)

            # 2. Create a test project via service
            proj = pv.project_service.create_project(
                name="UI Test Project",
                local_path=str(tmp_path),
                description="Testing GUI interactions",
                template="Web Application",
            )
            assert proj is not None

            # 3. Refresh list and check project rendering
            pv.refresh_projects_list()
            cards = pv.cards_scroll.winfo_children()
            assert len(cards) >= 1

            # 4. Open workspace screen
            pv.show_workspace_screen(proj.id)
            assert pv.current_project_id == proj.id
            assert pv.workspace_container.winfo_ismapped() or pv.workspace_container.winfo_manager() != ""

            # 5. Switch between all workspace tabs
            sub_tabs = [
                "overview", "coding", "git", "tasks", "features", "backlog",
                "roadmap", "bugs_debt", "docs_adr", "memory", "search_ask", "ai_pm", "settings"
            ]
            for tab_name in sub_tabs:
                pv._switch_workspace_tab(tab_name)
                assert pv.active_workspace_tab == tab_name
                # Workspace body should have rendered child widgets
                assert len(pv.workspace_body.winfo_children()) >= 1

            # 6. Test 'Start with Agent'
            tasks = pv.project_service.db.list_tasks(proj.id)
            assert len(tasks) >= 1
            pv._start_task_with_agent(tasks[0].id)
            assert pv.active_workspace_tab == "coding"
            assert "GÖREV BAŞLATILDI" in pv.coding_chat_box.get("1.0", "end")

            assert len(streams) == 1
            from firik_agent.core.events import AgentEvent, AgentEventType
            call = streams[-1]
            call["on_event"](AgentEvent(session_id=call["session_id"], type=AgentEventType.TEXT_DELTA, content="Plan hazır."))
            call["on_event"](AgentEvent(session_id=call["session_id"], type=AgentEventType.DONE, content="Plan hazır."))
            call["on_done"]("Plan hazır.")
            assert pv.coding_chat_box.get("1.0", "end").count("Plan hazır.") == 1
            assert not pv._coding_state()["busy"]
            pv.coding_input.insert(0, "Devam et")
            app.update()
            pv.coding_input.focus_force()
            app.update()
            focused = app.focus_get() or getattr(pv.coding_input, "_entry", pv.coding_input)
            focused.event_generate("<Return>")
            app.update()
            assert len(streams) == 2
            assert pv.coding_input.get() == ""
            pv.coding_input.insert(0, "Bekleyen talimat")
            focused = app.focus_get() or getattr(pv.coding_input, "_entry", pv.coding_input)
            focused.event_generate("<Return>")
            app.update()
            assert len(streams) == 2
            assert pv.coding_input.get() == "Bekleyen talimat"
            assert streams[-1]["session_id"] == call["session_id"]
            pv._switch_workspace_tab("overview")
            streams[-1]["on_error"]("Bağlantı kesildi")
            pv._switch_workspace_tab("coding")
            assert "Bağlantı kesildi" in pv.coding_chat_box.get("1.0", "end")
            assert not pv._coding_state()["busy"]
            pv.coding_input.delete(0, "end")
            pv.coding_input.insert(0, "Sayısal Enter")
            pv.coding_input.focus_force()
            app.update()
            # Windows Tk maps both Enter keys to Return; KP_Enter has no native
            # keycode there, so generating it does not dispatch a key event.
            keypad_enter = "<Return>" if app.tk.call("tk", "windowingsystem") == "win32" else "<KP_Enter>"
            focused = app.focus_get() or getattr(pv.coding_input, "_entry", pv.coding_input)
            focused.event_generate(keypad_enter)
            app.update()
            assert len(streams) == 3
            assert streams[-1]["prompt"] == "Sayısal Enter"
            streams[-1]["on_done"]("Tamamlandı")
            for width in (940, 1120, 1600):
                app.geometry(f"{width}x760")
                app.update()
                app.update_idletasks()
                assert pv.coding_input.winfo_width() > 150
                assert pv.coding_send_btn.winfo_rootx() + pv.coding_send_btn.winfo_width() <= app.winfo_rootx() + app.winfo_width()
                for button in pv.tab_buttons.values():
                    assert button.winfo_rootx() + button.winfo_width() <= app.winfo_rootx() + app.winfo_width()

            # 7. Test PM Check action
            pv._run_pm_check_action()
            checks = pv.project_service.db.list_checks(proj.id)
            assert len(checks) >= 1

            # 8. Return to list screen
            pv.show_list_screen()
            assert pv.list_container.winfo_ismapped() or pv.list_container.winfo_manager() != ""

        finally:
            if hasattr(app, "service") and hasattr(app.service, "projects") and hasattr(app.service.projects, "scheduler"):
                app.service.projects.scheduler.stop()
            if app is not None:
                app.destroy()
            monkeypatch.chdir(original_cwd)


def test_coding_model_selection_persists_and_applies_to_existing_session(tmp_path, monkeypatch):
    from firik_agent.core.types import Message
    from firik_agent.projects.db import ProjectDatabase

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(FirikApp, "_check_initial_status", lambda self: None)
    model_callbacks = []
    monkeypatch.setattr(
        FirikService, "fetch_models_async",
        lambda self, on_success, **kwargs: model_callbacks.append(on_success),
    )
    app = FirikApp()
    streams = []
    monkeypatch.setattr(app.service, "chat_agent_stream_async", lambda **kwargs: streams.append(kwargs))
    try:
        app.show_view("projects")
        pv = app.views["projects"]
        first = pv.project_service.create_project("First", str(tmp_path / "first"))
        second = pv.project_service.create_project("Second", str(tmp_path / "second"))
        pv.show_workspace_screen(first.id)
        pv._switch_workspace_tab("coding")
        default_model = pv.project_service.db.get_settings(first.id).coding_model
        assert pv.coding_model_combo.get() == default_model
        model_callbacks[-1](["model-a", "model-b"])
        assert "model-a" in pv.coding_model_combo.cget("values")
        assert pv.coding_model_combo.get() == default_model

        def select_model(model):
            pv.coding_model_combo.set(model)
            pv.coding_model_combo.cget("command")(model)

        select_model("model-a")
        restored_db = ProjectDatabase(pv.project_service.db.db_path)
        assert restored_db.get_settings(first.id).coding_model == "model-a"
        assert restored_db.get_settings(second.id).coding_model == default_model
        session = pv._coding_state()["session"]
        session.messages.append(Message(role="user", content="Önceki talimat"))
        pv._run_coding_prompt("Devam et")
        assert session.model == "model-a"
        assert pv.coding_model_combo.cget("state") == "disabled"
        select_model("model-b")
        assert session.model == "model-a"
        assert restored_db.get_settings(first.id).coding_model == "model-a"
        assert pv.coding_model_combo.get() == "model-a"
        streams[-1]["on_done"]("Tamamlandı")
        assert pv.coding_model_combo.cget("state") == "readonly"
        select_model("model-b")
        pv._run_coding_prompt("Yeni modelle devam et")
        assert session.model == "model-b"
        assert streams[-1]["session_id"] == session.session_id
        assert session.messages[0].content == "Önceki talimat"
        streams[-1]["on_done"]("Tamamlandı")

        pending_models = model_callbacks[-1]
        pv.show_workspace_screen(second.id)
        pv._switch_workspace_tab("coding")
        pending_models(["outdated-model"])
        assert "outdated-model" not in pv.coding_model_combo.cget("values")
        assert pv.coding_model_combo.get() == default_model
        pv.show_workspace_screen(first.id)
        pv._switch_workspace_tab("coding")
        assert pv.coding_model_combo.get() == "model-b"
        model_callbacks[-1](["model-a"])
        assert "model-b" in pv.coding_model_combo.cget("values")
        assert pv.coding_model_combo.get() == "model-b"
        task = pv.project_service.db.list_tasks(first.id)[0]
        pv._start_task_with_agent(task.id)
        assert session.model == "model-b"
        streams[-1]["on_error"]("Bağlantı kesildi")
        assert pv.coding_model_combo.cget("state") == "readonly"

        for width in (940, 1120, 1600):
            app.geometry(f"{width}x760")
            app.update()
            app.update_idletasks()
            assert pv.coding_model_combo.winfo_width() > 100
            assert pv.coding_model_combo.winfo_rootx() + pv.coding_model_combo.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    finally:
        app.destroy()


def test_coding_changes_activity_preview_and_editor(tmp_path, monkeypatch):
    import customtkinter as ctk
    from firik_agent.core.events import AgentEvent, AgentEventType
    import firik_agent.ui.views.projects_view as projects_view

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(FirikApp, "_check_initial_status", lambda self: None)
    monkeypatch.setattr(FirikService, "fetch_models_async", lambda self, on_success, **kwargs: on_success([self.default_model]))
    opened = []
    monkeypatch.setattr(projects_view, "open_in_editor", lambda *args: opened.append(args))
    root = tmp_path / "code"
    root.mkdir()
    (root / "main.py").write_text("print('old')\n")
    (root / "remove.py").write_text("unused = True\n")
    (root / "prior_edit.py").write_text("preexisting = True\n")
    (root / "src").mkdir()
    (root / "src" / "nested.py").write_text("nested = True\n")
    app = FirikApp()
    streams = []
    monkeypatch.setattr(app.service, "chat_agent_stream_async", lambda **kwargs: streams.append(kwargs))
    try:
        app.show_view("projects")
        pv = app.views["projects"]
        project = pv.project_service.create_project("Coding", str(root))
        other = pv.project_service.create_project("Other", str(tmp_path / "other"))
        pv.show_workspace_screen(project.id)
        pv._switch_workspace_tab("coding")
        pv._on_coding_editor_selected("Cursor")
        pv.coding_open_project_btn.invoke()
        assert opened[-1][:2] == ("Cursor", root)
        assert "src/nested.py" in [w.cget("text") for w in pv.coding_files_list.winfo_children()]

        pv._run_coding_prompt("Dosyaları güncelle ve test et")
        call = streams[-1]
        emit = lambda event_type, **kwargs: call["on_event"](AgentEvent(type=event_type, session_id=call["session_id"], **kwargs))
        emit(AgentEventType.TOOL_CALL_STARTED, tool_name="write_file", arguments={"path": "main.py", "content": "print('new')\n"})
        # Assert the visible evidence card, independent of retained empty-state
        # widgets or the number of nested layout frames.
        card = next(value[1] for value in pv._coding_evidence_cache["activity"].values())
        pending = list(card.winfo_children())
        labels = []
        while pending:
            widget = pending.pop()
            if isinstance(widget, ctk.CTkLabel):
                labels.append(widget.cget("text"))
            pending.extend(widget.winfo_children())
        assert any("Çalışıyor" in text for text in labels)
        (root / "main.py").write_text("print('new')\n")
        (root / "new.py").write_text("new = True\n")
        (root / "remove.py").unlink()
        emit(AgentEventType.TOOL_CALL_RESULT, tool_name="write_file", content="Successfully wrote file", duration_ms=25)
        assert len(pv._coding_state()["changes"]) == 3
        emit(AgentEventType.TOOL_CALL_STARTED, tool_name="run_command", arguments={"command": "pytest", "cwd": str(root)})
        emit(AgentEventType.TOOL_CALL_RESULT, tool_name="run_command", content="Exit code 1:\n1 failed", duration_ms=1500)
        assert pv._coding_state()["activities"][-1]["status"] == "Hata"
        pv._show_coding_activity(pv._coding_state()["activities"][-1])
        dialog = [w for w in pv.winfo_children() if isinstance(w, ctk.CTkToplevel)][-1]
        assert "1 failed" in dialog.winfo_children()[0].get("1.0", "end")
        dialog.destroy()
        pv._switch_workspace_tab("overview")
        emit(AgentEventType.TEXT_DELTA, content="Dosyalar güncellendi.")
        emit(AgentEventType.DONE, content="Dosyalar güncellendi.")
        call["on_done"]("Dosyalar güncellendi.")
        pv._switch_workspace_tab("coding")
        state = pv._coding_state()
        assert not state["busy"]
        assert "1 araç hatası" in pv.coding_status_label.cget("text")
        assert {c["path"]: c["status"] for c in state["changes"]} == {"main.py": "modified", "new.py": "added", "remove.py": "deleted"}
        assert len(state["activities"]) == 2
        assert state["activities"][0]["duration_ms"] == 25
        pv.coding_tabs.set("Değişiklikler")
        pv._show_coding_change("main.py")
        diff = pv.coding_diff_box.get("1.0", "end")
        assert "-print('old')" in diff and "+print('new')" in diff
        assert pv.coding_diff_box._textbox.tag_ranges("added")
        assert pv.coding_diff_box._textbox.tag_ranges("removed")
        assert pv.coding_diff_box.cget("state") == "disabled"
        pv.coding_open_file_btn.invoke()
        assert opened[-1][:2] == ("Cursor", root / "main.py")
        pv._show_coding_change("remove.py")
        assert pv.coding_open_file_btn.cget("state") == "disabled"

        pv._preview_coding_file("src/nested.py")
        dialog = [w for w in pv.winfo_children() if isinstance(w, ctk.CTkToplevel)][-1]
        preview = next(w for w in dialog.winfo_children() if isinstance(w, ctk.CTkTextbox))
        assert "1  nested = True" in preview.get("1.0", "end")
        pv.show_workspace_screen(other.id)
        next(w for w in dialog.winfo_children() if isinstance(w, ctk.CTkButton)).invoke()
        assert opened[-1][:2] == ("Cursor", root / "src" / "nested.py")
        dialog.destroy()
        pv._switch_workspace_tab("coding")
        assert pv._coding_state()["changes"] == []
        pv.show_workspace_screen(project.id)
        pv._switch_workspace_tab("coding")
        assert len(pv._coding_state()["changes"]) == 3
        for width, height in ((940, 620), (1120, 760), (1600, 900)):
            app.geometry(f"{width}x{height}")
            app.update()
            for name in ("Sohbet", "Değişiklikler", "İşlemler", "Dosyalar"):
                pv.coding_tabs.set(name)
                app.update()
                if name == "Değişiklikler":
                    assert pv.coding_diff_box.winfo_height() >= 80, str({
                        name: (widget.winfo_width(), widget.winfo_height(), widget.winfo_reqheight())
                        for name, widget in {"window": app, "body": pv.workspace_body, "pane": pv.coding_mid,
                                             "tabs": pv.coding_tabs, "diff": pv.coding_diff_box,
                                             "summary": pv.coding_changes_summary, "list": pv.coding_changes_list}.items()})
                for widget in (pv.coding_open_project_btn, pv.coding_send_btn, pv.coding_diff_box):
                    # Inactive tabs retain their previous native geometry;
                    # bounds only describe the currently displayed controls.
                    if not widget.winfo_ismapped():
                        continue
                    assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width()
                    assert widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + app.winfo_height()
    finally:
        for widget in pv.winfo_children():
            if isinstance(widget, ctk.CTkToplevel):
                widget.destroy()
        app.destroy()
