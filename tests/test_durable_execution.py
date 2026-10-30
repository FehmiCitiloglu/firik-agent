"""Real runtime boundaries with SQLite and deterministic crash hooks, offline."""
import asyncio
import hashlib
import json

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.recovery import RecoveryBudget, RecoveryLimits, BudgetedCompactionProvider
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter, DurableExecutionError
from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal, JournalConflict
from firik_agent.projects.models import Project
from firik_agent.projects.cockpit import CockpitState
from firik_agent.projects.resume import DurableResumeController


class Crash(BaseException):
    pass


@pytest.fixture
def durable(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    db = ProjectDatabase(tmp_path / "state" / "projects.db")
    db.create_project(Project(id="project", name="Fixture", local_path=str(root)))
    store = CodingSessionStore(db)
    run = store.create_run("project", session_id="session", mode="auto")
    control = CodingRunControl("session", "project", mode="auto", run_id=run.id)
    journal = ExecutionJournal(db)
    control.attach_journal(journal, workspace_root=root)
    recovery = DurableResumeController(db)
    recovery.record_workspace(run.id, root)
    return root, store, run, control, journal, recovery


def tool(name, args, identity="call"):
    return ToolCall(id=identity, function=ToolCallFunction(name=name, arguments=json.dumps(args)))


def crash_at(control, target):
    def inject(boundary, data):
        if boundary == target:
            raise Crash(target)
    control.durability_fault_hook = inject


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary,changed", [("before_filesystem_mutation", False), ("after_filesystem_mutation", True)])
async def test_filesystem_crash_keeps_unknown_effect_and_preserves_dirty_bytes(durable, boundary, changed):
    root, store, run, control, journal, recovery = durable
    path = root / "dirty.py"
    path.write_text("user_dirty = 1\n")
    recovery.record_workspace(run.id, root)
    tools = CodingToolFilter(root, control)
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    call = tool("edit_file", {"path": "dirty.py", "expected_hash": before_hash,
                             "old_text": "= 1", "new_text": "= 2"})
    crash_at(control, boundary)
    with pytest.raises(Crash):
        await tools.execute(call, ChatSession(session_id="session"))
    assert path.read_text() == ("user_dirty = 2\n" if changed else "user_dirty = 1\n")
    journal.recover_interrupted(run.id)
    assert journal.list_steps(run.id)[0]["state"] == "outcome_unknown"
    store.recover_interrupted()
    report = recovery.inspect(run.id)
    assert not report["resume_eligible"] and report["uncertain_operations"]
    assert path.read_text() == ("user_dirty = 2\n" if changed else "user_dirty = 1\n")


@pytest.mark.asyncio
async def test_intent_rollback_prevents_manual_tool_dispatch(durable):
    root, store, run, control, journal, recovery = durable
    def fail(boundary, context):
        if boundary == "journal.before_commit":
            raise OSError("fixture disk error")
    journal.fault_hook = fail
    with pytest.raises(DurableExecutionError):
        await CodingToolFilter(root, control).execute(tool("write_file", {
            "path": "new.py", "content": "unsafe", "expected_hash": "new"}), ChatSession(session_id="session"))
    assert not (root / "new.py").exists()
    assert journal.list_steps(run.id) == []


class Offline:
    name, current_model, model_metadata = "fixture", "offline model", {}

    def __init__(self):
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        return Message(role="assistant", content="durable output")

    async def chat_stream(self, **kwargs):
        self.calls += 1
        yield StreamChunk(delta_content="durable output")


def agent_for(provider):
    agent = Agent(config={"agent": {"context_compaction": {"enabled": False}}, "providers": {},
                          "plugins": {}, "skills": {}})
    agent._is_initialized = True
    agent.providers.register("fixture", provider, set_active=True)
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("boundary", ["before_model_request", "after_model_output"])
async def test_model_crash_never_resubmits_on_recovery(durable, streaming, boundary):
    root, store, run, control, journal, recovery = durable
    provider = Offline()
    agent = agent_for(provider)
    crash_at(control, boundary)
    with pytest.raises(Crash):
        if streaming:
            _ = [event async for event in agent.run_stream("task", session=ChatSession(session_id="session"), run_control=control, session_tool_filter=CodingToolFilter(root, control), cwd=str(root))]
        else:
            await agent.run("task", session=ChatSession(session_id="session"), run_control=control, session_tool_filter=CodingToolFilter(root, control))
    assert provider.calls == (0 if boundary == "before_model_request" else 1)
    store.recover_interrupted()
    assert journal.list_steps(run.id)[0]["state"] == "outcome_unknown"
    assert recovery.inspect(run.id)["uncertain_operations"]
    assert provider.calls == (0 if boundary == "before_model_request" else 1)


@pytest.mark.asyncio
async def test_pending_approval_is_durable_and_never_inherited(durable):
    root, store, run, control, journal, recovery = durable
    pending = asyncio.create_task(control.request_approval("run_command", {"command": "echo ok"}, "test"))
    await asyncio.sleep(.04)
    row, = journal.list_steps(run.id)
    assert row["state"] == "ready" and row["metadata"]["pending"] is True
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)
    store.recover_interrupted()
    assert recovery.inspect(run.id)["pending_approvals"]
    assert not control.decide_approval(row["attempt_id"], True)


