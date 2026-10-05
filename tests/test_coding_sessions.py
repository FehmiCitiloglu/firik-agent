"""Real SQLite persistence, interruption and identity guarantees for coding runs."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest

from evren_agent.projects.coding_sessions import CodingSessionStore, REDACTED
from evren_agent.projects.db import ProjectDatabase
from evren_agent.projects.models import CodingSession, Project, Task
from evren_agent.core.events import AgentEvent, AgentEventType


@pytest.fixture
def store(tmp_path):
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p1", name="Birinci", local_path=str(tmp_path)))
    db.create_project(Project(id="p2", name="İkinci", local_path=str(tmp_path)))
    db.save_task(Task(id="t1", project_id="p1", title="Birinci görev"))
    db.save_task(Task(id="t2", project_id="p2", title="İkinci görev"))
    return CodingSessionStore(db)


def test_interrupted_work_reopens_without_executing_or_auto_resuming(store, tmp_path):
    source = tmp_path / "existing.py"
    source.write_text("print('kullanıcı değişikliği')", encoding="utf-8")
    run = store.create_run("p1", task_id="t1", session_id="conversation", model="selected", provider="local")
    store.update_run(run.id, status="applying", plan=[{"id": "step1", "status": "in_progress"}])
    store.record_message(run.id, "user", "Türkçe bir istek")
    store.record_decision(run.id, {"kind": "plan", "approved": True})
    store.record_file_change(run.id, {"path": "existing.py", "before_hash": "old", "after_hash": "new"})
    store.append_event(run.id, {"type": "tool_result", "project_id": "p1", "task_id": "t1", "session_id": "conversation"})

    reopened = CodingSessionStore(ProjectDatabase(store.db.db_path))
    # Even store construction is passive; the application performs startup recovery.
    assert reopened.get_run(run.id).status == "applying"
    recovered = reopened.recover_interrupted()
    assert len(recovered) == 1
    restored = reopened.get_run(run.id)
    assert restored.status == "interrupted"
    assert restored.messages[0]["content"] == "Türkçe bir istek"
    assert restored.decisions[0]["approved"] is True
    assert restored.file_journal[0]["after_hash"] == "new"
    assert restored.checkpoints[-1]["requires_revalidation"] is True
    assert restored.checkpoints[-1]["previous_status"] == "applying"
    assert json.loads(restored.plan)[0]["id"] == "step1"
    assert reopened.list_events(run.id)[0].task_id == "t1"
    assert reopened.recover_interrupted() == []
    assert source.read_text(encoding="utf-8") == "print('kullanıcı değişikliği')"
    with pytest.raises(ValueError, match="otomatik"):
        reopened.update_run(run.id, status="running")
    continuation = reopened.create_run("p1", task_id="t1", session_id="conversation", resumed_from=run.id)
    assert continuation.id != run.id
    assert continuation.resumed_from == run.id
    assert reopened.get_run(run.id).status == "interrupted"


def test_project_task_session_and_event_identity_are_enforced(store):
    with pytest.raises(ValueError, match="Görev"):
        store.create_run("p1", task_id="t2")
    with pytest.raises(ValueError, match="Görev"):
        store.create_run("p1", task_id="missing")
    run = store.create_run("p1", task_id="t1", session_id="c1")
    with pytest.raises(ValueError, match="Oturum"):
        store.create_run("p2", session_id="c1")
    with pytest.raises(ValueError, match="Oturum"):
        store.create_run("p1", task_id=None, session_id="c1")
    for field, wrong in [("project_id", "p2"), ("task_id", "t2"), ("session_id", "c2"), ("run_id", "other")]:
        with pytest.raises(ValueError, match="kimliği"):
            store.append_event(run.id, {"type": "text_delta", field: wrong})
        with pytest.raises(ValueError, match="kimliği"):
            store.append_event(run.id, {"type": "text_delta", "data": {field: wrong}})
    assert store.list_events(run.id) == []
    valid = store.append_event(run.id, {"type": "text_delta", "text": "yanıt"})
    assert valid.project_id == "p1" and valid.task_id == "t1"
    assert valid.session_id == "c1" and valid.run_id == run.id
    assert store.get_run(run.id, project_id="p2") is None
    assert store.list_events(run.id, project_id="p2") == []
    with pytest.raises(ValueError):
        store.export_run(run.id, project_id="p2")
    with pytest.raises(ValueError):
        store.update_run(run.id, project_id="p2")
    with pytest.raises(ValueError):
        store.record_message(run.id, "user", "wrong", task_id="t2")
    assert store.get_run(run.id).messages == []


def test_existing_agent_event_optional_identity_and_metadata_contract(store):
    run = store.create_run("p1", task_id="t1", session_id="event-conversation")
    event = AgentEvent(AgentEventType.TEXT_DELTA, "event-conversation", content="Merhaba",
                       metadata={"project_id": "p1", "task_id": "t1", "run_id": run.id})
    saved = store.append_event(run.id, event)
    assert saved.event_type == "text_delta"
    assert saved.data["content"] == "Merhaba"
    event.metadata["project_id"] = "p2"
    with pytest.raises(ValueError, match="kimliği"):
        store.append_event(run.id, event)


def test_runtime_boolean_permissions_and_paused_approval_states_survive_recovery(store):
    run = store.create_run("p1", permissions={"allow_edits": True, "allow_mcp": False, "git_push": "never"})
    assert run.permissions == {"allow_edits": True, "allow_mcp": False, "git_push": "never"}
    store.update_run(run.id, status="awaiting_approval")
    store.update_run(run.id, status="paused")
    assert store.recover_interrupted()[0].checkpoints[-1]["previous_status"] == "paused"


def test_latest_history_and_terminal_state_remain_project_scoped(store):
    first = store.create_run("p1", task_id="t1")
    second = store.create_run("p2", task_id="t2")
    third = store.create_run("p1")
    assert store.latest_run("p1").id == third.id
    assert store.latest_run("p1", task_id="t1").id == first.id
    assert store.latest_run("p2").id == second.id
    store.finish_run(first.id, "cancelled", error="Kullanıcı durdurdu")
    store.finish_run(second.id, "completed", summary="Bitti", usage={"input_tokens": 12, "output_tokens": 5})
    recovered = store.recover_interrupted(project_id="p1")
    assert [r.id for r in recovered] == [third.id]
    assert store.get_run(first.id).status == "cancelled"
    assert store.get_run(second.id).status == "completed"
    assert store.get_run(second.id).usage["input_tokens"] == 12
    assert len(store.db.list_sessions("p1")) == 2


def test_old_database_additive_upgrade_preserves_history_and_settings(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE coding_sessions (
                id TEXT PRIMARY KEY, project_id TEXT, task_id TEXT, feature_id TEXT,
                title TEXT, user_request TEXT, plan TEXT, agent_actions TEXT, files_changed TEXT,
                commands_executed TEXT, tests_executed TEXT, git_diff TEXT, result TEXT,
                summary TEXT, duration_sec INTEGER, commit_hash TEXT, tokens_used INTEGER,
                cost_est REAL, created_at TEXT
            );
            CREATE TABLE project_settings (
                project_id TEXT PRIMARY KEY, coding_model TEXT, pm_model TEXT, review_model TEXT,
                fast_model TEXT, autonomy_level TEXT, pm_check_interval TEXT, permissions TEXT,
                token_budget TEXT, max_checks_per_hour INTEGER, max_tokens_per_check INTEGER,
                min_change_threshold INTEGER
            );
            INSERT INTO coding_sessions(id, project_id, title, plan, summary, tokens_used, created_at)
                VALUES ('historic', 'p1', 'Eski oturum', 'Mevcut plan', 'Kaybolmaması gereken özet', 42, '2025-01-01');
            INSERT INTO project_settings(project_id, coding_model, autonomy_level, permissions)
                VALUES ('p1', 'user-choice', 'manage', '{"write_files":"never"}');
        """)
    upgraded = ProjectDatabase(path)
    upgraded.create_project(Project(id="p1", name="Mevcut proje"))
    history = upgraded.list_sessions("p1")
    assert history[0].summary == "Kaybolmaması gereken özet"
    assert history[0].plan == "Mevcut plan" and history[0].tokens_used == 42
    assert history[0].run_id == history[0].session_id == "historic"
    assert history[0].status == "completed"
    settings = upgraded.get_settings("p1")
    assert settings.coding_model == "user-choice"
    assert settings.permissions["write_files"] == "never"
    settings.coding_preferences = {"font_scale": 1.2, "panels": {"explorer": 250}, "diff_mode": "side_by_side"}
    upgraded.save_settings(settings)
    again = ProjectDatabase(path)
    assert again.get_settings("p1").coding_preferences == settings.coding_preferences
    assert again.get_settings("p1").coding_model == "user-choice"
    assert again.get_project("p1").name == "Mevcut proje"
    assert len(again.list_sessions("p1")) == 1


