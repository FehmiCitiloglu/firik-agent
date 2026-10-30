"""Recovery is explicit, scoped, conservative and never replays external work."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal
from firik_agent.projects.models import Project
from firik_agent.projects.resume import DurableResumeController, capture_workspace, narrow_permissions


@pytest.fixture
def recovery(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("# existing dirty user work\n")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p1", name="project", local_path=str(root)))
    db.create_project(Project(id="p2", name="other", local_path=str(root)))
    controller = DurableResumeController(db)
    run = controller.store.create_run("p1", permissions={"allow_edits": True, "allow_terminal": False,
                                                      "filesystem.write": "ask", "git.push": "deny"})
    controller.record_workspace(run.id, root)
    return controller, run, root


def interrupted(controller, run):
    controller.store.recover_interrupted()
    return controller.inspect(run.id)


def test_passive_open_and_inspection_do_not_execute_or_replay(recovery):
    controller, run, root = recovery
    intent = controller.journal.begin(run.id, "shell:publish", input_data={"command": "publish"}, side_effect="external_side_effect")
    controller.journal.transition(run.id, intent["attempt_id"], "dispatching")
    controller.store.recover_interrupted()
    reopened = DurableResumeController(ProjectDatabase(controller.db.db_path))
    report = reopened.inspect(run.id)
    assert report["uncertain_operations"][0]["state"] == "outcome_unknown"
    assert not report["resume_eligible"]
    assert root.joinpath("main.py").read_text() == "# existing dirty user work\n"
    assert reopened.journal.get(run.id, intent["attempt_id"])["state"] == "outcome_unknown"


def test_resume_requires_explicit_action_and_preserves_completed_plan(recovery):
    controller, run, _ = recovery
    controller.store.update_run(run.id, plan=json.dumps([{"id": "done", "status": "completed"}]),
                                completed_steps=[{"id": "done", "status": "completed"}])
    interrupted(controller, run)
    with pytest.raises(ValueError, match="onayı"):
        controller.prepare_resume(run.id, current_permissions=run.permissions)
    prepared = controller.prepare_resume(run.id, current_permissions=run.permissions, approved=True)
    assert prepared.status == "queued"
    assert json.loads(prepared.plan)[0]["status"] == "completed"
    assert prepared.completed_steps == [{"id": "done", "status": "completed"}]
    assert prepared.tests_executed == []
    assert controller.inspect(prepared.id)["prepared_continuation"]
    assert controller.inspect(prepared.id)["resume_eligible"]


def test_repeated_and_concurrent_resume_create_exactly_one_prepared_run(recovery):
    controller, run, _ = recovery
    interrupted(controller, run)
    def prepare(_):
        other = DurableResumeController(ProjectDatabase(controller.db.db_path))
        return other.prepare_resume(run.id, current_permissions=run.permissions, approved=True).id
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(prepare, range(8)))
    assert len(set(ids)) == 1
    assert len(controller.store.list_runs("p1")) == 2
    controller.store.update_run(ids[0], status="running")
    with pytest.raises(ValueError, match="kullanıldı"):
        prepare(0)


def test_existing_prepared_run_is_narrowed_against_current_policy(recovery):
    controller, run, _ = recovery
    interrupted(controller, run)
    first = controller.prepare_resume(run.id, current_permissions=run.permissions, approved=True)
    stricter = dict(run.permissions, allow_edits=False)
    again = controller.prepare_resume(run.id, current_permissions=stricter, approved=True)
    assert first.id == again.id
    assert again.permissions["allow_edits"] is False
    assert controller.store.get_run(first.id).permissions["allow_edits"] is False
    assert again.permissions["allow_terminal"] is False
    assert narrow_permissions({"git.push": "deny"}, {"git.push": "allow"})["git.push"] == "deny"


@pytest.mark.parametrize("change,reason", [("modified", "file_modified"), ("removed", "file_missing"), ("added", "file_added")])
def test_workspace_detects_external_edits_missing_files_and_additions(recovery, change, reason):
    controller, run, root = recovery
    interrupted(controller, run)
    if change == "modified":
        (root / "main.py").write_text("user changed this after interruption\n")
    elif change == "removed":
        (root / "main.py").unlink()
    else:
        (root / "new.py").write_text("new user file\n")
    report = controller.inspect(run.id)
    assert any(item["reason"] == reason for item in report["workspace_conflicts"])
    assert not report["resume_eligible"]
    with pytest.raises(ValueError, match="önkoşulları"):
        controller.prepare_resume(run.id, current_permissions=run.permissions, approved=True)


def test_git_head_index_and_preexisting_dirty_changes_are_validated(recovery):
    controller, run, root = recovery
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    controller.record_workspace(run.id, root)
    interrupted(controller, run)
    assert controller.inspect(run.id)["resume_eligible"]
    subprocess.run(["git", "-C", str(root), "add", "main.py"], check=True)
    report = controller.inspect(run.id)
    assert any(item["reason"] == "git_state_changed" for item in report["workspace_conflicts"])
    assert (root / "main.py").read_text() == "# existing dirty user work\n"


def test_live_concurrent_executor_is_not_marked_interrupted(recovery):
    controller, run, _ = recovery
    token = controller.store.claim_owner(run.id)
    other = CodingSessionStore(ProjectDatabase(controller.db.db_path))
    assert other.recover_interrupted() == []
    assert other.get_run(run.id).status == "running"
    assert controller.inspect(run.id)["live_owner"] is True
    with pytest.raises(ValueError, match="etkin"):
        other.claim_owner(run.id)
    controller.store.release_owner(run.id, token)
    assert len(other.recover_interrupted()) == 1


def test_failed_run_with_uncommitted_tool_outcome_recovers_journal(recovery):
    controller, run, _ = recovery
    intent = controller.journal.begin(run.id, "tool:publish", side_effect="external_side_effect")
    controller.journal.transition(run.id, intent["attempt_id"], "running")
    controller.store.finish_run(run.id, "failed")
    controller.store.recover_interrupted()
    assert controller.journal.get(run.id, intent["attempt_id"])["state"] == "outcome_unknown"
    assert controller.inspect(run.id)["lifecycle_state"] == "needs_reconciliation"


def test_pending_approval_survives_restart_without_becoming_a_grant(recovery):
    controller, run, _ = recovery
    pending = controller.journal.begin(run.id, "approval:write_file", metadata={"kind": "approval", "pending": True})
    controller.store.update_run(run.id, status="awaiting_approval")
    interrupted(controller, run)
    report = controller.inspect(run.id)
    assert report["pending_approvals"][0]["attempt_id"] == pending["attempt_id"]
    prepared = controller.prepare_resume(run.id, current_permissions=run.permissions, approved=True)
    assert all(item.get("approved") is not True for item in prepared.decisions)
    assert prepared.decisions[-1]["pending_approvals_require_fresh_decision"] is True
    assert controller.journal.get(run.id, pending["attempt_id"])["state"] == "ready"


def test_uncertain_child_requires_reconciliation_and_completed_delivery_is_not_duplicated(recovery):
    controller, run, _ = recovery
    child = controller.journal.begin(run.id, "child:worker", agent_id="child_1", metadata={"kind": "child"})
    controller.journal.transition(run.id, child["attempt_id"], "running")
    interrupted(controller, run)
    report = controller.inspect(run.id)
    assert report["children"][0]["agent_id"] == "child_1"
    with pytest.raises(ValueError):
        controller.reconcile(run.id, child["attempt_id"], "completed", evidence="")
    controller.reconcile(run.id, child["attempt_id"], "completed", evidence="Inspected child patch already present")
    prepared = controller.prepare_resume(run.id, current_permissions=run.permissions, approved=True)
    assert controller.journal.list_steps(prepared.id) == []
    assert controller.journal.get(run.id, child["attempt_id"])["state"] == "completed"


def test_model_projection_loss_blocks_resume_until_explicit_acknowledgement(recovery):
    controller, run, _ = recovery
    model = controller.journal.begin(run.id, "model:local", metadata={"kind": "model"})
    controller.journal.transition(run.id, model["attempt_id"], "dispatching")
    controller.journal.transition(run.id, model["attempt_id"], "completed", metadata={"projection_required": True})
    interrupted(controller, run)
    assert "model_output_projection_unconfirmed" in controller.inspect(run.id)["resume_blockers"]
    controller.reconcile(run.id, model["attempt_id"], "completed", evidence="Reviewed durable response; acknowledged missing transcript")
    assert controller.inspect(run.id)["resume_eligible"]


def test_saved_model_projection_is_recognized(recovery):
    controller, run, _ = recovery
    model = controller.journal.begin(run.id, "model:local", metadata={"kind": "model"})
    controller.journal.transition(run.id, model["attempt_id"], "dispatching")
    controller.journal.transition(run.id, model["attempt_id"], "completed", metadata={"projection_required": True})
    controller.store.record_message(run.id, {"role": "assistant", "content": "saved response",
                                           "execution": {"durable_request_id": model["attempt_id"]}})
    interrupted(controller, run)
    assert controller.inspect(run.id)["resume_eligible"]


def test_missing_checkpoint_is_blocking_and_restore_preserves_unrelated_dirty_file(recovery):
    controller, run, root = recovery
    checkpoint = controller.checkpoints.create_checkpoint("p1", root, run_id=run.id)
    controller.store.checkpoint(run.id, checkpoint)
    (root / "main.py").write_text("agent change\n")
    (root / "unrelated.py").write_text("user only\n")
    comparison = controller.checkpoints.compare_checkpoint(checkpoint["id"], project_id="p1", root=root, run_id=run.id)
    target = next(item for item in comparison["changes"] if item["path"] == "main.py")
    controller.checkpoints.restore_checkpoint(checkpoint["id"], project_id="p1", root=root, run_id=run.id,
                                             expected_hashes={"main.py": target["after_hash"]}, paths=["main.py"])
    assert (root / "unrelated.py").read_text() == "user only\n"
    with sqlite3.connect(controller.db.db_path) as conn:
        conn.execute("DELETE FROM coding_checkpoints WHERE id=?", (checkpoint["id"],))
    interrupted(controller, run)
    assert any(item["reason"] == "missing_checkpoint" for item in controller.inspect(run.id)["workspace_conflicts"])


def test_wrong_project_and_changed_root_are_rejected(recovery, tmp_path):
    controller, run, _ = recovery
    interrupted(controller, run)
    with pytest.raises(ValueError, match="projeye"):
        controller.inspect(run.id, project_id="p2")
    project = controller.db.get_project("p1")
    replacement = tmp_path / "elsewhere"
    replacement.mkdir()
    project.local_path = str(replacement)
    controller.db.update_project(project)
    assert any(item["reason"] == "project_path_changed" for item in controller.inspect(run.id)["workspace_conflicts"])


def test_secret_arguments_and_manifest_contents_are_never_exported(recovery):
    controller, run, root = recovery
    (root / ".env").write_text("PASSWORD=do-not-export\n")
    controller.record_workspace(run.id, root)
    controller.journal.begin(run.id, "tool:api", input_data={"api_key": "hidden-credential", "command": "TOKEN=raw-command-secret"})
    interrupted(controller, run)
    exported = json.dumps(controller.export_run(run.id))
    for secret in ("do-not-export", "hidden-credential", "raw-command-secret"):
        assert secret not in exported


def test_partial_capture_and_corrupted_manifest_fail_closed(recovery):
    controller, run, root = recovery
    assert capture_workspace(root, max_files=0)["complete"] is False
    checkpoints = controller.store.get_run(run.id).checkpoints
    checkpoints[-1]["workspace_manifest"]["files"]["main.py"]["hash"] = "corrupted"
    controller.store.update_run(run.id, checkpoints=checkpoints)
    interrupted(controller, run)
    assert any(item["reason"] == "workspace_manifest_corrupt" for item in controller.inspect(run.id)["workspace_conflicts"])


def test_workspace_reconciliation_is_explicit_and_invalidates_old_verification(recovery):
    controller, run, root = recovery
    interrupted(controller, run)
    (root / "main.py").write_text("accepted user edit\n")
    with pytest.raises(ValueError):
        controller.reconcile_workspace(run.id, evidence="reviewed")
    controller.reconcile_workspace(run.id, evidence="Reviewed user edit and current Git state", approved=True)
    assert controller.inspect(run.id)["resume_eligible"]
    assert controller.store.get_run(run.id).decisions[-1]["verification_invalidated"]


def test_cancel_preserves_uncertainty_and_does_not_restore_files(recovery):
    controller, run, root = recovery
    effect = controller.journal.begin(run.id, "tool:mutate")
    controller.journal.transition(run.id, effect["attempt_id"], "dispatching")
    interrupted(controller, run)
    controller.cancel(run.id)
    assert controller.journal.get(run.id, effect["attempt_id"])["state"] == "outcome_unknown"
    assert (root / "main.py").read_text() == "# existing dirty user work\n"


def test_cli_prepared_continuation_survives_startup_without_execution(recovery):
    controller, run, _ = recovery
    interrupted(controller, run)
    prepared = controller.prepare_resume(run.id, current_permissions=run.permissions, approved=True)
    restarted = DurableResumeController(ProjectDatabase(controller.db.db_path))
    assert restarted.store.recover_interrupted() == []
    assert restarted.store.get_run(prepared.id).status == "queued"
    assert restarted.inspect(prepared.id)["resume_eligible"]
    assert restarted.journal.list_steps(prepared.id) == []


def test_claimed_queued_continuation_with_dead_owner_is_interrupted(recovery):
    controller, run, _ = recovery
    interrupted(controller, run)
    prepared = controller.prepare_resume(run.id, current_permissions=run.permissions, approved=True)
    controller.store.claim_owner(prepared.id)
    with sqlite3.connect(controller.db.db_path) as conn:
        conn.execute("UPDATE coding_run_owners SET owner_pid=? WHERE run_id=?", (2_147_483_647, prepared.id))
    assert controller.store.recover_interrupted()[0].id == prepared.id
    assert controller.store.get_run(prepared.id).status == "interrupted"


def test_process_termination_releases_ownership_for_safe_startup_recovery(recovery):
    import sys
    controller, run, _ = recovery
    script = """
