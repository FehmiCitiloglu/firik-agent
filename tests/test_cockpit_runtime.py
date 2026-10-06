"""Policy boundaries, dependency execution and current-source quality evidence."""
import asyncio
import json

import pytest

from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.types import ToolCall, ToolCallFunction
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.quality_gates import discover_quality_gates


def call(name, **arguments):
    return ToolCall(id="cockpit", function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


def runtime(tmp_path, mode="assisted", **options):
    (tmp_path / "main.py").write_text("value = 1\n")
    session = ChatSession(session_id="cockpit")
    control = CodingRunControl(session.session_id, "project", mode=mode, **options)
    tools = CodingToolFilter(tmp_path, control)
    return session, control, tools


def approve(control):
    events = []
    def receive(event):
        events.append(event)
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            control.decide_approval(event.metadata["request_id"], True)
    control.on_event = receive
    return events


@pytest.mark.asyncio
async def test_actor_scopes_follow_threads_and_concurrent_approvals_without_leaking(tmp_path):
    _, control, _ = runtime(tmp_path)
    events = approve(control)
    entered = asyncio.Event()
    async def first():
        with control.actor("worker-a"):
            entered.set()
            await asyncio.sleep(.01)
            await asyncio.to_thread(control.emit, AgentEventType.FILE_CHANGED, "A", path="a.py")
            await control.request_approval("run_tests", {"command": "A"}, "A")
            with control.actor("nested"):
                control.emit(AgentEventType.VERIFICATION, "nested")
            control.emit(AgentEventType.VERIFICATION, "A-result")
    async def second():
        await entered.wait()
        with control.actor("worker-b"):
            await asyncio.to_thread(control.emit, AgentEventType.FILE_CHANGED, "B", path="b.py")
            await control.request_approval("run_tests", {"command": "B"}, "B")
    await asyncio.gather(first(), second())
    control.emit(AgentEventType.TASK_STATE, "parent")
    assert events[-1].metadata.get("agent_id") is None
    expected = {"A": "worker-a", "B": "worker-b", "A-result": "worker-a", "nested": "nested"}
    for event in events:
        if event.content in expected:
            assert event.metadata["agent_id"] == expected[event.content]
            assert event.metadata["parent_agent_id"] == control.run_id
    required = [event for event in events if event.type == AgentEventType.APPROVAL_REQUIRED]
    assert {event.metadata["agent_id"] for event in required} == {"worker-a", "worker-b"}
    assert {decision["agent_id"] for decision in control.decisions} == {"worker-a", "worker-b"}
    with pytest.raises(RuntimeError):
        with control.actor("failed"):
            raise RuntimeError("reset on exception")
    assert control.actor_metadata() == {}


@pytest.mark.parametrize("mode,write", [("safe", "ask"), ("assisted", "allow"), ("autonomous", "allow"),
                                        ("custom", "ask"), ("approved", "ask"), ("auto", "allow")])
def test_modes_have_explicit_policy_and_keep_host_shell_boundary(mode, write):
    control = CodingRunControl("s", "p", mode=mode, policy={"shell.safe": "allow", "secrets": "allow"})
    assert control.permission_decision("filesystem.write") == write
    assert control.permission_decision("shell.safe") == "ask"
    assert control.permission_decision("secrets") == "deny"
    assert control.snapshot()["sandbox"] == "none"


def test_inherited_policy_and_boolean_permissions_cannot_be_widened():
    control = CodingRunControl("s", "p", mode="custom", policy={"filesystem.write": "allow", "spawn_agents": "allow"},
                               inherited_policy={"filesystem.write": "deny", "spawn_agents": "ask"})
    assert control.permission_decision("filesystem.write") == "deny"
    assert control.permission_decision("spawn_agents") == "ask"
    control.permissions["allow_subagents"] = False
    assert control.permission_decision("spawn_agents") == "deny"
    with pytest.raises(ValueError):
        control.set_policy({"filesystem.write": "sometimes"})
    with pytest.raises(ValueError):
        control.set_policy({"unrestricted": "allow"})


@pytest.mark.asyncio
async def test_ai_excluded_paths_are_hidden_from_runtime_tools_and_denied_direct_reads(tmp_path):
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "notes.py").write_text("sensitive_context = 'private'\n")
    session, control, tools = runtime(tmp_path, excluded_paths=["private"])
    result = await tools.execute(call("read_file", path="private/notes.py"), session)
    assert result.is_error
    assert "private" not in json.dumps(tools.list_files()["entries"])
    assert tools.search_project("sensitive_context")["matches"] == []
    result = await tools.execute(call("write_file", path="private/new.py", content="generated", expected_hash="new"), session)
    assert result.is_error and not (tmp_path / "private" / "new.py").exists()
    assert control.permission_decision("shell.safe") == "deny"
    assert control.permission_decision("mcp") == "deny"
    control.excluded_paths = []
    assert not (await tools.execute(call("read_file", path="private/notes.py"), session)).is_error


