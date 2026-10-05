import asyncio
import json

import pytest

from evren_agent.core.session import ChatSession
from evren_agent.core.types import ToolCall, ToolCallFunction
from evren_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from evren_agent.projects.subagents import SubagentToolFilter
from evren_agent.ui.subagent_activity import update_subagent_activity


def setup(tmp_path, factory=lambda: None):
    session = ChatSession(session_id="parent")
    parent = CodingToolFilter(tmp_path, CodingRunControl("parent", "project"))
    events = []
    return SubagentToolFilter(parent, session, factory, events.append), events


@pytest.mark.asyncio
async def test_parallel_limit_total_limit_and_cancel_before_entry(tmp_path):
    manager, events = setup(tmp_path)
    blocked = asyncio.Event()
    async def worker(assignment, tools):
        await blocked.wait()
        return "ok"
    manager.coordinator.register("planning", worker)
    first = await manager.spawn("research", "a")
    second = await manager.spawn("research", "b")
    with pytest.raises(ValueError, match="İki"):
        await manager.spawn("research", "c")
    await manager.cancel(first["agent_id"])
    assert manager.jobs[first["agent_id"]]["status"] == "cancelled"
    third = await manager.spawn("research", "c")
    await manager.cancel(second["agent_id"])
    await manager.spawn("research", "d")
    with pytest.raises(ValueError, match="dört"):
        await manager.spawn("research", "e")
    await manager.close()
    assert all(job["future"].done() and job["status"] == "cancelled" for job in manager.jobs.values())


@pytest.mark.asyncio
async def test_child_tools_reject_mutations_and_parent_tightened_permissions(tmp_path):
    manager, events = setup(tmp_path)
    (tmp_path / "data.py").write_text("value = 1")
    async def worker(assignment, tools):
        assert "spawn_subagent" not in {s["function"]["name"] for s in tools.get_schemas()}
        for name in ("write_file", "run_command", "record_verification"):
            result = await tools.execute(ToolCall(id=name, function=ToolCallFunction(name=name, arguments="{}")))
            assert result.is_error
        manager.control.permissions["allow_reads"] = False
        result = await tools.execute(ToolCall(id="read", function=ToolCallFunction(name="read_file", arguments='{"path":"data.py"}')))
        assert result.is_error
        return "ok"
    manager.coordinator.register("review", worker)
    job = await manager.spawn("review", "inspect")
    result = await manager.wait(job["agent_id"])
    assert result["passed"] and (tmp_path / "data.py").read_text() == "value = 1"


@pytest.mark.asyncio
async def test_wrong_session_unknown_id_and_parent_cancellation(tmp_path):
    manager, events = setup(tmp_path)
    call = ToolCall(id="spawn", function=ToolCallFunction(name="spawn_subagent", arguments=json.dumps({"role":"research","task":"inspect"})))
    assert (await manager.execute(call, ChatSession(session_id="other"))).is_error
    assert manager.get_schemas(ChatSession(session_id="other")) == []
    with pytest.raises(ValueError, match="ait değil"):
        await manager.wait("unknown")
    manager.control.cancel()
    with pytest.raises(asyncio.CancelledError):
        await manager.execute(call, manager.session)
    assert not manager.jobs


def test_activity_updates_one_card_and_restores_passively():
    activities = []
    metadata = {"agent_id": "a", "role": "research", "task": "incele", "status": "running", "elapsed_sec": 2}
    update_subagent_activity(activities, metadata)
    update_subagent_activity(activities, {**metadata, "status": "completed", "elapsed_sec": 4}, "kanıt", historical=True)
    assert len(activities) == 1 and activities[0]["status"] == "Tamamlandı"
    assert activities[0]["output"] == "kanıt" and activities[0]["historical"]


@pytest.mark.asyncio
async def test_worker_timeout_is_failed_and_releases_slot(tmp_path, monkeypatch):
    manager, events = setup(tmp_path)
    async def worker(assignment, tools):
        await asyncio.Event().wait()
    manager.coordinator.register("review", worker)
    monkeypatch.setattr("evren_agent.projects.subagents.WORKER_TIMEOUT", 0.01)
    spawned = await manager.spawn("review", "incele")
    result = await manager.wait(spawned["agent_id"])
    assert result["status"] == "failed" and not result["passed"]
    assert "zaman" in result["result"]
    assert manager.jobs[spawned["agent_id"]]["future"].done()
