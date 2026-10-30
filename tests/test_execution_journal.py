"""Real SQLite atomicity, process-death and journal identity guarantees."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from firik_agent.projects.coding_sessions import CodingSessionStore, REDACTED
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal, JournalConflict, JournalCorruptionError
from firik_agent.projects.models import Project


@pytest.fixture
def setup(tmp_path):
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Project", local_path=str(tmp_path)))
    store = CodingSessionStore(db)
    run = store.create_run("p", session_id="session")
    return db, store, run, ExecutionJournal(db)


def test_intent_and_terminal_are_atomic_with_existing_event_stream(setup):
    db, store, run, journal = setup
    intent = journal.begin(run.id, "tool:write_file", step_id="s", attempt_id="a", input_data={"path": "a.py"},
                           side_effect="conditional_write", permission_context={"allow_edits": True}, workspace_revision=3)
    assert intent["state"] == "ready" and intent["started_at"] is None
    assert intent["session_id"] == run.session_id and intent["project_id"] == "p"
    assert intent["workspace_revision"] == 3 and not intent["safe_to_retry"]
    journal.transition(run.id, "a", "dispatching")
    journal.transition(run.id, "a", "running")
    done = journal.transition(run.id, "a", "completed", result={"written": True})
    assert done["terminal_at"] and done["result_ref"]
    assert done["result"] == {"written": True}
    assert [e.data["state"] for e in store.list_events(run.id)] == ["ready", "dispatching", "running", "completed"]
    count = len(store.list_events(run.id))
    assert journal.transition(run.id, "a", "completed", result={"written": True}) == done
    assert len(store.list_events(run.id)) == count
    with pytest.raises(JournalConflict):
        journal.transition(run.id, "a", "completed", result={"written": False})
    with pytest.raises(JournalConflict):
        journal.begin(run.id, "tool:write_file", step_id="s", attempt_id="different")
    with pytest.raises(JournalConflict):
        journal.transition(run.id, "a", "running")


def test_idempotent_intents_reject_input_rebinding_and_foreign_owners(setup):
    db, store, run, journal = setup
    original = journal.begin(run.id, "model:chat", attempt_id="a", input_data={"q": 1}, side_effect="model_request")
    assert journal.begin(run.id, "model:chat", attempt_id="a", input_data={"q": 1}, side_effect="model_request") == original
    with pytest.raises(JournalConflict):
        journal.begin(run.id, "model:chat", attempt_id="a", input_data={"q": 2}, side_effect="model_request")
    other = store.create_run("p")
    assert journal.get(other.id, "a") is None
    with pytest.raises(JournalConflict):
        journal.begin(other.id, "model:chat", attempt_id="a", input_data={"q": 1})
    with pytest.raises(JournalConflict):
        journal.begin(other.id, "tool:read_file", parent_step_id=original["step_id"])
    with pytest.raises(JournalConflict):
        journal.transition(other.id, "a", "dispatching")


def test_only_one_unfinished_attempt_per_logical_step(setup):
    _, _, run, journal = setup
    journal.begin(run.id, "tool:read_file", step_id="same", attempt_id="a", side_effect="pure_read")
    with pytest.raises(JournalConflict):
        journal.begin(run.id, "tool:read_file", step_id="same", attempt_id="b", side_effect="pure_read")
    journal.transition(run.id, "a", "failed", error={"reason": "not_dispatched"})
    retry = journal.begin(run.id, "tool:read_file", step_id="same", attempt_id="b", side_effect="pure_read")
    assert retry["retry_safe"] and retry["safe_to_retry"]


def test_rollback_preserves_both_intent_and_events(setup):
    db, store, run, journal = setup
    def crash(boundary, context):
        if boundary == "journal.before_commit":
            raise RuntimeError("injected rollback")
    faulty = ExecutionJournal(db, fault_hook=crash)
    with pytest.raises(RuntimeError, match="injected"):
        faulty.begin(run.id, "tool:write_file", attempt_id="a")
    assert journal.list_steps(run.id) == [] and store.list_events(run.id) == []
    journal.begin(run.id, "tool:write_file", attempt_id="a")
    with pytest.raises(RuntimeError):
        faulty.transition(run.id, "a", "dispatching")
    assert journal.get(run.id, "a")["state"] == "ready"
    assert len(store.list_events(run.id)) == 1


@pytest.mark.parametrize("boundary,expected", [("journal.before_commit", None), ("journal.after_commit", "ready")])
def test_actual_process_termination_at_transaction_boundary(setup, boundary, expected):
    db, _, run, journal = setup
    code = """