@pytest.mark.asyncio
async def test_safe_edits_await_review_and_denied_live_policy_wins(tmp_path):
    session, control, tools = runtime(tmp_path, "safe")
    tools.update_plan([{"id": "edit", "title": "Edit", "acceptance_criteria": ["value changes"], "verification": "tests"}])
    revision = tools.read_file("main.py")["hash"]
    future = asyncio.create_task(tools.execute(call("edit_file", path="main.py", expected_hash=revision,
                                                 old_text="value = 1", new_text="value = 2"), session))
    for _ in range(100):
        if control.pending_approvals:
            break
        await asyncio.sleep(.001)
    assert control.pending_approvals and (tmp_path / "main.py").read_text() == "value = 1\n"
    request = next(iter(control.pending_approvals))
    control.set_policy({"filesystem.write": "deny"})
    assert control.decide_approval(request, True)
    assert not control.decide_approval(request, False)
    assert (await future).is_error
    assert (tmp_path / "main.py").read_text() == "value = 1\n"


@pytest.mark.asyncio
async def test_shell_network_sudo_and_force_push_are_checked_before_process_start(tmp_path):
    session, control, tools = runtime(tmp_path, policy={"network": "deny"})
    approve(control)
    for command in ("curl https://example.com", "sudo touch marker", "git push --force origin main"):
        result = await tools.execute(call("run_command", command=command), session)
        assert result.is_error
    assert not (tmp_path / "marker").exists()
    assert not control.decisions


def test_dependency_plan_rejects_cycles_missing_ids_and_premature_execution(tmp_path):
    _, _, tools = runtime(tmp_path)
    with pytest.raises(ValueError, match="döngü"):
        tools.update_plan([{"id": "a", "title": "A", "dependencies": ["b"]},
                           {"id": "b", "title": "B", "dependencies": ["a"]}])
    with pytest.raises(ValueError, match="mevcut"):
        tools.update_plan([{"id": "a", "title": "A", "dependencies": ["missing"]}])
    with pytest.raises(ValueError, match="bağımlılıkları"):
        tools.update_plan([{"id": "a", "title": "A"}, {"id": "b", "title": "B", "status": "in_progress", "dependencies": ["a"]}])
    plan = tools.update_plan([{"id": "a", "title": "A", "status": "in_progress", "assignedAgent": "worker", "description": "Read"},
                              {"id": "b", "title": "B", "dependencies": ["a"]}])["steps"]
    assert plan[0]["started_at"] and plan[0]["assigned_agent"] == "worker"
    with pytest.raises(ValueError, match="zaman"):
        tools.update_plan([{"id": "a", "title": "A", "startedAt": "tomorrow"}])
    with pytest.raises(ValueError, match="bağımlılıkları"):
        tools.edit_file("main.py", tools.read_file("main.py")["hash"], "value = 1", "value = 2", step_id="b")
    tools.edit_file("main.py", tools.read_file("main.py")["hash"], "value = 1", "value = 2", step_id="a")
    assert tools.control.plan[0]["status"] == "in_progress" and tools.control.plan[0]["started_at"]


