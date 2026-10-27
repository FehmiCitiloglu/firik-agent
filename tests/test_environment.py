"""Environment facts must match command execution and reach every agent entry point."""
import json
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from firik_agent.core import environment, shell
from firik_agent.core.agent import Agent
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction


def snapshot_from_prompt(prompt):
    context = prompt.split("## Local execution environment\n", 1)[1]
    return json.loads("{" + context.split("\n{", 1)[1])


@pytest.mark.parametrize(
    "system, runtime_platform, env_shell, comspec, executable, syntax",
    [
        ("Darwin", "darwin", "/bin/zsh", "cmd.exe", "/bin/zsh", "posix"),
        ("Linux", "linux", "/bin/bash", "cmd.exe", "/bin/bash", "posix"),
        ("Linux", "linux", None, None, "/bin/sh", "posix"),
        ("Windows", "win32", "/bin/zsh", None, "cmd.exe", "cmd"),
        ("Windows", "win32", None, r"C:\Windows\System32\cmd.exe", r"C:\Windows\System32\cmd.exe", "cmd"),
        ("Windows", "win32", None, "pwsh.exe", "pwsh.exe", "powershell"),
    ],
)
def test_snapshot_matches_runner_shell(monkeypatch, tmp_path, system, runtime_platform,
                                       env_shell, comspec, executable, syntax):
    monkeypatch.setattr(environment.platform, "system", lambda: system)
    monkeypatch.setattr(shell, "sys", SimpleNamespace(platform=runtime_platform))
    for key, value in (("SHELL", env_shell), ("COMSPEC", comspec)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(environment.shutil, "which", lambda name: executable if name == shell.default_shell() else None)
    monkeypatch.setattr(environment.platform, "freedesktop_os_release", lambda: {"PRETTY_NAME": "Test Linux"})
    info = environment.get_environment_info(str(tmp_path))
    assert info["os"] == ("macOS" if system == "Darwin" else system)
    assert info["default_shell"] == shell.shell_args("echo test")[0] == executable
    assert info["shell_syntax"] == syntax
    assert info["default_shell_available"] is True
    assert info["working_directory"] == str(tmp_path)
    if syntax == "cmd":
        assert shell.shell_args("echo test")[1:4] == ["/d", "/s", "/c"]
    elif syntax == "powershell":
        assert shell.shell_args("echo test")[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    if system == "Linux":
        assert info["linux_distribution"] == "Test Linux"


def test_missing_shell_and_limited_inventory_do_not_expose_environment(monkeypatch):
    monkeypatch.setenv("SHELL", "/missing/shell")
    monkeypatch.setattr(shell, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setenv("FIRIK_API_KEY", "secret-must-not-appear")
    monkeypatch.setenv("UNRELATED_VARIABLE", "private-must-not-appear")
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    info = environment.get_environment_info()
    assert info["default_shell"] == "/missing/shell"
    assert info["default_shell_available"] is False
    assert info["available_commands"] == {"git": "/usr/bin/git"}
    prompt = environment.environment_prompt()
    assert "secret-must-not-appear" not in prompt
    assert "private-must-not-appear" not in prompt
    assert "choose a confirmed available interpreter explicitly" in prompt


def test_packaged_app_is_not_described_as_python_cli(monkeypatch):
    monkeypatch.setattr(environment.sys, "frozen", True, raising=False)
    monkeypatch.setattr(environment.sys, "executable", "/Applications/firik.app/Contents/MacOS/firik")
    info = environment.get_environment_info()
    assert info["runtime_is_packaged"] is True
    assert "packaged app, not a Python CLI" in environment.environment_prompt()


def test_relative_cwd_refresh_and_missing_linux_metadata(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(environment.platform, "system", lambda: "Linux")
    def no_os_release():
        raise OSError("os-release unavailable")
    monkeypatch.setattr(environment.platform, "freedesktop_os_release", no_os_release)
    assert environment.get_environment_info("project")["working_directory"] == str(tmp_path / "project")
    assert "linux_distribution" not in environment.get_environment_info()
    project = tmp_path / "other"
    project.mkdir()
    monkeypatch.chdir(project)
    assert environment.get_environment_info()["working_directory"] == str(project)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["run", "stream", "stream_fallback"])
async def test_environment_is_sent_before_first_response_and_refreshed(tmp_path, monkeypatch, mode):
    agent = Agent(config={
        "agent": {"system_prompt": "BASE ROLE"},
        "skills": {"directory": str(tmp_path / "skills")},
        "plugins": {"directory": str(tmp_path / "plugins"), "autoload_builtins": False},
    })
    session = ChatSession(system_prompt="SESSION ROLE")
    await agent.initialize()
    monkeypatch.setattr(agent.skills, "get_prompt_augmentation", lambda: "ACTIVE SKILL")
    provider = agent.providers.get_active()
    requests = []

    async def chat(messages, **kwargs):
        requests.append(messages)
        return Message(role="assistant", content="ok")

    async def stream(messages, **kwargs):
        if mode == "stream_fallback":
            raise NotImplementedError("stream unsupported")
        requests.append(messages)
        yield StreamChunk(delta_content="ok")

    monkeypatch.setattr(provider, "chat", chat)
    monkeypatch.setattr(provider, "chat_stream", stream)
    try:
        for cwd in (str(tmp_path / "first"), str(tmp_path / "second")):
            agent.default_cwd = cwd
            if mode == "run":
                assert await agent.run("Where am I?", session=session) == "ok"
            else:
                events = [event async for event in agent.run_stream("Where am I?", session=session)]
                assert events[-1].content == "ok"
            system_message = requests[-1][0]
            assert system_message.role == "system"
            assert all(value in system_message.content for value in ("BASE ROLE", "SESSION ROLE", "ACTIVE SKILL"))
            info = snapshot_from_prompt(system_message.content)
            assert info["working_directory"] == cwd
            assert info["default_shell"] == shell.shell_args("")[0]
            schemas = SessionToolFilter(agent.tools).get_schemas(session)
            assert "get_environment_info" in {schema["function"]["name"] for schema in schemas}
        assert len(requests) == 2
        assert all(message.role != "system" for message in session.messages)
        result = await SessionToolFilter(agent.tools).execute(ToolCall(
            id="env", function=ToolCallFunction(name="get_environment_info", arguments="{}")), session)
        assert not result.is_error
        assert json.loads(result.content)["working_directory"] == agent.default_cwd
    finally:
        await agent.close()


@pytest.mark.asyncio
async def test_real_local_execution_matches_snapshot(tmp_path):
    info = environment.get_environment_info(str(tmp_path))
    code = "import json, os, platform; print(json.dumps({'cwd': os.getcwd(), 'os': platform.system(), 'arch': platform.machine()}))"
    args = [sys.executable, "-c", code]
    command = subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
    result = await shell.run_shell(command, cwd=info["working_directory"])
    assert result.startswith("Exit code 0:\n")
    observed = json.loads(result.split("\n", 1)[1])
    assert observed["cwd"] == str(tmp_path.resolve())
    assert info["os"] == ("macOS" if observed["os"] == "Darwin" else observed["os"])
    assert info["architecture"] == observed["arch"]
    assert info["default_shell"] == shell.shell_args(command)[0]