@pytest.mark.asyncio
async def test_approval_decision_journal_has_terminal_outcome(durable):
    root, store, run, control, journal, recovery = durable
    pending = asyncio.create_task(control.request_approval("run_command", {"command": "echo ok"}, "test"))
    await asyncio.sleep(.04)
    request_id = next(iter(control.pending_approvals))
    assert control.decide_approval(request_id, True)
    await pending
    row = journal.get(run.id, request_id)
    assert row["state"] == "completed" and row["result"] == {"allowed": True}
    assert row["metadata"]["grant_scope"] == "this_live_operation_only"


@pytest.mark.asyncio
async def test_tool_result_persistence_failure_leaves_effect_unknown_and_blocks_replay(durable):
    root, store, run, control, journal, recovery = durable
    provider = Offline()
    agent = agent_for(provider)
    tools = CodingToolFilter(root, control)
    session = ChatSession(session_id="session")
    call = tool("write_file", {"path": "once.py", "content": "once", "expected_hash": "new"})
    messages = [Message(role="assistant", tool_calls=[call])]
    async def checkpoint():
        pass
    attempt, result, failure = await agent._execute_attempt(call, messages=messages, session=session,
        tool_filter=tools, run_control=control, checkpoint=checkpoint)
    crash_at(control, "after_tool_result")
    with pytest.raises(Crash):
        await agent._dispatch_attempt(call, attempt, session=session, tool_filter=tools,
                                      run_control=control, checkpoint=checkpoint)
    assert (root / "once.py").read_text() == "once"
    journal.recover_interrupted(run.id)
    assert journal.get(run.id, attempt["attempt_id"])["state"] == "outcome_unknown"
    control.durability_fault_hook = None
    with pytest.raises(DurableExecutionError):
        await agent._execute_attempt(call, messages=[Message(role="assistant", tool_calls=[call])],
            session=session, tool_filter=tools, run_control=control, checkpoint=checkpoint)


@pytest.mark.asyncio
async def test_compaction_paid_request_is_journaled(durable):
    root, store, run, control, journal, recovery = durable
    provider = Offline()
    wrapper = BudgetedCompactionProvider(provider, RecoveryBudget(RecoveryLimits()), {}, run_control=control)
    await wrapper.chat(messages=[Message(role="user", content="api_key=fixture-secret")], max_tokens=32)
    row, = journal.list_steps(run.id)
    assert row["operation"] == "model:compaction" and row["state"] == "completed"
    assert "fixture-secret" not in json.dumps(row)


@pytest.mark.asyncio
async def test_streaming_projection_is_linked_to_durable_request(durable):
    root, store, run, control, journal, recovery = durable
    provider = Offline()
    agent = agent_for(provider)
    control.mode = "ask"
    session = ChatSession(session_id="session")
    events = [event async for event in agent.run_stream("task", session=session, run_control=control, session_tool_filter=CodingToolFilter(root, control), cwd=str(root))]
    model = next(row for row in journal.list_steps(run.id) if row["operation"] == "model:request")
    assert model["state"] == "completed"
    assistant = next(message for message in session.messages if message.role == "assistant")
    assert assistant.execution["durable_request_id"] == model["attempt_id"]


