from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from rich.console import Console

from firik_agent import cli
from firik_agent.core.agent import Agent
from firik_agent.core.shell import OUTPUT_LIMIT, run_shell, shell_args
from firik_agent.core.types import ToolCall, ToolCallFunction


def python_command(code):
    args = [sys.executable, "-c", code]
    return subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)


@pytest.mark.asyncio
async def test_shell_output_errors_and_working_directory(tmp_path):
    command = python_command("import os, sys; print(os.getcwd()); print('problem', file=sys.stderr); sys.exit(7)")
    result = await run_shell(command, cwd=str(tmp_path))
    assert "Exit code 7" in result
    assert str(tmp_path.resolve()) in result
    assert "[stderr]:\nproblem" in result


@pytest.mark.asyncio
async def test_shell_output_is_bounded_without_deadlock():
    result = await run_shell(python_command("import sys; print('x' * 100000); print('y' * 100000, file=sys.stderr)"))
    assert "Exit code 0" in result
    assert result.count("output truncated") == 2
    assert len(result) < OUTPUT_LIMIT * 2 + 200


@pytest.mark.asyncio
async def test_timeout_retains_partial_output():
    result = await run_shell(python_command("import time; print('started', flush=True); time.sleep(10)"), timeout=2)
    assert "timed out" in result
    assert "started" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, float('nan'), float('inf'), True, '10'])
async def test_invalid_timeouts(timeout):
    assert "timeout must be" in await run_shell("echo unused", timeout=timeout)


@pytest.mark.asyncio
async def test_blank_and_missing_shell():
    assert "cannot be empty" in await run_shell("  ")
    assert "Shell not found" in await run_shell("echo unused", shell="firik-nonexistent-shell")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell syntax")
@pytest.mark.asyncio
async def test_shell_quoting_pipes_and_explicit_interpreter():
    result = await run_shell("printf '%s' 'hello world' | tr '[:lower:]' '[:upper:]'", shell="sh")
    assert result == "Exit code 0:\nHELLO WORLD"


def test_windows_shell_arguments(monkeypatch):
    monkeypatch.setattr("firik_agent.core.shell.shutil.which", lambda name: name)
    assert shell_args('echo "hello world"', 'cmd') == ['cmd', '/d', '/s', '/c', 'echo "hello world"']
    assert shell_args('Write-Output "hello world"', 'pwsh') == ['pwsh', '-NoProfile', '-NonInteractive', '-Command', 'Write-Output "hello world"']


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_timeout_kills_descendants(tmp_path):
    marker = tmp_path / 'should-not-exist'
    child = f"import time; from pathlib import Path; time.sleep(4); Path({str(marker)!r}).touch()"
    parent = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); print('spawned', flush=True); time.sleep(10)"
    result = await run_shell(python_command(parent), timeout=2)
    assert "spawned" in result
    assert "timed out" in result
    await asyncio.sleep(4.2)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_bang_bypasses_model_and_preserves_tool_context(monkeypatch):
    agent = Agent(config={"agent": {}})
    chat = AsyncMock(side_effect=AssertionError("Model must not be called"))
    monkeypatch.setattr(agent.providers.get_active(), "chat", chat)
    monkeypatch.setattr(agent, "initialize", AsyncMock(side_effect=AssertionError("No initialization needed")))
    result = await agent.run("!" + python_command("print('local result')"))
    assert "local result" in result
    assert [message.role for message in agent.messages] == ['user', 'assistant', 'tool']
    assert agent.messages[-1].tool_call_id == agent.messages[-2].tool_calls[0].id
    assert agent.messages[-1].content == result
    assert await agent.run("!   ") == "Usage: !<command> (for example: !ls -la)"
    assert len(agent.messages) == 3
    chat.assert_not_called()
    await agent.close()


@pytest.mark.asyncio
async def test_model_tool_uses_async_runner(tmp_path):
    agent = Agent(config={"agent": {}})
    agent._register_meta_tools()
    result = await agent.tools.execute(ToolCall(id="call_shell", function=ToolCallFunction(
        name="run_command", arguments=json.dumps({"command": python_command("print('tool result')"), "cwd": str(tmp_path), "timeout": 3}),
    )))
    assert "Exit code 0:\ntool result" == result.content
    await agent.close()


@pytest.mark.asyncio
async def test_slash_shell_preserves_raw_command_and_literal_output(monkeypatch):
    agent = Agent(config={"agent": {}})
    runner = AsyncMock(return_value="Exit code 0:\n[bold]literal[/bold]")
    monkeypatch.setattr(agent, "run_local_command", runner)
    output = Console(record=True, width=120)
    monkeypatch.setattr(cli, "console", output)
    assert await cli.handle_slash_command(agent, '/shell printf "%s" "a  b" | cat')
    runner.assert_awaited_once_with('printf "%s" "a  b" | cat')
    assert "[bold]literal[/bold]" in output.export_text()
    await agent.close()


@pytest.mark.asyncio
async def test_ctrl_c_cancels_shell_and_preserves_session(monkeypatch):
    agent = Agent(config={"agent": {}})
    started = asyncio.Event()

    async def slow_command(command):
        started.set()
        return await run_shell(command)

    monkeypatch.setattr(agent, "run_command_async", slow_command)
    previous = signal.getsignal(signal.SIGINT)
    task = asyncio.create_task(cli.handle_shell_command(agent, python_command("import time; time.sleep(10)")))
    await started.wait()
    await asyncio.sleep(0.1)
    signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
    await asyncio.wait_for(task, timeout=3)
    assert signal.getsignal(signal.SIGINT) == previous
    assert agent.messages[-1].content == "Command interrupted by user."
    assert "still running" in await agent.run("!" + python_command("print('still running')"))
    await agent.close()


def test_single_shot_bang_needs_no_credentials(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["firik-agent", "-p", "!" + python_command("print('single shot')")])
    monkeypatch.setattr(cli, "ensure_provider_key", lambda *args: pytest.fail("Requested API key"))
    cli.main()
    assert "single shot" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_repl_routes_bang_without_model(monkeypatch):
    agent = Agent(config={"agent": {}})
    inputs = iter(["!echo repl", "/exit"])

    class Session:
        async def prompt_async(self, *args):
            return next(inputs)

    runner = AsyncMock()
    monkeypatch.setattr(cli, "PromptSession", lambda **kwargs: Session())
    monkeypatch.setattr(cli, "handle_shell_command", runner)
    monkeypatch.setattr(agent, "run", AsyncMock(side_effect=AssertionError("Unexpected chat")))
    await cli.run_repl(agent)
    runner.assert_awaited_once_with(agent, "echo repl")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows cmd quoting regression")
@pytest.mark.asyncio
async def test_cmd_quoted_executable_and_shell_operators(tmp_path):
    # A quoted executable path must survive cmd /c along with nested argument quotes.
    command = f'"{sys.executable}" -c "print(\'hello world\')" && echo second'
    result = await run_shell(command, shell="cmd.exe", cwd=str(tmp_path))
    assert "Exit code 0" in result
    assert "hello world" in result
    assert "second" in result