import sys, time
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.coding_sessions import CodingSessionStore
store = CodingSessionStore(ProjectDatabase(sys.argv[1]))
store.claim_owner(sys.argv[2])
print('owner-claimed', flush=True)
time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(controller.db.db_path), run.id],
                               stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "owner-claimed"
        assert controller.store.is_live(run.id)
        assert controller.store.recover_interrupted() == []
        process.kill()
        process.wait(timeout=5)
        recovered = controller.store.recover_interrupted()
        assert recovered[0].id == run.id
        assert controller.store.get_run(run.id).status == "interrupted"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_child_model_projection_is_recovered_from_persisted_child_messages(recovery):
    controller, run, _ = recovery
    model = controller.journal.begin(run.id, "model:child", metadata={"kind": "model"}, agent_id="child_1")
    controller.journal.transition(run.id, model["attempt_id"], "dispatching")
    controller.journal.transition(run.id, model["attempt_id"], "completed", metadata={"projection_required": True})
    controller.store.record_decision(run.id, {"kind": "subagent", "agent_id": "child_1", "messages": [
        {"role": "assistant", "content": "child output", "execution": {"durable_request_id": model["attempt_id"]}}]})
    interrupted(controller, run)
    assert controller.inspect(run.id)["resume_eligible"]


def _complete_inherited_effect(controller, run, *, arguments='{"command":"publish"}', result=None, metadata=None):
    effect = controller.journal.begin(run.id, "tool:run_command", input_data=arguments,
        side_effect="external_side_effect", metadata=metadata)
    controller.journal.transition(run.id, effect["attempt_id"], "dispatching")
    return controller.journal.transition(run.id, effect["attempt_id"], "completed",
        result=result or {"content": "published", "is_error": False})


