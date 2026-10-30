"""Delegation cannot grant capabilities or write directly into the user checkout."""
import asyncio
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from firik_agent.core.session import ChatSession
from firik_agent.core.types import ToolCall, ToolCallFunction
from firik_agent.projects.assignments import AgentAssignment, AgentBudget, validated_paths
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.subagents import SubagentToolFilter


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "a.py").write_text("value = 1\n")
    (tmp_path / "b.py").write_text("other = 1\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def manager(root, **kwargs):
    control = CodingRunControl("parent", "project", mode="auto")
    return SubagentToolFilter(CodingToolFilter(root, control), ChatSession(session_id="parent"),
        lambda: None, lambda event: None, **kwargs)


def call(name, **arguments):
    return ToolCall(id="tool", function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


def test_assignment_validates_scope_and_immutable_budget():
    for path in ("../escape", "/etc/passwd", ".git/config", "C:/escape", "x\\y"):
        with pytest.raises(ValueError):
            validated_paths([path])
    with pytest.raises(ValueError):
        AgentBudget(True)
    assignment = AgentAssignment("task", "worker", ("a.py",), frozenset({"read_file"}), (), (), (), AgentBudget(100), "host")
    assert assignment.to_dict()["permitted_tools"] == ["read_file"]


@pytest.mark.asyncio
async def test_worker_isolated_and_contract_blocks_host_shell(repo):
    owner = manager(repo)
    async def worker(assignment, tools):
        assert Path(assignment.workspace.root) != repo
        assert assignment.workspace.kind == "isolated_existing"
        names = {item["function"]["name"] for item in tools.get_schemas()}
        assert not names & {"run_command", "run_tests", "run_quality_gate", "spawn_subagent"}
        read = await tools.execute(call("read_file", path="a.py"))
        snapshot = json.loads(read.content)
        result = await tools.execute(call("edit_file", path="a.py", expected_hash=snapshot["hash"], old_text="1", new_text="2"))
        assert not result.is_error, result
        outside = await tools.execute(call("write_file", path="b.py", expected_hash="", content="outside"))
        assert outside.is_error
        return "changed locally"
    owner.coordinator.register("coding", worker)
    spawned = await owner.spawn("worker", "change", write_paths=["a.py"], required_outputs=["diff"], acceptance_criteria=["value changes"])
    result = await owner.wait(spawned["agent_id"])
    assert result["passed"], result
    assert (repo / "a.py").read_text() == "value = 1\n"
    assert (Path(result["workspace"]["path"]) / "a.py").read_text() == "value = 2\n"
    assert result["merge_proposal"]["paths"] == ["a.py"]
    assert not result["merge_proposal"]["reviewed"]
    await owner.close()
    assert Path(result["workspace"]["path"]).exists()  # no work discarded by cancellation/close


@pytest.mark.asyncio
async def test_parent_live_permission_narrowing_reaches_child(repo):
    owner = manager(repo)
    async def worker(assignment, tools):
        owner.control.permissions["allow_edits"] = False
        result = await tools.execute(call("write_file", path="new.py", content="bad", expected_hash="new"))
        assert result.is_error
        return "denied"
    owner.coordinator.register("coding", worker)
    spawned = await owner.spawn("worker", "change", write_paths=["new.py"])
    assert (await owner.wait(spawned["agent_id"]))["passed"]
    assert not (repo / "new.py").exists()


@pytest.mark.asyncio
async def test_parent_contract_cannot_be_widened(repo):
    owner = manager(repo)
    for kwargs in ({"permitted_tools": ["run_command"]}, {"execution_backend": "docker"},
                   {"budget": {"token_budget": owner.limits.token_budget + 1}}, {"write_paths": ["../escape"]}):
        with pytest.raises((ValueError, PermissionError)):
            await owner.spawn("worker", "change", write_paths=kwargs.pop("write_paths", ["a.py"]), **kwargs)
    assert not owner.jobs


@pytest.mark.asyncio
async def test_dirty_start_explicit_clean_head_keeps_user_changes(repo):
    (repo / "a.py").write_text("user pending\n")
    owner = manager(repo)
    with pytest.raises((ValueError, RuntimeError)):
        await owner.spawn("worker", "change", write_paths=["a.py"])
    clean = manager(repo, dirty_start="clean_head")
    async def worker(assignment, tools):
        assert (Path(assignment.workspace.root) / "a.py").read_text() == "value = 1\n"
        return "clean head only"
    clean.coordinator.register("coding", worker)
    spawned = await clean.spawn("worker", "change", write_paths=["a.py"])
    assert (await clean.wait(spawned["agent_id"]))["passed"]
    assert (repo / "a.py").read_text() == "user pending\n"


@pytest.mark.asyncio
async def test_cancelled_child_preserves_local_work_and_parent_approvals(repo):
    owner = manager(repo)
    entered = asyncio.Event()
    async def worker(assignment, tools):
        (Path(assignment.workspace.root) / "a.py").write_text("preserve me\n")
        entered.set()
        await asyncio.Event().wait()
    owner.coordinator.register("coding", worker)
    spawned = await owner.spawn("worker", "change", write_paths=["a.py"])
    await asyncio.wait_for(entered.wait(), 3)
    await owner.cancel(spawned["agent_id"])
    result = await owner.wait(spawned["agent_id"])
    assert result["status"] == "cancelled"
    assert (Path(result["workspace"]["path"]) / "a.py").read_text() == "preserve me\n"
    assert (repo / "a.py").read_text() == "value = 1\n"
    assert not owner.control.pending_approvals


@pytest.mark.asyncio
async def test_review_and_integration_require_human_and_policy(repo):
    owner = manager(repo)
    async def worker(assignment, tools):
        (Path(assignment.workspace.root) / "a.py").write_text("value = 2\n")
        return "change"
    owner.coordinator.register("coding", worker)
    spawned = await owner.spawn("worker", "change", write_paths=["a.py"])
    result = await owner.wait(spawned["agent_id"])
    proposal = result["merge_proposal"]
    with pytest.raises(PermissionError):
        await owner.integrate_changes(spawned["agent_id"], proposal["digest"])
    approvals = []
    async def approve(name, args, reason):
        approvals.append((name, args))
    owner.control.request_approval = approve
    await owner.review_changes(spawned["agent_id"], proposal["digest"])
    assert approvals[-1][0] == "review_subagent_changes"
    assert (repo / "a.py").read_text() == "value = 1\n"
    owner.control.set_policy({"filesystem.write": "deny"})
    with pytest.raises(PermissionError):
        await owner.integrate_changes(spawned["agent_id"], proposal["digest"])
    owner.control.set_policy({})
    await owner.integrate_changes(spawned["agent_id"], proposal["digest"])
    assert approvals[-1][0] == "integrate_subagent_changes"
    assert (repo / "a.py").read_text() == "value = 2\n"
    assert owner.control.workspace_revision > 0
    assert owner.control.changes[-1]["integration"]


@pytest.mark.asyncio
async def test_overlapping_agents_cannot_choose_one_result_silently(repo):
    owner = manager(repo)
    async def worker(assignment, tools):
        (Path(assignment.workspace.root) / "a.py").write_text(assignment.context["agent_id"] + "\n")
        return "done"
    owner.coordinator.register("coding", worker)
    first = await owner.spawn("worker", "one", write_paths=["a.py"])
    second = await owner.spawn("worker", "two", write_paths=["a.py"])
    for agent in (first, second):
        assert (await owner.wait(agent["agent_id"]))["passed"]
    proposal = owner.jobs[first["agent_id"]]["merge_proposal"]
    with pytest.raises(PermissionError, match="Overlapping"):
        await owner.review_changes(first["agent_id"], proposal["digest"])
    assert (repo / "a.py").read_text() == "value = 1\n"
    assert Path(first["workspace"]["path"]).exists() and Path(second["workspace"]["path"]).exists()


@pytest.mark.asyncio
async def test_docker_parent_cannot_delegate_host_fallback(repo):
    from firik_agent.core.execution import ExecutionConfig
    owner = manager(repo)
    owner.control.execution_config = ExecutionConfig(backend="docker")
    with pytest.raises(PermissionError):
        await owner.spawn("tester", "test", execution_backend="host")
    async def tester(assignment, tools):
        child_control = tools.parent.control
        assert child_control.execution_config is owner.control.execution_config
        assert child_control.execution_status["backend"] == "docker"
        assert "run_tests" in {schema["function"]["name"] for schema in tools.get_schemas()}
        assert "run_command" not in {schema["function"]["name"] for schema in tools.get_schemas()}
        return "configuration verified without launching Docker"
    owner.coordinator.register("testing", tester)
    spawned = await owner.spawn("tester", "inspect config")
    assert (await owner.wait(spawned["agent_id"]))["passed"]


@pytest.mark.asyncio
async def test_parent_policy_tightened_during_child_approval_prevents_write(repo):
    owner = manager(repo)
    owner.control.mode = "approved"
    async def approve(name, args, reason):
        assert args["agent_workspace"] != str(repo)
        owner.control.set_policy({"filesystem.write": "deny"})
    owner.control.request_approval = approve
    async def worker(assignment, tools):
        result = await tools.execute(call("write_file", path="new.py", content="bad", expected_hash="new"))
        assert result.is_error
        assert not (Path(assignment.workspace.root) / "new.py").exists()
        return "denied after policy change"
    owner.coordinator.register("coding", worker)
    spawned = await owner.spawn("worker", "write", write_paths=["new.py"])
    assert (await owner.wait(spawned["agent_id"]))["passed"]
    assert not (repo / "new.py").exists()


@pytest.mark.asyncio
async def test_conflict_resolution_requires_human_inspection_of_both_versions(repo):
    owner = manager(repo)
    async def worker(assignment, tools):
        (Path(assignment.workspace.root) / "a.py").write_text(assignment.context["agent_id"] + "\n")
        return "done"
    owner.coordinator.register("coding", worker)
    first = await owner.spawn("worker", "one", write_paths=["a.py"])
    second = await owner.spawn("worker", "two", write_paths=["a.py"])
    await owner.wait(first["agent_id"])
    await owner.wait(second["agent_id"])
    proposal = await owner.inspect_changes(first["agent_id"])
    approvals = []
    async def approve(name, args, reason):
        approvals.append((name, args))
    owner.control.request_approval = approve
    other_id = second["workspace"]["workspace_id"]
    resolved = await owner.resolve_conflicts(first["agent_id"], proposal["digest"],
        {other_id: "Human inspected both independent versions and accepted this source as the explicit resolved result."})
    assert approvals[-1][0] == "resolve_subagent_conflicts"
    assert len(approvals[-1][1]["competing_patches"]) == 1
    assert not resolved["reviewed"]
    await owner.review_changes(first["agent_id"], proposal["digest"])
    await owner.integrate_changes(first["agent_id"], proposal["digest"])
    assert Path(first["workspace"]["path"]).exists() and Path(second["workspace"]["path"]).exists()
    assert (repo / "a.py").read_text() == first["agent_id"] + "\n"


@pytest.mark.asyncio
async def test_live_parent_excluded_paths_narrow_child_and_integration(repo):
    owner = manager(repo)
    async def worker(assignment, tools):
        read = await tools.execute(call("read_file", path="a.py"))
        snapshot = json.loads(read.content)
        owner.control.excluded_paths = [str(repo / "a.py")]
        edit = await tools.execute(call("edit_file", path="a.py", expected_hash=snapshot["hash"], old_text="1", new_text="2"))
        assert edit.is_error
        return "protected"
    owner.coordinator.register("coding", worker)
    spawned = await owner.spawn("worker", "change", write_paths=["a.py"])
    assert (await owner.wait(spawned["agent_id"]))["passed"]
    assert (Path(spawned["workspace"]["path"]) / "a.py").read_text() == "value = 1\n"

    # A new exclusion set after local work also prevents the integration route.
    owner = manager(repo)
    async def direct_worker(assignment, tools):
        (Path(assignment.workspace.root) / "a.py").write_text("changed\n")
        return "local change"
    owner.coordinator.register("coding", direct_worker)
    spawned = await owner.spawn("worker", "change", write_paths=["a.py"])
    result = await owner.wait(spawned["agent_id"])
    owner.control.excluded_paths = ["a.py"]
    with pytest.raises(PermissionError):
        await owner.review_changes(spawned["agent_id"], result["merge_proposal"]["digest"])