def test_session_updates_do_not_delete_persisted_event_children(store):
    run = store.create_run("p1")
    store.append_event(run.id, {"type": "tool_call", "tool": "read_file"})
    existing = store.get_run(run.id)
    existing.summary = "Yeni özet"
    store.db.save_session(existing)
    assert store.get_run(run.id).summary == "Yeni özet"
    assert len(store.list_events(run.id)) == 1


def test_real_sqlite_threads_and_multiple_store_instances_do_not_lose_events_or_messages(store):
    run = store.create_run("p1")
    other = CodingSessionStore(ProjectDatabase(store.db.db_path))

    def append(index):
        target = store if index % 2 else other
        target.append_event(run.id, {"type": "activity", "index": index})
        target.record_message(run.id, "assistant", f"Mesaj {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(32)))
    events = store.list_events(run.id)
    assert [e.sequence for e in events] == list(range(1, 33))
    assert {e.data["index"] for e in events} == set(range(32))
    assert len(store.get_run(run.id).messages) == 32


def test_credentials_are_redacted_before_sqlite_storage_and_export(store):
    known = "opaque-credential-detected-from-provider"
    store = CodingSessionStore(store.db, known_secrets=(known,))
    token = "sk-proj-12345678901234567890123456789"
    run = store.create_run("p1", user_request=f"EVREN_API_KEY=leaked-secret\nBearer {token}\n{known}")
    store.record_message(run.id, "assistant", "password='another-password'", details={"api_key": "mapping-secret"})
    store.append_event(run.id, {"type": "tool_result", "result": {"access_token": "nested-secret", "token": "plain-token-secret", "OPENAI_API_KEY": "env-key-secret"}, "text": known})
    store.record_file_change(run.id, {"path": "config.py", "before": "PASSWORD=source-secret", "after": "safe", "before_hash": "h1", "after_hash": "h2"})
    store.update_run(run.id, file_journal=[{"path": ".env", "before": "arbitrary value", "after": "hidden value"}])
    pem = "-----BEGIN PRIVATE KEY-----\nsensitive-private-bytes\n-----END PRIVATE KEY-----"
    store.checkpoint(run.id, {"result": pem})
    exported = store.export_json(run.id)
    for secret in [known, token, "leaked-secret", "another-password", "mapping-secret", "nested-secret",
                   "source-secret", "arbitrary value", "hidden value", "sensitive-private-bytes", "plain-token-secret", "env-key-secret"]:
        assert secret not in exported
        # Search actual SQLite rows, including event payloads, rather than only the returned object.
        with sqlite3.connect(store.db.db_path) as conn:
            raw = repr(conn.execute("SELECT * FROM coding_sessions").fetchall()) + repr(conn.execute("SELECT * FROM coding_run_events").fetchall())
        assert secret not in raw
    restored = store.get_run(run.id)
    assert restored.file_journal[0]["redacted"] is True
    assert restored.file_journal[0]["reversible"] is False
    assert restored.file_journal[0]["before"] == REDACTED


def test_legacy_session_save_redacts_source_credentials(store):
    record = CodingSession(id="legacy", project_id="p1", git_diff="+API_KEY='legacy-secret'", summary="password=summary-secret")
    store.db.save_session(record)
    with sqlite3.connect(store.db.db_path) as conn:
        raw = repr(conn.execute("SELECT * FROM coding_sessions WHERE id='legacy'").fetchone())
    assert "legacy-secret" not in raw and "summary-secret" not in raw


def test_test_results_never_claim_unexecuted_success(store):
    run = store.create_run("p1")
    with pytest.raises(ValueError, match="gerçek"):
        store.record_test(run.id, {"command": "pytest", "status": "passed"})
    store.record_test(run.id, {"command": "pytest", "cwd": "/workspace", "exit_code": 1, "status": "failed"})
    store.record_test(run.id, {"command": "pytest", "cwd": "/workspace", "exit_code": 0, "status": "passed"})
    assert [result["exit_code"] for result in store.get_run(run.id).tests_executed] == [1, 0]


def test_legacy_save_cannot_move_run_to_foreign_project_or_task(store):
    run = store.create_run("p1", task_id="t1")
    run.project_id = "p2"
    with pytest.raises(ValueError):
        store.db.save_session(run)
    assert store.get_run(run.id).project_id == "p1"