def test_inherited_completed_effect_is_found_without_any_persisted_messages(recovery):
    controller, run, _ = recovery
    effect = _complete_inherited_effect(controller, run)
    assert controller.store.get_run(run.id).messages == []
    interrupted(controller, run)
    continued = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    found = controller.inherited_completed_tool(continued.id, "run_command", '{"command":"publish"}')
    assert found["attempt_id"] == effect["attempt_id"]
    assert found["result"]["content"] == "published"
    assert controller.inherited_completed_tool(run.id, "run_command", '{"command":"publish"}') is None
    assert controller.inherited_completed_tool(continued.id, "run_command", '{"command":"other"}') is None


def test_inherited_effect_lookup_normalizes_json_and_follows_full_source_chain(recovery):
    from firik_agent.core.recovery import operation_fingerprint
    controller, run, _ = recovery
    arguments = '{ "cwd": ".", "command": "publish" }'
    effect = _complete_inherited_effect(controller, run, arguments=arguments,
        metadata={"operation_fingerprint": operation_fingerprint("run_command", arguments)})
    interrupted(controller, run)
    middle = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    controller.store.update_run(middle.id, status="running")
    controller.store.finish_run(middle.id, "interrupted")
    newest = controller.prepare_resume(middle.id, approved=True, current_permissions=run.permissions)
    found = controller.inherited_completed_tool(newest.id, "run_command", '{"command":"publish","cwd":"."}')
    assert found["attempt_id"] == effect["attempt_id"]