def test_native_execution_event_projection_is_passive_and_idempotent(durable):
    root, store, run, control, journal, recovery = durable
    intent = control.durable_begin("tool:run_command", side_effect="non_idempotent_mutation")
    control.durable_transition(intent["attempt_id"], "dispatching")
    store.recover_interrupted()
    projection = CockpitState()
    record = store.get_run(run.id)
    events = store.list_events(run.id)
    projection.restore(record, events)
    assert projection.execution_steps[intent["attempt_id"]]["state"] == "outcome_unknown"
    assert projection.summary["durable_lifecycle_state"] == "needs_reconciliation"
    length = len(projection.timeline)
    projection.apply(events[-1])
    assert len(projection.timeline) == length


def test_completed_plan_step_cannot_be_mutated_again(durable):
    root, store, run, control, journal, recovery = durable
    control.plan = [{"id": "done", "status": "completed"}]
    with pytest.raises(ValueError, match="Tamamlanan"):
        CodingToolFilter(root, control).write_file("extra.py", "extra", "new", step_id="done")
    assert not (root / "extra.py").exists()


@pytest.mark.asyncio
async def test_failed_pre_mutation_checkpoint_callback_prevents_write(durable):
    root, store, run, control, journal, recovery = durable
    tools = CodingToolFilter(root, control)
    def persist_failed(_path):
        control.cancel_event.set()
    tools.before_file_mutation = persist_failed
    with pytest.raises(asyncio.CancelledError):
        await tools.execute(tool("write_file", {"path": "not-created.py", "content": "x", "expected_hash": "new"}),
                            ChatSession(session_id="session"))
    assert not (root / "not-created.py").exists()


def test_durability_errors_in_event_delivery_propagate(durable):
    from firik_agent.core.events import AgentEventType
    root, store, run, control, journal, recovery = durable
    def fail(_event):
        raise DurableExecutionError("test persistence failure")
    control.on_event = fail
    with pytest.raises(DurableExecutionError):
        control.emit(AgentEventType.CHECKPOINT_CREATED)


def test_journal_binding_validates_session_and_workspace_identity(durable, tmp_path):
    root, store, run, control, journal, recovery = durable
    with pytest.raises(DurableExecutionError):
        CodingRunControl("wrong-session", "project", run_id=run.id).attach_journal(journal, workspace_root=root)
    with pytest.raises(DurableExecutionError):
        control.attach_journal(journal, workspace_root=tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_result", [{"content": '{"persisted":true}', "is_error": False}, {"truncated": True}, None])
async def test_resume_reuses_completed_mutation_even_when_transcript_projection_was_lost(durable, saved_result):
    root, store, run, control, journal, recovery = durable
    args = {"path": "once.py", "content": "once", "expected_hash": "new"}
    original = journal.begin(run.id, "tool:write_file", input_data=json.dumps(args), side_effect="conditional_write")
    journal.transition(run.id, original["attempt_id"], "dispatching")
    journal.transition(run.id, original["attempt_id"], "completed", result=saved_result)
    (root / "once.py").write_text("once")
    recovery.record_workspace(run.id, root)
    store.finish_run(run.id, "interrupted")
    continued = recovery.prepare_resume(run.id, current_permissions={}, approved=True)
    resumed = CodingRunControl("session", "project", mode="auto", run_id=continued.id)
    resumed.resumed_from = run.id
    resumed.attach_journal(journal, workspace_root=root)
    agent = agent_for(Offline())
    call = tool("write_file", args, identity="different_provider_id")
    async def checkpoint():
        pass
    attempt, result, failure = await agent._execute_attempt(call,
        messages=[Message(role="assistant", tool_calls=[call])], session=ChatSession(session_id="session"),
        tool_filter=CodingToolFilter(root, resumed), run_control=resumed, checkpoint=checkpoint)
    assert (root / "once.py").read_text() == "once"
    row, = journal.list_steps(continued.id)
    assert row["operation"] == "tool_result_reuse"
    if saved_result and saved_result.get("content"):
        assert failure is None and result.content == '{"persisted":true}'
        assert attempt["replay_avoided"] is True and row["state"] == "completed"
    else:
        assert failure is not None and result is None and row["state"] == "needs_reconciliation"

