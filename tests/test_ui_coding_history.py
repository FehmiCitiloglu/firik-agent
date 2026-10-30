"""Real Tk rendering and SQLite restart for the coding history workspace."""
from pathlib import Path
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from firik_agent.core.session import ChatSession
from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project, Task
from firik_agent.ui.coding_bridge import CodingBridge
from firik_agent.ui.coding_history import CodingHistoryMixin, restore_state_history


def test_subagent_record_reopens_as_one_passive_card(history):
    app, view, first, second = history
    view.service.coding_store.record_decision(first.id, {
        "kind": "subagent", "agent_id": "a1", "parent_run_id": first.id,
        "role": "review", "task": "main.py incele", "status": "completed",
        "elapsed_sec": 3, "result": "main.py:1 incelendi", "messages": []})
    state = view._coding_state()
    cards = [item for item in state["activities"] if item.get("agent_id") == "a1"]
    assert len(cards) == 1 and cards[0]["historical"]
    assert cards[0]["output"] == "main.py:1 incelendi" and cards[0]["duration_ms"] == 3000
    assert not state["busy"] and state["future"] is None


class HistoryView(CodingHistoryMixin, ctk.CTkFrame):
    def __init__(self, master, service):
        super().__init__(master)
        self.service, self.project_service = service, service.projects
        self.current_project_id, self.render_count = "p1", 0
        self._coding_states = {}

    def _coding_state(self):
        if self.current_project_id not in self._coding_states:
            session, record = self.service.restore_coding(self.current_project_id)
            state = {"session": session, "busy": False, "run_id": 0,
                     "preferences": {"font_size": 16, "left_width": 250}, "mode": "plan",
                     "permissions": {"allow_edits": False}, "context_paths": ["main.py"]}
            restore_state_history(state, record)
            self._coding_states[self.current_project_id] = state
        return self._coding_states[self.current_project_id]

    def _switch_workspace_tab(self, name):
        self.render_count += 1
        self.rendered_tab = name


@pytest.fixture
def history(tmp_path):
    from firik_agent.ui.app import _ensure_tk_environment
    _ensure_tk_environment()
    db = ProjectDatabase(tmp_path / "projects.db")
    for identifier in ("p1", "p2"):
        db.create_project(Project(id=identifier, name=identifier, local_path=str(tmp_path)))
        db.save_task(Task(id=f"t-{identifier}", project_id=identifier, title=f"Görev {identifier}"))
    store = CodingSessionStore(db)
    first = store.create_run("p1", task_id="t-p1", session_id="c1", model="saved-model", user_request="Türkçe isteğim")
    store.record_message(first.id, "user", "Türkçe isteğim")
    store.record_message(first.id, "assistant", "Türkçe yanıtım")
    store.update_run(first.id, plan=[{"id": "s1", "title": "Kaydı incele", "status": "in_progress"}],
                     context_report=[{"key": "task", "display_name": "Görev", "has_content": True, "tokens_used": 25}],
                     usage={"total_tokens": 42})
    store.record_file_change(first.id, {"path": "main.py", "kind": "modified", "before_hash": "a", "after_hash": "b",
        "diff": "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-eski\n+yeni\n"})
    store.record_test(first.id, {"command": "pytest", "executed": True, "exit_code": 0, "output": "1 passed"})
    store.record_test(first.id, {"command": "lint", "executed": True, "exit_code": 2, "output": "failed"})
    store.record_test(first.id, {"command": "build", "executed": False, "output": "SDK yok"})
    store.record_decision(first.id, {"kind": "plan", "approved": True})
    second = store.create_run("p2", task_id="t-p2", session_id="c2", user_request="Diğer projenin kaydı")
    store.finish_run(second.id, summary="İkinci proje")
    reopened = CodingSessionStore(ProjectDatabase(db.db_path))
    reopened.recover_interrupted()
    service = CodingBridge()
    service.coding_store = reopened
    service.projects = SimpleNamespace(db=reopened.db)
    service.sessions, service._coding_filters = {}, {}
    service.default_model = "default"
    service._coding_known_secrets = ()
    app = ctk.CTk()
    app.geometry("800x600")
    view = HistoryView(app, service)
    view.pack(fill="both", expand=True)
    try:
        yield app, view, first, second
    finally:
        app.destroy()


