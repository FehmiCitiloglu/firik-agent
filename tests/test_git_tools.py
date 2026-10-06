"""Git tools must operate on actual repository data and respect session cwd."""
import asyncio
import json
import os
import subprocess

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.types import ToolCall, ToolCallFunction
from firik_agent.projects.git_service import GitService


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test Developer")
    git(tmp_path, "config", "user.email", "dev@example.test")
    (tmp_path / "code.py").write_text("first = 1\nsecond = 2\n")
    git(tmp_path, "add", "code.py")
    git(tmp_path, "commit", "-m", "Initial code")
    return tmp_path


def test_status_preserves_paths_rename_and_first_record(repo):
    (repo / "code.py").write_text("first = 3\nsecond = 2\n")
    filename = " çorba tarif.py" if os.name == "nt" else " çorba -> tarif\n.py"
    (repo / filename).write_text("hello\n")
    status = GitService.get_status(repo)
    assert status["modified"][0]["path"] == "code.py"
    assert status["untracked"][0]["path"] == filename
    assert not status["clean"] and not status["upstream_known"]
    git(repo, "restore", "code.py")
    git(repo, "mv", "code.py", "yeni kod.py")
    rename = GitService.get_status(repo)["staged"][0]
    assert rename["path"] == "yeni kod.py"
    assert rename["original_path"] == "code.py"


def test_status_error_is_not_clean_and_unborn_branch(tmp_path):
    assert not GitService.get_status(tmp_path)["clean"]
    assert GitService.get_status(tmp_path)["error"]
    git(tmp_path, "init", "-b", "development")
    assert GitService.get_current_branch(tmp_path) == "development"
    assert GitService.get_status(tmp_path)["clean"]
    nested = tmp_path / "nested"
    nested.mkdir()
    assert GitService.is_git_repo(nested)


def test_literal_path_diff_and_leading_indentation(repo):
    (repo / "code.py").write_text("    first = 3\nsecond = 2\n")
    assert "+    first = 3" in GitService.get_diff(repo, file_path="code.py")
    with pytest.raises(ValueError):
        GitService.get_diff(repo, file_path="../outside.py")


def test_selected_diff_uses_repository_paths_from_nested_project(repo):
    nested = repo / "sub"
    nested.mkdir()
    (nested / "code.py").write_text("before\n")
    git(repo, "add", "sub/code.py")
    git(repo, "commit", "-m", "Add nested code")
    (nested / "code.py").write_text("after\n")
    assert GitService.get_status(nested)["files"][0]["path"] == "sub/code.py"
    assert "+after" in GitService.get_diff(nested, file_path="sub/code.py", strict=True)


@pytest.mark.asyncio
async def test_registered_git_tools_real_session_execution(repo):
    agent = Agent(config={"agent": {}, "skills": {"directory": str(repo / "skills")},
                          "plugins": {"directory": str(repo / "plugins"), "autoload_builtins": False}})
    agent.default_cwd = str(repo)
    agent._register_meta_tools()
    session = ChatSession()
    gate = SessionToolFilter(agent.tools)
    assert {"git_inspect", "git_tools", "git_prepare"} <= {s["function"]["name"] for s in gate.get_schemas(session)}

    async def call(name, args):
        result = await gate.execute(ToolCall(id="test", function=ToolCallFunction(name=name, arguments=json.dumps(args))), session)
        assert not result.is_error, result.content
        return json.loads(result.content)

    history = await call("git_inspect", {"view": "file_history", "file_path": "code.py"})
    assert history[0]["author"] == "Test Developer"
    git(repo, "switch", "-c", "side-history")
    (repo / "code.py").write_text("first = 4\nsecond = 2\n")
    git(repo, "add", "code.py")
    git(repo, "commit", "-m", "Side branch change")
    git(repo, "switch", "main")
    selected_history = await call("git_inspect", {"view": "file_history", "file_path": "code.py", "ref": "side-history"})
    assert selected_history[0]["message"] == "Side branch change"
    default_history = await call("git_inspect", {"view": "file_history", "file_path": "code.py"})
    assert default_history[0]["message"] == "Initial code"
    blame = await call("git_inspect", {"view": "blame", "file_path": "code.py"})
    assert blame[0]["author"] == "Test Developer"
    catalog = await call("git_tools", {})
    assert len(catalog) >= 30
    (repo / "code.py").write_text("first = 99\nsecond = 2\n")
    mutation = next(tool for tool in catalog if tool["id"] == "stage")
    pending = await call("git_tools", {"tool_id": mutation["id"], "params": {"paths": ["code.py"]}})
    assert pending["requires_confirmation"] and not pending["executed"]
    assert not GitService.get_status(repo)["staged"]
    assert GitService.get_status(repo)["modified"][0]["path"] == "code.py"
    session.enabled_tool_names = {"git_inspect"}
    assert not gate.is_tool_allowed("git_tools", session)


def test_inherited_git_environment_cannot_redirect_repository(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "missing.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "missing"))
    assert GitService.is_git_repo(repo)
    assert GitService.get_current_branch(repo) == "main"


@pytest.mark.asyncio
async def test_git_query_does_not_block_agent_event_loop(repo, monkeypatch):
    import threading
    from firik_agent.projects.git_tools import GitAgentTools
    entered, release = threading.Event(), threading.Event()
    def query(*args, **kwargs):
        entered.set()
        release.wait(timeout=3)
        return {"clean": True}
    monkeypatch.setattr(GitService, "get_status", query)
    agent = type("AgentContext", (), {"default_cwd": str(repo)})()
    pending = asyncio.create_task(GitAgentTools(agent).inspect())
    try:
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set() and not pending.done()
    finally:
        release.set()
    assert await pending == {"clean": True}
