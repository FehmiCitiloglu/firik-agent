"""Boundary and completion regressions at the real coding/SDK dispatch points."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_coding_bridge import bridge as bridge

from firik_agent.core.execution import (BackendAvailability, CommandResult, ExecutionConfig,
                                        ExecutionWorkspace, SandboxUnavailable)
from firik_agent.core.session import ChatSession
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import ToolCall, ToolCallFunction, ToolDefinition
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter


def call(name, **arguments):
    return ToolCall(id="test", function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


class FakeSandbox:
    def __init__(self, root, *, matches=True, unavailable=False):
        self.root, self.matches, self.unavailable = root, matches, unavailable
        self.executed = 0

    async def create_workspace(self, root):
        if self.unavailable:
            raise SandboxUnavailable("Docker daemon unavailable")
        return ExecutionWorkspace("isolated", self.root, Path(root), "docker", True)

    async def prepare_environment(self, workspace):
        return BackendAvailability(True, "docker")

    async def execute_command(self, workspace, request):
        self.executed += 1
        return CommandResult(request.command_id, workspace.workspace_id, "docker", "sandbox", "completed",
                             exit_code=0, stdout="test passed", source_tree_matches=self.matches)

    async def inspect_status(self, workspace=None):
        return {"backend": "docker", "running": []}

    async def destroy_workspace(self, workspace):
        raise RuntimeError("artifacts retained")


@pytest.mark.asyncio
async def test_required_sandbox_unavailable_never_calls_host(tmp_path, monkeypatch):
    control = CodingRunControl("s", "p", execution_config={"backend": "docker"})
    tools = CodingToolFilter(tmp_path, control)
    tools.execution_backend = FakeSandbox(tmp_path, unavailable=True)

    async def forbidden(*args, **kwargs):
        pytest.fail("unrestricted host fallback")

    monkeypatch.setattr("firik_agent.projects.coding_runtime.run_shell", forbidden)
    with pytest.raises(SandboxUnavailable):
        await tools.run_command("touch should-not-exist")
    assert control.snapshot()["execution"]["available"] is False
    assert not (tmp_path / "should-not-exist").exists()


@pytest.mark.asyncio
async def test_sandbox_source_mutation_cannot_verify_user_workspace(tmp_path):
    (tmp_path / "source.py").write_text("print('original')")
    control = CodingRunControl("s", "p", mode="auto", execution_config={"backend": "docker"})
    tools = CodingToolFilter(tmp_path, control)
    tools.execution_backend = FakeSandbox(tmp_path, matches=False)
    result = json.loads((await tools.run_tests("test then mutate source")).content)
    assert result["exit_code"] == 0
    assert result["fresh"] is False
    assert result["evidence_id"] not in tools._verified_evidence
    assert result["execution_boundary"] == "sandbox"


@pytest.mark.asyncio
async def test_docker_network_cannot_bypass_parent_network_deny(tmp_path):
    session = ChatSession()
    control = CodingRunControl(session.session_id, "p", mode="auto", policy={"network": "deny"},
        execution_config={"backend": "docker", "profile": "development", "network": True, "network_approved": True})
    tools = CodingToolFilter(tmp_path, control)
    tools.execution_backend = backend = FakeSandbox(tmp_path)
    result = await tools.execute(call("run_command", command="python harmless-looking-script.py"), session)
    assert result.is_error
    assert backend.executed == 0
    assert result.metadata["execution_boundary"] == "sandbox"


@pytest.mark.asyncio
async def test_sandbox_does_not_label_mcp_as_sandboxed(tmp_path):
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="outside", description="external", source="mcp:example",
                                     execution_boundary="sandbox"), lambda: "outside")
    assert registry.get_tool("outside").execution_boundary == "remote_mcp"
    result = await registry.execute(call("outside"))
    assert result.metadata["execution_boundary"] == "remote_mcp"
    control = CodingRunControl("s", "p", execution_config={"backend": "docker"})
    tools = CodingToolFilter(tmp_path, control)
    assert tools.tools.get_tool("read_file").execution_boundary == "host"
    assert tools.tools.get_tool("run_command").execution_boundary == "sandbox"


@pytest.mark.asyncio
async def test_retained_artifacts_are_not_deleted_on_run_close(tmp_path):
    tools = CodingToolFilter(tmp_path, CodingRunControl("s", "p", execution_config={"backend": "docker"}))
    tools.execution_backend = backend = FakeSandbox(tmp_path)
    tools.execution_workspace = await backend.create_workspace(tmp_path)
    assert (await tools.close_execution())["retained"]
    assert tools.control.execution_status["state"] == "retained"


@pytest.mark.asyncio
async def test_sdk_docker_selection_cannot_fall_back(tmp_path, monkeypatch):
    from firik_agent.core.agent import Agent
    agent = Agent.__new__(Agent)
    agent.execution_config = ExecutionConfig(backend="docker")
    agent._execution_backend = FakeSandbox(tmp_path, unavailable=True)
    agent._execution_workspaces = {}
    with pytest.raises(SandboxUnavailable):
        await agent.run_command_async("echo nope", cwd=str(tmp_path))


def test_doctor_reports_unavailable_without_provider_or_fallback(monkeypatch, capsys):
    from firik_agent.projects import sandbox_cli
    class Unavailable:
        async def availability(self):
            return BackendAvailability(False, "docker", "daemon stopped")
    monkeypatch.setattr(sandbox_cli, "build_backend", lambda config: Unavailable())
    assert sandbox_cli.main(["doctor"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["host_fallback"] is False
    assert result["external_tools_sandboxed"] is False
    assert result["available"] is False


def test_manual_desktop_action_inherits_required_docker(bridge, monkeypatch, python_command):
    from test_cockpit_actions import manual_session
    from firik_agent.core.events import AgentEventType
    service, _, roots, session = manual_session(bridge)
    settings = service.projects.db.get_settings("p1")
    settings.coding_preferences["execution_config"] = {"backend": "docker"}
    service.projects.db.save_settings(settings)
    monkeypatch.setattr("firik_agent.projects.coding_runtime.build_backend",
        lambda config: FakeSandbox(roots["p1"], unavailable=True))
    def approve(event):
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            service.coding_control(session.session_id, "approve", event.metadata["request_id"], True)
    command = python_command("-c", "from pathlib import Path; Path('host-fallback').touch()")
    result = service.run_coding_command_async(session.session_id, command, lambda result: None,
        lambda error: None, on_event=approve).result(timeout=5)
    assert result["status"] == "failed"
    assert not (roots["p1"] / "host-fallback").exists()
    assert service.coding_store.latest_run("p1", session_id=session.session_id).usage["execution"]["backend"] == "docker"