import os, sys
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal
path, run_id, boundary = sys.argv[1:]
def crash(current, context):
    if current == boundary:
        os._exit(73)
ExecutionJournal(ProjectDatabase(path), fault_hook=crash).begin(run_id, 'tool:write_file', attempt_id='crashed')
"""
    process = subprocess.run([sys.executable, "-c", code, str(db.db_path), run.id, boundary], capture_output=True, timeout=15)
    assert process.returncode == 73, process.stderr.decode()
    reopened = ExecutionJournal(ProjectDatabase(db.db_path))
    row = reopened.get(run.id, "crashed")
    assert (row["state"] if row else None) == expected
    with sqlite3.connect(db.db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_side_effect_then_process_death_is_unknown_and_never_replayed(setup, tmp_path):
    db, _, run, journal = setup
    marker = tmp_path / "external-effect.txt"
    code = """
import os, sys
from pathlib import Path
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal
path, run_id, marker = sys.argv[1:]
journal = ExecutionJournal(ProjectDatabase(path))
journal.begin(run_id, 'tool:external', step_id='external', attempt_id='a', side_effect='external_side_effect')
journal.transition(run_id, 'a', 'dispatching')
Path(marker).write_text('executed once')
os._exit(74)
"""
    process = subprocess.run([sys.executable, "-c", code, str(db.db_path), run.id, str(marker)], capture_output=True, timeout=15)
    assert process.returncode == 74, process.stderr.decode()
    recovered = journal.recover_interrupted(run.id)
    assert recovered[0]["state"] == "outcome_unknown"
    assert recovered[0]["metadata"]["automatic_replay_permitted"] is False
    assert not recovered[0]["safe_to_retry"]
    assert journal.recover_interrupted(run.id) == []
    with pytest.raises(JournalConflict):
        journal.begin(run.id, "tool:external", step_id="external", side_effect="external_side_effect")
    with pytest.raises(JournalConflict):
        journal.transition(run.id, "a", "completed")
    with pytest.raises(JournalConflict):
        journal.reconcile(run.id, "a", "completed", evidence="")
    journal.reconcile(run.id, "a", "completed", evidence="Inspected external receipt", result={"observed": True})
    assert marker.read_text() == "executed once"


def test_all_interrupted_operation_types_remain_honestly_uncertain(setup):
    _, _, run, journal = setup
    for operation in ("model:chat", "tool:write_file", "tool:git_commit", "tool:run_quality_gate", "checkpoint:create", "child:run"):
        intent = journal.begin(run.id, operation, agent_id="child" if operation == "child:run" else "")
        journal.transition(run.id, intent["attempt_id"], "running" if False else "dispatching")
    waiting = journal.begin(run.id, "approval:request", metadata={"approved": False})
    interrupted = journal.recover_interrupted(run.id)
    assert len(interrupted) == 6
    assert {row["state"] for row in interrupted} == {"outcome_unknown"}
    assert journal.get(run.id, waiting["attempt_id"])["metadata"]["approved"] is False
    assert journal.get(run.id, waiting["attempt_id"])["state"] == "ready"


def test_multiple_store_instances_serialize_concurrent_run_activity(setup):
    db, store, run, journal = setup
    second_run = store.create_run("p")
    def execute(index):
        local = ExecutionJournal(ProjectDatabase(db.db_path))
        owner = run.id if index % 2 else second_run.id
        row = local.begin(owner, "tool:read_file", attempt_id=f"a{index}", side_effect="pure_read")
        local.transition(owner, row["attempt_id"], "dispatching")
        local.transition(owner, row["attempt_id"], "completed", result={"i": index})
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(execute, range(30)))
    assert len(journal.list_steps(run.id)) == len(journal.list_steps(second_run.id)) == 15
    for owner in (run.id, second_run.id):
        assert all(row["state"] == "completed" for row in journal.list_steps(owner))
        assert [e.sequence for e in store.list_events(owner)] == list(range(1, 46))


def test_secret_arguments_and_large_outputs_are_sanitized_and_bounded(setup):
    db, store, run, _ = setup
    journal = ExecutionJournal(db, known_secrets=("configured-value-123",))
    row = journal.begin(run.id, "tool:shell", input_data={"api_key": "raw-secret", "command": "TOKEN=shell-secret echo ok",
        "extra": "configured-value-123"}, permission_context={"allow_shell": True},
        metadata={"authorization": "Bearer private-auth", "detail": "password=metadata-secret"})
    journal.transition(run.id, row["attempt_id"], "dispatching")
    journal.transition(run.id, row["attempt_id"], "completed", result={"text": "x" * 100000, "token": "huge-secret"})
    conn = db._get_connection()
    try:
        raw = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM coding_execution_steps"))
    finally:
        conn.close()
    for secret in ("raw-secret", "shell-secret", "configured-value-123", "private-auth", "metadata-secret", "huge-secret"):
        assert secret not in raw
    assert len(raw) < 17000
    assert journal.get(run.id, row["attempt_id"])["result"]["truncated"] is True


def test_corrupted_state_fails_closed_instead_of_guessing(setup):
    db, _, run, journal = setup
    journal.begin(run.id, "tool:read_file", attempt_id="a")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute("UPDATE coding_execution_steps SET metadata='partial{' WHERE attempt_id='a'")
    with pytest.raises(JournalCorruptionError):
        journal.list_steps(run.id)
    with pytest.raises(JournalCorruptionError):
        journal.transition(run.id, "a", "dispatching")


def test_capacity_and_retention_protect_recoverable_outcomes(setup):
    db, store, run, journal = setup
    limited = ExecutionJournal(db, max_steps_per_run=1)
    limited.begin(run.id, "tool:read_file", attempt_id="a", side_effect="pure_read")
    with pytest.raises(JournalConflict, match="capacity"):
        limited.begin(run.id, "tool:read_file", attempt_id="b")
    journal.transition(run.id, "a", "dispatching")
    journal.transition(run.id, "a", "completed", result={"answer": "x" * 2000})
    assert journal.compact(run.id, keep_results=0) == 0
    store.finish_run(run.id, "completed")
    assert journal.compact(run.id, keep_results=0) == 1
    compacted = journal.get(run.id, "a")
    assert compacted["state"] == "completed" and compacted["result"]["compacted"]
    journal.transition(run.id, "a", "completed", result={"answer": "x" * 2000})
    assert len(store.list_events(run.id)) == 3


def test_versioned_additive_migration_preserves_historical_sessions(setup):
    db, store, run, _ = setup
    store.record_message(run.id, "user", "Historical message")
    # Representative v1 database: existing complete sessions/events, no v2 tables.
    with sqlite3.connect(db.db_path) as conn:
        for table in ("coding_execution_steps", "coding_resume_claims", "coding_run_owners"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DELETE FROM project_schema_migrations WHERE version=2")
    reopened_db = ProjectDatabase(db.db_path)
    reopened = CodingSessionStore(reopened_db)
    assert reopened.get_run(run.id).messages[0]["content"] == "Historical message"
    journal = ExecutionJournal(reopened_db)
    assert journal.list_steps(run.id) == []
    journal.begin(run.id, "tool:read_file")
    with sqlite3.connect(db.db_path) as conn:
        assert {row[0] for row in conn.execute("SELECT version FROM project_schema_migrations")} >= {1, 2}


def test_approval_can_atomically_enter_running_without_external_dispatch(setup):
    _, _, run, journal = setup
    journal.begin(run.id, "approval:write_file", attempt_id="approval", metadata={"pending": True})
    row = journal.transition(run.id, "approval", "running")
    assert row["started_at"]
    journal.transition(run.id, "approval", "completed", result={"allowed": False})
    assert journal.get(run.id, "approval")["result"] == {"allowed": False}


def test_workspace_safety_manifest_is_never_silently_truncated(setup):
    _, _, run, journal = setup
    manifest = {f"long/path/file_{i:04d}.py": "a" * 64 for i in range(1500)}
    journal.begin(run.id, "tool:write_file", attempt_id="manifest")
    journal.transition(run.id, "manifest", "dispatching")
    journal.transition(run.id, "manifest", "completed", metadata={"workspace_manifest": manifest})
    assert journal.get(run.id, "manifest")["metadata"]["workspace_manifest"] == manifest
    journal.begin(run.id, "tool:write_file", attempt_id="too_large")
    journal.transition(run.id, "too_large", "dispatching")
    with pytest.raises(JournalConflict, match="Safety metadata"):
        journal.transition(run.id, "too_large", "completed", metadata={"workspace_manifest": {"files": "x" * 1048577}})
    assert journal.get(run.id, "too_large")["state"] == "dispatching"


@pytest.mark.parametrize("column,value", [("version", -1), ("retry_safe", 1),
    ("input_fingerprint", "partial"), ("created_at", "")])
def test_partial_or_inconsistent_execution_metadata_blocks_dispatch(setup, column, value):
    db, _, run, journal = setup
    journal.begin(run.id, "tool:external", attempt_id="partial", side_effect="unknown")
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(f"UPDATE coding_execution_steps SET {column}=? WHERE attempt_id='partial'", (value,))
    with pytest.raises(JournalCorruptionError):
        journal.transition(run.id, "partial", "dispatching")
