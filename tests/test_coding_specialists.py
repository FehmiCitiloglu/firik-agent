from __future__ import annotations

import asyncio
import json

import pytest

from evren_agent.core.session import ChatSession
from evren_agent.core.types import ToolCall, ToolCallFunction
from evren_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from evren_agent.projects.specialists import (
    FileLeaseConflict, FileLeaseManager, SpecialistCoordinator, SpecialistRole,
    WorkspaceBinding, intersect_permissions,
)


def call(name, arguments):
    return ToolCall(id="child_call", function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


def setup(tmp_path, mode="auto", permissions=None):
    (tmp_path / "source.py").write_text("value = 1\n")
    session = ChatSession(session_id="session")
    control = CodingRunControl("session", "project", task_id="task", mode=mode, permissions=permissions)
    tools = CodingToolFilter(tmp_path, control)
    workspace = WorkspaceBinding("project", str(tmp_path))
    return session, control, tools, workspace


@pytest.mark.asyncio
async def test_specialists_never_start_without_enable_and_registered_real_runner(tmp_path):
    session, control, parent, workspace = setup(tmp_path)
    coordinator = SpecialistCoordinator()
    called = []
    coordinator.register("planning", lambda assignment, tools: called.append(assignment))
    with pytest.raises(ValueError, match="etkin değil"):
        await coordinator.run("planning", workspace=workspace, parent_filter=parent, session=session, context={})
    assert called == [] and (tmp_path / "source.py").read_text() == "value = 1\n"
    with pytest.raises(ValueError, match="yapılandırılmadı"):
        await SpecialistCoordinator(enabled=True).run("coding", workspace=workspace, parent_filter=parent,
                                                      session=session, context={})


def test_permission_intersection_never_grants_unknown_truthy_or_parent_denied_permissions():
    granted = intersect_permissions({"allow_edits": False, "allow_tests": True},
                                    {"allow_edits": True, "allow_tests": 1, "allow_mcp": True})
    assert dict(granted) == {"allow_edits": False, "allow_tests": False}
    assert dict(intersect_permissions({"allow_edits": True}, {})) == {"allow_edits": False}
    with pytest.raises(TypeError):
        granted["allow_edits"] = True


@pytest.mark.asyncio
async def test_plan_specialist_inherits_read_only_parent_mode_and_selected_context(tmp_path):
    session, control, parent, workspace = setup(tmp_path, mode="plan")
    coordinator = SpecialistCoordinator(enabled=True)
    async def runner(assignment, tools):
        assert assignment.mode == "plan" and assignment.project_id == "project"
        assert assignment.context["api_key"] == "[SECRET_FILTERED]"
        names = {schema["function"]["name"] for schema in tools.get_schemas()}
        assert "read_file" in names and "write_file" not in names and "run_command" not in names
        return await tools.execute(call("write_file", {"path": "source.py", "expected_hash": "", "content": "bad"}))
    coordinator.register("coding", runner)
    result = await coordinator.run("coding", workspace=workspace, parent_filter=parent, session=session,
                                   context={"project_id": "project", "api_key": "private"}, requested_tools=["read_file", "write_file"])
    assert result.is_error and (tmp_path / "source.py").read_text() == "value = 1\n"


@pytest.mark.asyncio
async def test_registered_coding_runner_edits_only_its_lease_through_parent_expected_hash(tmp_path):
    session, control, parent, workspace = setup(tmp_path)
    (tmp_path / "other.py").write_text("other = 1\n")
    coordinator = SpecialistCoordinator(enabled=True)
    async def runner(assignment, tools):
        snapshot = parent.read_file("source.py")
        changed = await tools.execute(call("edit_file", {"path": "source.py", "expected_hash": snapshot["hash"],
                                                         "old_text": "value = 1", "new_text": "value = 2"}))
        outside = await tools.execute(call("edit_file", {"path": "other.py", "expected_hash": "",
                                                         "old_text": "other = 1", "new_text": "other = 2"}))
        return changed, outside
    coordinator.register(SpecialistRole.CODING, runner)
    changed, outside = await coordinator.run("coding", workspace=workspace, parent_filter=parent, session=session,
                                            context={}, write_paths=["source.py"])
    assert not changed.is_error and outside.is_error
    assert (tmp_path / "source.py").read_text() == "value = 2\n"
    assert (tmp_path / "other.py").read_text() == "other = 1\n"
    assert len(control.changes) == 1


def test_file_leases_conflict_for_same_file_or_parent_folder_and_release_on_error(tmp_path):
    workspace = WorkspaceBinding("project", str(tmp_path))
    leases = FileLeaseManager()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "source.py").write_text("value = 1")
    with leases.acquire(workspace, "first", ["src/source.py"]):
        with pytest.raises(FileLeaseConflict):
            with leases.acquire(workspace, "second", ["src"]):
                pass
        with leases.acquire(workspace, "second", ["other.py"]):
            pass
    with pytest.raises(RuntimeError, match="runner failed"):
        with leases.acquire(workspace, "first", ["src/source.py"]):
            raise RuntimeError("runner failed")
    with leases.acquire(workspace, "second", ["src/source.py"]):
        pass


