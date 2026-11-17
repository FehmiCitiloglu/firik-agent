"""Electron's command terminal shares scoped execution and agent approvals."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from firik_agent.core.types import StreamChunk, ToolCall, ToolCallFunction
from firik_agent.core.execution import BackendAvailability, CommandResult
from test_desktop_workspaces import coding_params, eventually, frames, workspace  # noqa: F401


def commands(call, project="p1"):
    return call("terminal.list", {"project_id": project})["result"]["commands"]


def test_terminal_streams_both_channels_and_retains_real_exit(workspace, python_command):
    _, _, call, _, roots, output = workspace
    script = roots["p1"] / "stream.py"
    script.write_text("import sys,time\nprint('live-output',flush=True)\nprint('live-error',file=sys.stderr,flush=True)\ntime.sleep(.6)\nsys.exit(7)\n")
    row = call("terminal.start", {"project_id": "p1", "command": python_command("-u", "stream.py")})["result"]
    assert row["source"] == "user" and row["boundary"] == "host"
    eventually(lambda: any("live-output" in item["stdout"] and item["status"] == "running" for item in commands(call)))
    eventually(lambda: commands(call)[0]["status"] == "completed")
    result = commands(call)[0]
    assert result["exit_code"] == 7 and "live-error" in result["stderr"]
    assert result["duration_seconds"] > 0
    assert commands(call, "p2") == []
    assert any(item.get("params", {}).get("kind") == "terminal" for item in frames(output))


def test_terminal_cancel_survives_renamed_project_and_reaps_process(workspace, python_command):
    _, _, call, _, roots, _ = workspace
    root = roots["p1"]
    script = root / "cancel.py"
    script.write_text("import time\nfrom pathlib import Path\nprint('ready',flush=True)\ntime.sleep(2)\nPath('must-not-exist').touch()\n")
    row = call("terminal.start", {"project_id": "p1", "command": python_command("-u", "cancel.py")})["result"]
    eventually(lambda: "ready" in commands(call)[0]["stdout"])
    assert "error" in call("terminal.cancel", {"project_id": "p2", "command_id": row["command_id"]})
    moved = root.with_name("renamed-project")
    root.rename(moved)
    try:
        assert call("terminal.cancel", {"project_id": "p1", "command_id": row["command_id"]})["result"]["ok"]
        eventually(lambda: commands(call)[0]["status"] == "cancelled")
        time.sleep(.1)
        assert not (moved / "must-not-exist").exists()
    finally:
        moved.rename(root)


def test_terminal_enforces_scope_permissions_and_action_policy(workspace, python_command):
    _, service, call, _, roots, _ = workspace
    (roots["p1"] / "link").symlink_to(roots["p2"], target_is_directory=True)
    base = {"project_id": "p1", "command": python_command("-c", "print('safe')")}
    for cwd in ("../p2", "link", "main.py"):
        assert "error" in call("terminal.start", {**base, "cwd": cwd})
    settings = service.projects.db.get_settings("p1")
    for prefs in ({"permissions": {"allow_terminal": False}}, {"permissions": {"allow_reads": False}},
                  {"excluded_files": ["private.py"]}, {"policy": {"shell.safe": "deny"}}):
        settings.coding_preferences = prefs
        service.projects.db.save_settings(settings)
        assert "error" in call("terminal.start", base)
    settings.coding_preferences = {"policy": {"network": "deny", "git.commit": "deny"}}
    service.projects.db.save_settings(settings)
    for command in ("curl https://example.test", "git commit -m fixture", "sudo echo blocked", "git push --force"):
        assert "error" in call("terminal.start", {**base, "command": command})
    assert commands(call) == []


def test_terminal_reservation_blocks_coding_git_and_preference_races(workspace, python_command):
    server, _, call, _, _, _ = workspace
    p = coding_params(call)
    row = call("terminal.start", {"project_id": "p1", "command": python_command("-c", "import time; time.sleep(5)")})["result"]
    try:
        assert "error" in call("terminal.start", {"project_id": "p1", "command": "echo second"})
        assert "error" in call("coding.start", {**p, "prompt": "İncele", "model": "fixture-model"})
        assert "error" in call("coding.preferences.update", {"project_id": "p1", "allow_reads": True,
            "allow_edits": True, "allow_terminal": False, "allow_tests": True})
        preview = call("git.preview", {"project_id": "p1", "tool_id": "stage", "arguments": {"paths": "main.py"}})["result"]
        assert "error" in call("git.execute", {"project_id": "p1", "token": preview["token"], "confirmed": True})
        other = call("terminal.start", {"project_id": "p2", "command": python_command("-c", "print('other project')")})["result"]
        assert other["project_id"] == "p2"
    finally:
        call("terminal.cancel", {"project_id": "p1", "command_id": row["command_id"]})
    eventually(lambda: not server.workspaces.terminal.busy())


def test_terminal_timeout_output_limits_and_known_key_redaction(workspace, python_command):
    _, _, call, _, roots, _ = workspace
    script = roots["p1"] / "output.py"
    script.write_text("import sys\nprint('fixture-only-key')\nprint('x'*100000)\n")
    call("state.snapshot")  # Refresh the same known-key mask as normal desktop startup.
    call("terminal.start", {"project_id": "p1", "command": python_command("-u", "output.py")})
    eventually(lambda: commands(call)[0]["status"] == "completed")
    row = commands(call)[0]
    assert row["truncated"] and len(row["stdout"]) <= 32768
    assert "fixture-only-key" not in row["stdout"]
    call("terminal.start", {"project_id": "p1", "command": python_command("-c", "import time; time.sleep(5)"), "timeout": .1})
    eventually(lambda: commands(call)[-1]["status"] == "timed_out")
    assert commands(call)[-1]["timed_out"] is True


def test_required_docker_never_falls_back_to_host(workspace, monkeypatch, python_command):
    _, service, call, _, roots, _ = workspace
    settings = service.projects.db.get_settings("p1")
    settings.coding_preferences["execution_config"] = {"backend": "docker"}
    service.projects.db.save_settings(settings)

    class MissingDocker:
        async def create_workspace(self, root):
            raise RuntimeError("Docker unavailable fixture")

    monkeypatch.setattr("firik_agent.desktop_rpc.terminal.build_backend", lambda config: MissingDocker())
    call("terminal.start", {"project_id": "p1", "command": python_command("-c", "from pathlib import Path; Path('host-fallback').touch()")})
    eventually(lambda: commands(call)[0]["status"] == "error")
    row = commands(call)[0]
    assert row["backend"] == "docker" and row["boundary"] == "sandbox"
    assert "Docker unavailable" in row["error"]
    assert not (roots["p1"] / "host-fallback").exists()


def test_agent_commands_appear_only_after_approval_and_stream_to_terminal(workspace, monkeypatch, python_command):
    server, service, call, _, roots, _ = workspace
    (roots["p1"] / "agent-command.py").write_text("import time\nfrom pathlib import Path\nprint('agent-live',flush=True)\nPath('agent-ran').touch()\ntime.sleep(.4)\n")
    command = python_command("-u", "agent-command.py")

    async def provider(messages, **kwargs):
        if not any(message.role == "tool" and message.name == "run_command" for message in messages):
            yield StreamChunk(tool_calls=[ToolCall(id="agent-terminal", function=ToolCallFunction(name="run_command", arguments=json.dumps({"command": command})))])
        else:
            yield StreamChunk(delta_content="Komut sonucu incelendi.")

    original = service._coding_worker
    def worker(*args, **kwargs):
        agent = original(*args, **kwargs)
        monkeypatch.setattr(agent.providers.get_active(), "chat_stream", provider)
        return agent
    monkeypatch.setattr(service, "_coding_worker", worker)
    p = coding_params(call)
    run = call("coding.start", {**p, "prompt": "Komutu çalıştır", "model": "fixture-model", "mode": "assisted"})["result"]
    eventually(lambda: bool(server.workspaces.coding_state(**p).approvals))
    assert not commands(call) and not (roots["p1"] / "agent-ran").exists()
    approval = server.workspaces.coding_state(**p).approvals[0]
    assert approval.tool_name == "run_command"
    call("coding.control", {**p, "run_id": run["run_id"], "action": "approve", "request_id": approval.request_id, "allowed": True})
    eventually(lambda: any(row["source"] == "agent" and "agent-live" in row["stdout"] for row in commands(call)))
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
    row = commands(call)[0]
    assert row["status"] == "completed" and row["exit_code"] == 0
    assert row["session_id"] == p["session_id"] and row["run_id"] == run["run_id"]
    assert "error" in call("terminal.cancel", {"project_id": "p1", "command_id": row["command_id"]})


def test_terminal_cancel_during_preparation_and_close_cleanup(workspace, monkeypatch, python_command):
    server, _, call, _, roots, _ = workspace
    entered, released = threading.Event(), threading.Event()

    class PreparingBackend:
        async def create_workspace(self, root):
            entered.set()
            await asyncio.to_thread(released.wait, 3)
            raise AssertionError("cancelled preparation may not continue")

    monkeypatch.setattr("firik_agent.desktop_rpc.terminal.build_backend", lambda config: PreparingBackend())
    row = call("terminal.start", {"project_id": "p1", "command": python_command("-c", "print('not reached')")})["result"]
    try:
        assert entered.wait(2)
        call("terminal.cancel", {"project_id": "p1", "command_id": row["command_id"]})
        eventually(lambda: commands(call)[0]["status"] == "cancelled")
        assert not server.workspaces.terminal.busy()
    finally:
        released.set()


def test_terminal_cancel_during_workspace_cleanup_keeps_reservation(workspace, monkeypatch):
    server, _, call, _, _, _ = workspace
    entered, released, interrupted = threading.Event(), threading.Event(), threading.Event()

    class CleaningBackend:
        async def create_workspace(self, root):
            return object()

        async def prepare_environment(self, root):
            return BackendAvailability(True, "host")

        async def execute_command(self, workspace, request):
            return CommandResult(request.command_id, "fixture", "host", "host", "completed", exit_code=0)

        async def destroy_workspace(self, workspace, *, discard):
            entered.set()
            try:
                while not released.is_set():
                    await asyncio.sleep(.01)
            except asyncio.CancelledError:
                interrupted.set()
                raise

    monkeypatch.setattr("firik_agent.desktop_rpc.terminal.build_backend", lambda config: CleaningBackend())
    row = call("terminal.start", {"project_id": "p1", "command": "fixture"})["result"]
    try:
        assert entered.wait(2)
        assert commands(call)[0]["status"] == "stopping"
        call("terminal.cancel", {"project_id": "p1", "command_id": row["command_id"]})
        assert server.workspaces.terminal.busy("p1")
        assert "error" in call("terminal.start", {"project_id": "p1", "command": "second"})
        assert not interrupted.wait(.05)
    finally:
        released.set()
    eventually(lambda: commands(call)[0]["status"] == "completed")
    assert not server.workspaces.terminal.busy("p1")


def test_terminal_close_cleans_other_commands_when_one_finishes_during_cancel(workspace, monkeypatch, python_command):
    server, _, call, _, roots, _ = workspace
    roots["p1"].joinpath("first.py").write_text(
        "import time\nfrom pathlib import Path\nprint('ready', flush=True)\n"
        "while not Path('release-first').exists(): time.sleep(.01)\n")
    first = call("terminal.start", {"project_id": "p1", "command": python_command("-u", "first.py")})["result"]
    second = call("terminal.start", {"project_id": "p2", "command": python_command("-u", "-c", "import time; print('ready',flush=True); time.sleep(30)")})["result"]
    eventually(lambda: all("ready" in commands(call, project)[0]["stdout"] for project in ("p1", "p2")))
    terminal = server.workspaces.terminal
    first_future = terminal._futures[first["command_id"]]
    original_cancel = terminal.cancel

    def finish_before_cancel(params):
        if params.command_id == first["command_id"]:
            roots["p1"].joinpath("release-first").touch()
            first_future.result(timeout=3)
        return original_cancel(params)

    monkeypatch.setattr(terminal, "cancel", finish_before_cancel)
    terminal.close()
    assert commands(call, "p1")[0]["status"] == "completed"
    assert commands(call, "p2")[0]["command_id"] == second["command_id"]
    assert commands(call, "p2")[0]["status"] == "cancelled"
    assert not terminal.busy()
    eventually(lambda: not terminal._futures)
