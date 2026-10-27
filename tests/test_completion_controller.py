"""Completion relies on host execution evidence and live workspace identity."""
import json

import pytest

from firik_agent.core.completion import CompletionController
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.types import ToolCall
from firik_agent.core.types import Message
from firik_agent.core.types import ToolDefinition
from firik_agent.core.tools import ToolRegistry
from firik_agent.projects.cockpit import CockpitState
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter, repair_tool_history
from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project


def evidence(**overrides):
    return {"evidence_id": "e1", "kind": "test", "command": "pytest", "cwd": ".",
            "executed": True, "passed": True, "fresh": True, "status": "passed", "exit_code": 0,
            "workspace_hash": "current", "workspace_revision": 2, **overrides}


def evaluate(controller=None, **overrides):
    arguments = {"mode": "assisted", "plan": [], "verifications": [evidence()], "quality_gates": [],
                 "quality_results": [], "verified_ids": {"e1"}, "workspace_revision": 2,
                 "workspace_hash": "current", "workspace_complete": True, "changes": True}
    arguments.update(overrides)
    return (controller or CompletionController()).evaluate(**arguments)


def test_current_host_execution_evidence_is_verified_and_no_prose_can_replace_it():
    step = {"title": "Fix", "status": "completed", "acceptance_criteria": ["Works"],
            "verification": "pytest", "evidence_ids": ["e1"]}
    assert evaluate(plan=[step])["completion_classification"] == "completed_verified"
    assert evaluate()["completion_classification"] == "completed_unverified"
    assert evaluate(verifications=[], verified_ids=set())["completion_classification"] == "blocked"
    result = evaluate(verifications=[], verified_ids=set(), changes=False)
    assert result["completion_classification"] == "completed_unverified"


@pytest.mark.parametrize("override", [
    {"workspace_hash": "old"}, {"workspace_revision": 1}, {"exit_code": None},
    {"executed": False}, {"fresh": False}, {"passed": False},
])
def test_stale_unexecuted_failed_or_missing_exit_evidence_cannot_verify(override):
    assert not evaluate(verifications=[evidence(**override)])["passed"]


def test_incomplete_acceptance_and_unresolved_verification_block_final_response():
    step = {"id": "s", "title": "Fix", "status": "pending", "acceptance_criteria": ["Works"]}
    assert not evaluate(plan=[step])["passed"]
    step.update(status="completed", verification="pytest", evidence_ids=["e1"])
    assert evaluate(plan=[step])["passed"]
    failed = evidence(evidence_id="e2", command="lint", passed=False, exit_code=1, status="failed")
    assert not evaluate(plan=[step], verifications=[failed, evidence()])["passed"]
    assert not evaluate(unresolved_operations=[{"tool_call_id": "remote-mutation"}])["passed"]
    assert not evaluate(verifications=[evidence(), {"kind": "criterion", "criterion": "Works", "status": "unverified"}])["passed"]


@pytest.mark.parametrize("mode", ["ask", "plan"])
def test_readonly_modes_need_no_build_or_test(mode):
    result = evaluate(mode=mode, plan=[{"title": "future implementation", "status": "pending"}],
                      changes=False, verifications=[], verified_ids=set())
    assert result["passed"] and result["completion_classification"] == "completed_unverified"