def test_legacy_inherited_effect_normalization_uses_only_complete_unredacted_summary(recovery):
    controller, run, _ = recovery
    effect = _complete_inherited_effect(controller, run, arguments='{ "command": "publish", "cwd": "." }')
    interrupted(controller, run)
    continued = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    assert controller.inherited_completed_tool(continued.id, "run_command", '{"cwd":".","command":"publish"}')["attempt_id"] == effect["attempt_id"]


def test_inherited_lookup_preserves_completed_identity_when_result_is_truncated(recovery):
    controller, run, _ = recovery
    effect = _complete_inherited_effect(controller, run, result={"content": "x" * 20000})
    interrupted(controller, run)
    continued = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    found = controller.inherited_completed_tool(continued.id, "run_command", '{"command":"publish"}')
    assert found["attempt_id"] == effect["attempt_id"]
    assert found["result"]["truncated"] is True


def test_inherited_lookup_rejects_cross_session_corruption_and_cycles(recovery):
    controller, run, _ = recovery
    _complete_inherited_effect(controller, run)
    interrupted(controller, run)
    continued = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    with sqlite3.connect(controller.db.db_path) as conn:
        conn.execute("UPDATE coding_sessions SET session_id='foreign-session' WHERE id=?", (run.id,))
    with pytest.raises(ValueError, match="kimliği"):
        controller.inherited_completed_tool(continued.id, "run_command", '{"command":"publish"}')
    with sqlite3.connect(controller.db.db_path) as conn:
        conn.execute("UPDATE coding_sessions SET session_id=?,resumed_from=? WHERE id=?",
                     (continued.session_id, continued.id, run.id))
    with pytest.raises(ValueError, match="zinciri"):
        controller.inherited_completed_tool(continued.id, "run_command", '{"command":"publish"}')


