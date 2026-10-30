"""Manual cockpit actions must preserve run ownership, policy and durable evidence."""
import inspect
from concurrent.futures import Future

import pytest
from test_coding_bridge import (
    bridge as bridge,  # noqa: PLC0414 -- explicit pytest fixture re-export
)
from test_coding_bridge import start, tool

from firik_agent.core.events import AgentEventType
from firik_agent.core.types import StreamChunk


def manual_session(bridge):
    service, state, roots = bridge
    session, initial = start(bridge)
    initial.result(timeout=5)
    settings = service.projects.db.get_settings("p1")
    settings.coding_preferences.update(provider="local", mode="auto", permissions={
        "allow_terminal": True, "allow_tests": True,
    })
    service.projects.db.save_settings(settings)
    return service, state, roots, session


def test_manual_submit_failure_releases_session_and_closes_coroutine(bridge, monkeypatch):
    service, _, _, session = manual_session(bridge)
    submitted = []

    def unavailable(coroutine, **kwargs):
        submitted.append(coroutine)
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(service.runtime, "submit", unavailable)
    with pytest.raises(RuntimeError, match="runtime unavailable"):
        service.run_coding_command_async(session.session_id, "unused", lambda value: None, lambda error: None)
    assert session.session_id not in service._streams
    assert session.session_id not in service._stream_futures
    assert inspect.getcoroutinestate(submitted[0]) == inspect.CORO_CLOSED


def test_manual_cancel_before_entry_releases_reserved_session(bridge, monkeypatch):
    service, _, _, session = manual_session(bridge)
    previous = service.coding_store.latest_run("p1", session_id=session.session_id)

    def cancelled_before_entry(coroutine, **kwargs):
        # A runtime that never begins the task cannot execute its finally block.
        coroutine.close()
        future = Future()
        future.cancel()
        return future

    monkeypatch.setattr(service.runtime, "submit", cancelled_before_entry)
    future = service.run_coding_command_async(session.session_id, "unused", lambda value: None, lambda error: None)
    assert future.cancelled()
    assert session.session_id not in service._streams
    assert session.session_id not in service._stream_futures
    assert service.coding_store.latest_run("p1", session_id=session.session_id).id == previous.id


def test_manual_audit_failure_stops_before_command_execution(bridge, monkeypatch, python_command):
    service, _, roots, session = manual_session(bridge)
    marker = roots["p1"] / "audit-failure-marker.txt"
    append = service.coding_store.append_event
    failures = []

    def fail_initial_checkpoint(run_id, event):
        if event.type == AgentEventType.CHECKPOINT_CREATED:
            failures.append(run_id)
            raise RuntimeError("forced audit append failure")
        return append(run_id, event)

    monkeypatch.setattr(service.coding_store, "append_event", fail_initial_checkpoint)

    def approve(event):
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            service.coding_control(session.session_id, "approve", event.metadata["request_id"], True)

    command = python_command("-c", "from pathlib import Path; Path('audit-failure-marker.txt').write_text('executed')")
    future = service.run_coding_command_async(session.session_id, command, lambda value: None, lambda error: None,
                                             on_event=approve)
    try:
        future.result(timeout=5)
    except RuntimeError as error:
        assert "could not be persisted" in str(error)
    assert failures
    assert not marker.exists()
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert record.status in {"failed", "cancelled"}
    assert session.session_id not in service._streams
    assert session.session_id not in service._coding_controls


@pytest.mark.parametrize("kind", ["shell", "test"])
def test_manual_actions_inherit_project_install_packages_denial(bridge, python_command, kind):
    service, _, roots, session = manual_session(bridge)
    settings = service.projects.db.get_settings("p1")
    settings.permissions["install_packages"] = "never"
    service.projects.db.save_settings(settings)
    marker = roots["p1"] / "denied-command-marker.txt"
    approvals = []

    def approve(event):
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            approvals.append(event.metadata["request_id"])
            service.coding_control(session.session_id, "approve", event.metadata["request_id"], True)

    command = python_command("-c", "from pathlib import Path; Path('denied-command-marker.txt').write_text('executed')")
    result = service.run_coding_command_async(session.session_id, command, lambda value: None, lambda error: None,
                                              kind=kind, on_event=approve).result(timeout=5)
    assert result["status"] == "failed"
    assert not marker.exists() and not approvals
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert record.status == "failed"
    assert record.permissions["allow_terminal"] is False
    assert record.permissions["allow_tests"] is False


def test_completion_gate_block_is_durable_and_resumes_as_a_new_run(bridge):
    service, state, _ = bridge
    pending = [{"id": "pending", "title": "Doğrulanacak iş", "acceptance_criteria": ["Kanıt gerekli"],
                "verification": "Kaynağı doğrula"}]

    async def incomplete(iteration, messages):
        if iteration == 1:
            yield StreamChunk(tool_calls=[tool("update_plan", steps=pending)])
        else:
            yield StreamChunk(delta_content="Model yanıtı sona erdi.")

    state["script"] = incomplete
    done_events = []
    session, first = start(bridge, on_event=lambda event: done_events.append(event) if event.type == AgentEventType.DONE else None)
    first.result(timeout=5)
    blocked = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert blocked.status == "blocked" and blocked.result == "blocked"
    assert blocked.ended_at and not done_events
    assert not service.coding_store.recover_interrupted()
    assert service.coding_store.get_run(blocked.id).status == "blocked"

    async def simple(iteration, messages):
        yield StreamChunk(delta_content="Yeni devam isteği")

    state["script"] = simple
    _, resumed = start(bridge, session=session,
        options={"resume_run_id": blocked.id, "resume_approved": True})
    resumed.result(timeout=5)
    following = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert following.id != blocked.id and following.resumed_from == blocked.id
    assert any(item.get("kind") == "resume_revalidation" for item in following.checkpoints)