def test_restart_restores_transcript_plan_context_usage_diff_tests_decisions_and_interruption(history):
    app, view, first, second = history
    state = view._coding_state()
    assert state["session"].model == "saved-model"
    assert state["text"] == "Türkçe isteğim\n\nTürkçe yanıtım"
    assert state["task_id"] == "t-p1" and state["plan"][0]["id"] == "s1"
    assert state["context_report"][0]["tokens_used"] == 25
    assert state["usage"]["total_tokens"] == 42
    assert state["changes"][0]["added"] == state["changes"][0]["removed"] == 1
    assert "-eski" in state["changes"][0]["diff"]
    assert state["changes"][0]["origin"] == "Ajan (kalıcı kayıt)"
    assert not state["changes"][0]["can_undo"]
    assert state["agent_paths"]["main.py"]["after_hash"] == "b"
    assert state["test_counts"] == {"passed": 1, "failed": 1, "unexecuted": 1}
    assert [a["status"] for a in state["activities"][:3]] == ["Tamamlandı", "Hata", "Çalıştırılmadı"]
    assert "Gerçek çıkış kodu: 2" in state["activities"][1]["output"]
    assert any(a["status"] == "Onaylandı" for a in state["activities"])
    assert any(a["status"] == "Kesildi" for a in state["activities"])
    assert "yeniden doğrulanacak" in state["inspection_warning"]
    assert not state["pending_approvals"] and not state["busy"]


def test_real_history_dialog_is_project_scoped_and_open_is_passive(history):
    app, view, first, second = history
    dialog = view._coding_sessions()
    app.update()
    def all_children(widget):
        yield widget
        for child in widget.winfo_children():
            yield from all_children(child)
    texts = [widget.cget("text") for widget in all_children(dialog) if isinstance(widget, ctk.CTkLabel)]
    assert any("Türkçe isteğim" in text for text in texts)
    assert not any("Diğer projenin kaydı" in text for text in texts)
    buttons = [widget for widget in all_children(dialog) if isinstance(widget, ctk.CTkButton) and widget.cget("text") == "Kaydı Aç"]
    assert len(buttons) == 1
    buttons[0].invoke()
    app.update()
    assert view.render_count == 1 and view._coding_state()["record"].id == first.id
    assert not view._coding_state()["busy"]
    assert view.service.coding_store.get_run(first.id).status == "interrupted"
    with pytest.raises(ValueError):
        view._load_coding_history(second.id)
    assert view._coding_state()["record"].id == first.id


def test_explicit_new_session_keeps_preferences_and_clears_task_and_history_only(history):
    app, view, first, second = history
    state = view._coding_state()
    old_session = state["session"].session_id
    preferences = dict(state["preferences"])
    view._new_coding_session()
    assert state["session"].session_id != old_session
    assert state["preferences"] == preferences and state["permissions"]["allow_edits"] is False
    assert state["task_id"] is None and not state["plan"] and not state["changes"] and not state["activities"]
    assert state["record"] is None and state["status"] == "Hazır"
    assert view.service.coding_store.get_run(first.id).status == "interrupted"
    assert len(view.service.coding_store.list_runs("p1")) == 1
    view.current_project_id = "p2"
    assert view._coding_state()["record"].id == second.id
    assert "İkinci proje" in view._coding_state()["text"]


def test_history_undo_requires_same_live_run_filter(history):
    app, view, first, second = history
    view._coding_state()
    view.service._coding_filters["c1"] = SimpleNamespace(control=SimpleNamespace(run_id="different-run"))
    view._load_coding_history(first.id)
    assert not view._coding_state()["changes"][0]["can_undo"]
    view.service._coding_filters["c1"] = SimpleNamespace(control=SimpleNamespace(run_id=first.id))
    view._load_coding_history(first.id)
    assert view._coding_state()["changes"][0]["can_undo"]