def test_committed_child_final_outcome_projects_undelivered_child_model(recovery):
    controller, run, _ = recovery
    child = controller.journal.begin(run.id, "child:worker", step_id="child_1", attempt_id="child_1",
                                    metadata={"kind": "child", "agent_id": "child_1"})
    controller.journal.transition(run.id, child["attempt_id"], "running")
    model = controller.journal.begin(run.id, "model:child", parent_step_id="child_1", agent_id="child_1",
                                    metadata={"kind": "model"})
    controller.journal.transition(run.id, model["attempt_id"], "running")
    controller.journal.transition(run.id, model["attempt_id"], "completed", metadata={"projection_required": True})
    controller.journal.transition(run.id, child["attempt_id"], "completed",
        result={"status": "completed", "result": "Committed final child answer", "artifacts": []})
    interrupted(controller, run)
    report = controller.inspect(run.id)
    assert report["unprojected_model_outputs"] == []
    assert report["resume_eligible"]
    assert report["children"][0]["delivery_state"] == "persisted_not_delivered"
    continued = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    found = controller.inherited_child_outcome(continued.id, "child_1")
    assert found["result"]["result"] == "Committed final child answer"
    assert controller.inherited_child_outcome(run.id, "child_1") is None
    assert controller.inherited_child_outcome(continued.id, "missing-child") is None


def test_truncated_child_outcome_cannot_replace_missing_model_projection(recovery):
    controller, run, _ = recovery
    child = controller.journal.begin(run.id, "child:worker", step_id="child_1", attempt_id="child_1",
                                    metadata={"kind": "child", "agent_id": "child_1"})
    controller.journal.transition(run.id, child["attempt_id"], "running")
    model = controller.journal.begin(run.id, "model:child", parent_step_id="child_1", agent_id="child_1",
                                    metadata={"kind": "model"})
    controller.journal.transition(run.id, model["attempt_id"], "running")
    controller.journal.transition(run.id, model["attempt_id"], "completed", metadata={"projection_required": True})
    controller.journal.transition(run.id, child["attempt_id"], "completed",
                                  result={"status": "completed", "result": "x" * 20000})
    interrupted(controller, run)
    assert "model_output_projection_unconfirmed" in controller.inspect(run.id)["resume_blockers"]
    controller.reconcile(run.id, model["attempt_id"], "completed", evidence="Acknowledged missing child intermediate output")
    continued = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    assert controller.inherited_child_outcome(continued.id, "child_1")["result"]["truncated"] is True


def test_inherited_child_uses_same_isolation_checks_as_tool_outcomes(recovery):
    controller, run, _ = recovery
    child = controller.journal.begin(run.id, "child:worker", attempt_id="child_1", metadata={"kind": "child", "agent_id": "child_1"})
    controller.journal.transition(run.id, child["attempt_id"], "running")
    controller.journal.transition(run.id, child["attempt_id"], "failed", result={"result": "known failure"})
    interrupted(controller, run)
    continued = controller.prepare_resume(run.id, approved=True, current_permissions=run.permissions)
    assert controller.inherited_child_outcome(continued.id, "child_1")["state"] == "failed"
    with sqlite3.connect(controller.db.db_path) as conn:
        conn.execute("UPDATE coding_sessions SET session_id='foreign-session' WHERE id=?", (run.id,))
    with pytest.raises(ValueError, match="kimliği"):
        controller.inherited_child_outcome(continued.id, "child_1")