def test_failed_checkpoint_or_racing_user_edit_prevents_mutation(tmp_path):
    _, _, tools = runtime(tmp_path)
    original = (tmp_path / "main.py").read_bytes()
    revision = tools.read_file("main.py")["hash"]
    def failed(path):
        assert path == "main.py"
        raise OSError("checkpoint storage unavailable")
    tools.before_file_mutation = failed
    with pytest.raises(OSError, match="storage"):
        tools.edit_file("main.py", revision, "value = 1", "value = 2")
    assert (tmp_path / "main.py").read_bytes() == original
    def user_edit(path):
        (tmp_path / path).write_text("value = 'user'\n")
    tools.before_file_mutation = user_edit
    with pytest.raises(ValueError, match="değişti"):
        tools.edit_file("main.py", revision, "value = 1", "value = 2")
    assert "user" in (tmp_path / "main.py").read_text()


@pytest.mark.asyncio
async def test_old_green_evidence_cannot_complete_changed_or_externally_edited_code(tmp_path, python_command):
    session, control, tools = runtime(tmp_path)
    approve(control)
    command = python_command("-c", "assert True")
    evidence = json.loads((await tools.execute(call("run_tests", command=command), session)).content)
    completed = {"id": "a", "title": "A", "status": "completed", "acceptance_criteria": ["Works"],
                 "verification": command, "evidence_ids": [evidence["evidence_id"]]}
    tools.update_plan([completed])
    assert tools.completion_gate()["passed"]
    change = tools.edit_file("main.py", tools.read_file("main.py")["hash"], "value = 1", "value = 2")
    assert not tools.completion_gate()["passed"]
    assert control.verifications[0]["fresh"] is False and control.plan[0]["status"] == "pending"
    with pytest.raises(ValueError, match="kanıt"):
        tools.update_plan([completed])
    tools.undo_change(change["change_id"])
    evidence = json.loads((await tools.execute(call("run_tests", command=command), session)).content)
    completed["evidence_ids"] = [evidence["evidence_id"]]
    tools.update_plan([completed])
    (tmp_path / "main.py").write_text("value = 3\n")
    with pytest.raises(ValueError, match="kanıt"):
        tools.record_verification("Works", "passed", evidence["evidence_id"])


def test_quality_discovery_uses_manifest_scripts_and_never_executes_them(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "touch marker", "lint": "eslint .", "typecheck": "tsc", "build": "vite build"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
    gates = discover_quality_gates(tmp_path)
    assert {gate.kind for gate in gates} == {"tests", "lint", "typecheck", "build"}
    assert all(gate.command.startswith("pnpm run ") for gate in gates)
    assert not (tmp_path / "marker").exists()
    (tmp_path / "package.json").unlink()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="example"\ndependencies=["pytest", "ruff", "mypy"]\n[build-system]\nrequires=["setuptools"]\n')
    assert {gate.kind for gate in discover_quality_gates(tmp_path)} == {"tests", "lint", "typecheck", "build"}


@pytest.mark.asyncio
async def test_required_quality_gate_uses_real_exit_result_and_blocks_stale_success(tmp_path, python_command):
    _, control, tools = runtime(tmp_path)
    events = approve(control)
    tools.configure_quality_gates([{"id": "tests", "kind": "tests", "command": python_command("-c", "assert True")}])
    assert not tools.completion_gate()["passed"]
    result = json.loads((await tools.run_quality_gate("tests")).content)
    assert result["executed"] and result["passed"] and result["exit_code"] == 0 and result["duration_sec"] >= 0
    assert tools.completion_gate()["passed"]
    assert any(event.type == AgentEventType.APPROVAL_REQUIRED for event in events)
    tools.configure_quality_gates([{"id": "tests", "kind": "tests", "command": python_command("-c", "assert 1 == 1")}])
    assert not tools.completion_gate()["passed"]  # Same gate id, different command needs new evidence.
    await tools.run_quality_gate("tests")
    (tmp_path / "main.py").write_text("value = 9\n")
    assert not (await tools.completion_gate_async())["passed"]
    assert control.quality_results[-1]["status"] == "stale"
    tools.configure_quality_gates([{"id": "tests", "kind": "tests", "command": python_command("-c", "raise SystemExit(4)")}])
    failure = await tools.run_quality_gate("tests")
    assert failure.is_error and json.loads(failure.content)["exit_code"] == 4
    assert not tools.completion_gate()["passed"]