@pytest.mark.asyncio
async def test_failed_verification_cannot_repeat_without_progress_and_repairs_are_bounded(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n")
    control = CodingRunControl("s", "p", mode="assisted")
    tools = CodingToolFilter(tmp_path, control)
    calls = []
    async def failing(command, cwd=".", timeout=60):
        calls.append(command)
        return {"command": command, "cwd": cwd, "passed": False, "exit_code": 1, "timed_out": False, "output": "same failure"}
    tools.run_command = failing
    assert json.loads((await tools.run_tests("pytest")).content)["status"] == "failed"
    repeated = json.loads((await tools.run_tests("pytest")).content)
    assert repeated["status"] == "blocked" and repeated["executed"] is False and calls == ["pytest"]
    # Explicitly changed source permits new evidence, until the repair cap.
    for value in (2, 3):
        control.set_state("implementing")
        (tmp_path / "source.py").write_text(f"value = {value}\n")
        await tools.run_tests("pytest")
    assert control.completion.repair_cycles == 3 and control.completion.state == "blocked"
    (tmp_path / "source.py").write_text("value = 4\n")
    await tools.run_tests("pytest")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_real_recorded_exit_and_source_digest_control_completion(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n")
    control = CodingRunControl("s", "p", mode="assisted")
    tools = CodingToolFilter(tmp_path, control)
    async def successful(command, cwd=".", timeout=60):
        return {"command": command, "cwd": cwd, "passed": True, "exit_code": 0, "timed_out": False, "output": "1 passed"}
    tools.run_command = successful
    result = json.loads((await tools.run_tests("pytest")).content)
    tools.update_plan([{"id": "fix", "title": "Fix", "status": "completed", "acceptance_criteria": ["Works"],
                        "verification": "pytest", "evidence_ids": [result["evidence_id"]]}])
    assert tools.completion_gate()["completion_classification"] == "completed_verified"
    (tmp_path / "source.py").write_text("value = 2\n")
    assert tools.completion_gate()["completion_classification"] == "blocked"


def test_lifecycle_uses_existing_native_projection_and_legacy_session_statuses(tmp_path):
    events = []
    control = CodingRunControl("s", "p", on_event=events.append)
    control.set_state("repairing")
    assert control.state == "correcting" and events[-1].metadata["lifecycle_state"] == "repairing"
    control.set_state("completed_verified")
    assert control.state == "completed"
    cockpit = CockpitState()
    for event in events:
        cockpit.apply(event)
    assert cockpit.summary["completion_classification"] == "completed_verified"
    assert cockpit.agents["orchestrator"]["status"] == "completed"
    cockpit.finish("completed")
    assert cockpit.summary["completion_classification"] == "completed_verified"
    db = ProjectDatabase(tmp_path / "history.db")
    db.create_project(Project(id="p", name="Project", local_path=str(tmp_path)))
    store = CodingSessionStore(db)
    record = store.create_run("p", session_id="s")
    store.append_event(record.id, {"type": "task_state", "metadata": {"state": "completed", "completion_classification": "completed_verified"}})
    store.finish_run(record.id, "completed_verified")
    assert CodingSessionStore(ProjectDatabase(db.db_path)).get_run(record.id).status == "completed"
    assert store.get_run(record.id).result == "completed_verified"
    # Old records retain their old fields and never acquire fabricated evidence.
    old = store.create_run("p", session_id="old")
    store.finish_run(old.id, "completed")
    assert store.get_run(old.id).tests_executed == []


@pytest.mark.asyncio
async def test_malformed_arguments_and_permission_denials_remain_classified(tmp_path):
    control = CodingRunControl("s", "p", mode="ask")
    tools = CodingToolFilter(tmp_path, control)
    session = ChatSession(session_id="s")
    malformed = ToolCall(id="bad", function={"name": "list_files", "arguments": "{"})
    result = await tools.execute(malformed, session)
    assert result.metadata["failure_kind"] == "invalid_tool_arguments"
    assert result.metadata["request_phase"] == "tool_not_dispatched"
    denied = ToolCall(id="denied", function={"name": "write_file", "arguments": "{}"})
    result = await tools.execute(denied, session)
    assert result.metadata["failure_kind"] == "permission_denied"
    assert result.metadata["retry_eligible"] is False


@pytest.mark.asyncio
async def test_uncertain_external_timeout_blocks_mutation_replay(tmp_path):
    control = CodingRunControl("s", "p", mode="assisted", permissions={"allow_mcp": True})
    control.on_event = lambda event: control.decide_approval(event.metadata["request_id"], True) if event.type == AgentEventType.APPROVAL_REQUIRED else None
    session = ChatSession(session_id="s", active_mcp_servers={"remote"})
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="remote_mutate", source="mcp:remote", description="Mutation"), lambda: None)
    calls = []
    async def uncertain(call):
        calls.append(call.id)
        raise TimeoutError("connection stopped after dispatch")
    registry.execute = uncertain
    tools = CodingToolFilter(tmp_path, control, registry, {"remote_mutate"})
    call = ToolCall(id="first", function={"name": "remote_mutate", "arguments": "{}"})
    result = await tools.execute(call, session)
    assert result.metadata["failure_kind"] == "tool_outcome_unknown"
    assert result.metadata["request_phase"] == "tool_dispatched"
    assert control.state == "blocked" and control.unresolved_operations
    call = call.model_copy(update={"id": "replay"})
    result = await tools.execute(call, session)
    assert result.metadata["failure_kind"] == "permission_denied" and calls == ["first"]
    assert not tools.completion_gate()["passed"]


def test_legacy_pending_history_records_durable_uncertain_attempt_without_replay():
    from firik_agent.core.recovery import operation_fingerprint
    call = ToolCall(id="legacy-call", function={"name": "remote_mutate", "arguments": '{"amount":5}'})
    messages = [Message(role="assistant", tool_calls=[call])]
    control = CodingRunControl("s", "p")
    repair_tool_history(messages, control)
    attempt = messages[0].execution["tool_attempts"][0]
    assert attempt["fingerprint"] == operation_fingerprint("remote_mutate", '{"amount":5}')
    assert attempt["status"] == "outcome_unknown" and attempt["request_phase"] == "tool_dispatched"
    assert messages[1].execution["reconciliation_status"] == "required"
    assert control.unresolved_operations[0]["attempt_id"] == attempt["attempt_id"]
    assert "execution" not in messages[0].to_dict()
    repair_tool_history(messages)
    assert len(messages) == 2 and len(messages[0].execution["tool_attempts"]) == 1


def test_pending_repair_preserves_known_successful_attempt_and_prior_journal():
    call = ToolCall(id="known", function={"name": "write_file", "arguments": "{}"})
    original = {"attempt_id": "existing", "tool_call_id": "known", "status": "completed",
                "request_phase": "tool_completed", "fingerprint": "known-fingerprint"}
    messages = [Message(role="assistant", tool_calls=[call], execution={"tool_attempts": [original]})]
    repair_tool_history(messages)
    assert messages[0].execution["tool_attempts"] == [original]
    assert messages[1].execution["request_phase"] == "tool_completed"
