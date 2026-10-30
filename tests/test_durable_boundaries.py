"""Offline integration faults at Git, verification, checkpoint and child boundaries."""
import asyncio
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter, DurableExecutionError
from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal
from firik_agent.projects.models import Project
from firik_agent.projects.resume import DurableResumeController
from firik_agent.projects.subagents import SubagentToolFilter
from test_coding_bridge import bridge, start


class BoundaryCrash(BaseException):
    """Process-stop equivalent: bypass ordinary operation error handling."""


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    db = ProjectDatabase(tmp_path / "state" / "projects.db")
    db.create_project(Project(id="project", name="Boundary fixture", local_path=str(root)))
    store = CodingSessionStore(db)
    run = store.create_run("project", session_id="parent", mode="auto")
    control = CodingRunControl("parent", "project", mode="auto", run_id=run.id)
    journal = ExecutionJournal(db)
    control.attach_journal(journal, workspace_root=root)
    recovery = DurableResumeController(db)
    recovery.record_workspace(run.id, root)
    return root, store, run, control, journal, recovery


def call(name, arguments, identity):
    return ToolCall(id=identity, function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


def approve_local_fixture_commands(control):
    approvals = []
    def observe(event):
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            approvals.append(event.metadata["request_id"])
            assert control.decide_approval(event.metadata["request_id"], True)
    control.on_event = observe
    return approvals


def offline_agent(provider=None):
    agent = Agent(config={"agent": {"context_compaction": {"enabled": False}}, "providers": {},
                          "plugins": {"autoload_builtins": False}, "skills": {}})
    agent._is_initialized = True
    if provider:
        agent.providers.register(provider.name, provider, set_active=True)
    return agent


async def checkpoint():
    pass


@pytest.mark.asyncio
async def test_real_git_commit_then_crash_before_result_commit_remains_unknown(runtime, tmp_path):
    root, store, run, control, journal, recovery = runtime
    empty_hooks = tmp_path / "empty-hooks"
    empty_hooks.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    args = ["git", "-c", "user.name=Boundary Fixture", "-c", "user.email=fixture@example.invalid",
            "-c", f"core.hooksPath={empty_hooks}", "commit", "--allow-empty", "-m", "durable boundary"]
    command = subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
    recovery.record_workspace(run.id, root)
    approvals = approve_local_fixture_commands(control)
    tools, agent = CodingToolFilter(root, control), offline_agent()
    request = call("run_command", {"command": command}, "commit-once")
    session = ChatSession(session_id="parent")
    attempt, _, _ = await agent._execute_attempt(request,
        messages=[Message(role="assistant", tool_calls=[request])], session=session,
        tool_filter=tools, run_control=control, checkpoint=checkpoint)
    def inject(boundary, context):
        if boundary == "after_tool_result":
            raise BoundaryCrash(boundary)
    control.durability_fault_hook = inject
    with pytest.raises(BoundaryCrash):
        await agent._dispatch_attempt(request, attempt, session=session, tool_filter=tools,
                                      run_control=control, checkpoint=checkpoint)
    assert approvals, "The real shell/Git operation must pass a fresh explicit test approval."
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True).stdout
    assert subprocess.run(["git", "-C", str(root), "rev-list", "--count", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip() == "1"
    reopened = CodingSessionStore(ProjectDatabase(store.db.db_path))
    reopened.recover_interrupted()
    assert ExecutionJournal(reopened.db).get(run.id, attempt["attempt_id"])["state"] == "outcome_unknown"
    assert DurableResumeController(reopened.db).inspect(run.id)["resume_eligible"] is False
    control.durability_fault_hook = None
    with pytest.raises(DurableExecutionError):
        await agent._execute_attempt(request, messages=[Message(role="assistant", tool_calls=[request])],
            session=session, tool_filter=tools, run_control=control, checkpoint=checkpoint)
    assert subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True).stdout == commit


@pytest.mark.asyncio
async def test_cancelled_real_quality_gate_never_becomes_passing_evidence(runtime, tmp_path, python_command):
    root, store, run, control, journal, recovery = runtime
    marker = tmp_path / "quality-gate-started"
    script = f"from pathlib import Path; import time; Path({str(marker)!r}).write_text('started'); time.sleep(30)"
    command = python_command("-c", script)
    approvals = approve_local_fixture_commands(control)
    tools = CodingToolFilter(root, control)
    tools.configure_quality_gates([{"id": "interrupted", "kind": "tests", "command": command}])
    pending = asyncio.create_task(tools.execute(call("run_quality_gate", {"gate_id": "interrupted"}, "gate-call"),
                                              ChatSession(session_id="parent")))
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while not marker.exists() and not pending.done() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(.01)
        assert marker.exists(), "The fixture command must actually start before interruption."
        assert approvals
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    store.recover_interrupted()
    report = recovery.inspect(run.id)
    assert report["pending_verification"] and not report["resume_eligible"]
    assert all(row["state"] == "outcome_unknown" for row in report["steps"]
               if row["operation"] in {"tool:run_tests", "tool:run_quality_gate"})
    assert not any(item.get("passed") for item in control.verifications + control.quality_results)
    assert not any(item.get("passed") for item in store.get_run(run.id).tests_executed)


def test_checkpoint_snapshot_committed_before_terminal_journal_crash_stays_inspectable(bridge, monkeypatch):
    service, state, roots = bridge
    original = CodingRunControl.durable_boundary
    injected = []
    def boundary(control, name, **data):
        original(control, name, **data)
        if name != "before_completed" or injected or control.execution_journal is None:
            return
        row = control.execution_journal.get(control.journal_run_id, data.get("attempt_id"))
        if row and row["operation"] == "checkpoint.create":
            injected.append(row["attempt_id"])
            raise BoundaryCrash("snapshot committed, terminal journal absent")
    monkeypatch.setattr(CodingRunControl, "durable_boundary", boundary)
    session, future = start(bridge, mode="ask")
    with pytest.raises(BoundaryCrash):
        future.result(timeout=10)
    assert injected and not state["requests"], "The before-run checkpoint precedes all model dispatch."
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    snapshots = service.checkpoint_store.list_checkpoints("p1", run_id=record.id)
    assert len(snapshots) == 1
    service.coding_store.recover_interrupted(project_id="p1")
    row = ExecutionJournal(service.projects.db).get(record.id, injected[0])
    assert row["state"] == "outcome_unknown"
    report = DurableResumeController(service.projects.db).inspect(record.id, project_id="p1")
    assert report["uncertain_operations"] and not report["resume_eligible"]
    assert (roots["p1"] / "main.py").read_text() == "value = 'eski'\n"
    assert service.checkpoint_store.compare_checkpoint(snapshots[0]["id"], project_id="p1",
        root=roots["p1"], run_id=record.id)["changes"] == []


class OfflineChild:
    name, current_model = "offline-child", "fixture"
    model_metadata = {"fixture": {"source": "verified_config", "supports_tools": True,
        "input_cost_per_million": 0.0, "output_cost_per_million": 0.0}}

    def __init__(self):
        self.requests = 0

    def set_model(self, model):
        self.current_model = model

    async def chat_stream(self, **kwargs):
        self.requests += 1
        yield StreamChunk(delta_content="Persisted child result", usage={"total_tokens": 1})


@pytest.mark.asyncio
async def test_real_child_completed_result_survives_interrupted_delivery_without_recreation(runtime):
    root, store, run, control, journal, recovery = runtime
    provider = OfflineChild()
    parent = CodingToolFilter(root, control)
    session = ChatSession(session_id="parent", model="fixture")
    manager = SubagentToolFilter(parent, session, lambda: offline_agent(provider),
                                lambda event: store.append_event(run.id, event))
    spawned = await manager.spawn("researcher", "Return a short fixture finding")
    agent_id = spawned["agent_id"]
    await manager.jobs[agent_id]["future"]
    outcome = journal.get(run.id, agent_id)
    assert outcome["state"] == "completed" and outcome["result"]["result"] == "Persisted child result"
    assert provider.requests == 1
    def inject(boundary, data):
        if boundary == "before_completed" and data.get("attempt_id") == "delivery_" + agent_id:
            raise BoundaryCrash("child delivery interrupted")
    control.durability_fault_hook = inject
    with pytest.raises(BoundaryCrash):
        await manager.wait(agent_id)
    control.durability_fault_hook = None
    await manager.close()
    reopened_store = CodingSessionStore(ProjectDatabase(store.db.db_path))
    reopened_store.recover_interrupted()
    reopened = DurableResumeController(reopened_store.db)
    report = reopened.inspect(run.id)
    child = next(row for row in report["children"] if row["attempt_id"] == agent_id)
    delivery = next(row for row in report["children"] if row["attempt_id"] == "delivery_" + agent_id)
    assert child["state"] == "completed" and child["result"]["result"] == "Persisted child result"
    assert delivery["state"] == "outcome_unknown" and not report["resume_eligible"]
    reopened_store.recover_interrupted()
    reopened.inspect(run.id)
    assert provider.requests == 1
    assert len([row for row in reopened.journal.list_steps(run.id) if row["operation"].startswith("child:")]) == 1

    # An explicit reconciliation can acknowledge that this delivery never
    # reached its parent; the already completed child must still be reusable.
    reopened.reconcile(run.id, "delivery_" + agent_id, "cancelled",
                       evidence="Delivery interrupted before the parent received its result; child output inspected.")
    assert not reopened.inspect(run.id)["unprojected_model_outputs"]
    continuation = reopened.prepare_resume(run.id, current_permissions={}, approved=True)
    resumed = CodingRunControl("parent", "project", mode="auto", run_id=continuation.id)
    resumed.resumed_from = run.id
    resumed.attach_journal(reopened.journal, workspace_root=root)
    restored = SubagentToolFilter(CodingToolFilter(root, resumed), session,
        lambda: offline_agent(provider), lambda event: reopened_store.append_event(continuation.id, event))
    try:
        delivered = await restored.wait(agent_id)
        repeated = await restored.wait(agent_id)
        assert delivered["recovered"] is True and delivered["result"] == "Persisted child result"
        assert repeated == delivered
        assert not restored.jobs and provider.requests == 1
        assert len([row for row in reopened.journal.list_steps(continuation.id)
                    if row["operation"] == "child_delivery"]) == 1
    finally:
        await restored.close()

    # Another explicitly approved continuation must have a separate delivery
    # identity even though it reads the same original child outcome.
    reopened_store.finish_run(continuation.id, "interrupted")
    next_run = reopened.prepare_resume(continuation.id, current_permissions={}, approved=True)
    next_control = CodingRunControl("parent", "project", mode="auto", run_id=next_run.id)
    next_control.attach_journal(reopened.journal, workspace_root=root)
    next_manager = SubagentToolFilter(CodingToolFilter(root, next_control), session,
        lambda: offline_agent(provider), lambda event: reopened_store.append_event(next_run.id, event))
    try:
        assert (await next_manager.wait(agent_id))["recovered"] is True
        assert provider.requests == 1 and not next_manager.jobs
        first_delivery = reopened.journal.list_steps(continuation.id)[0]["attempt_id"]
        assert reopened.journal.list_steps(next_run.id)[0]["attempt_id"] != first_delivery
    finally:
        await next_manager.close()