@pytest.mark.asyncio
async def test_parallel_specialist_source_edits_conflict_before_second_runner_starts(tmp_path):
    session, control, parent, workspace = setup(tmp_path)
    coordinator = SpecialistCoordinator(enabled=True)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    async def runner(assignment, tools):
        calls.append(assignment.id)
        started.set()
        await release.wait()
        return "Gerçek yürütücü sonucu"
    coordinator.register("coding", runner)
    first = asyncio.create_task(coordinator.run("coding", workspace=workspace, parent_filter=parent,
                                               session=session, context={}, write_paths=["source.py"]))
    await started.wait()
    try:
        with pytest.raises(FileLeaseConflict):
            await coordinator.run("coding", workspace=workspace, parent_filter=parent, session=session,
                                  context={}, write_paths=["source.py"])
        assert len(calls) == 1
    finally:
        release.set()
        assert await first == "Gerçek yürütücü sonucu"


@pytest.mark.asyncio
async def test_cross_project_workspace_context_and_parent_cancellation_are_preserved(tmp_path):
    session, control, parent, workspace = setup(tmp_path)
    coordinator = SpecialistCoordinator(enabled=True)
    called = []
    coordinator.register("review", lambda assignment, tools: called.append(assignment))
    with pytest.raises(ValueError, match="ana oturuma"):
        await coordinator.run("review", workspace=WorkspaceBinding("other", str(tmp_path)),
                              parent_filter=parent, session=session, context={})
    with pytest.raises(ValueError, match="başka bir projeye"):
        await coordinator.run("review", workspace=workspace, parent_filter=parent, session=session,
                              context={"project_id": "other"})
    control.cancel()
    with pytest.raises(asyncio.CancelledError):
        await coordinator.run("review", workspace=workspace, parent_filter=parent, session=session, context={})
    assert called == []


@pytest.mark.asyncio
async def test_child_permissions_can_only_narrow_and_live_parent_gate_is_still_used(tmp_path):
    session, control, parent, workspace = setup(tmp_path)
    coordinator = SpecialistCoordinator(enabled=True)
    async def runner(assignment, tools):
        names = {schema["function"]["name"] for schema in tools.get_schemas()}
        assert "run_tests" not in names
        denied = await tools.execute(call("run_tests", {"command": "python -m pytest"}))
        control.mode = "plan"  # Parent may tighten its live mode while a child is active.
        edit = await tools.execute(call("edit_file", {"path": "source.py", "expected_hash": "",
                                                      "old_text": "value = 1", "new_text": "value = 2"}))
        return denied, edit
    coordinator.register("coding", runner)
    denied, edit = await coordinator.run("coding", workspace=workspace, parent_filter=parent, session=session,
                                        context={}, requested_permissions={"allow_edits": True, "allow_tests": False},
                                        write_paths=["source.py"])
    assert denied.is_error and edit.is_error
