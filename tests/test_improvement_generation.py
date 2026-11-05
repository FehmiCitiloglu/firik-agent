"""Existing Agent patch generation with a real permission owner and mock model.

No network, credentials or generated-code execution is needed. These tests
exercise source staging, provider requests and forbidden hallucinated tools.
"""
from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
import subprocess

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction
from firik_agent.providers.base import BaseProvider
from firik_agent.providers.registry import ProviderRegistry
from firik_agent.self_improvement.candidates import CandidateEngine, CandidateError


class PatchProvider(BaseProvider):
    def __init__(self, patch, *, tools=False, change_model=False, wait=False):
        super().__init__("fixture", "fixture://offline", default_model="pinned-v1")
        self.patch, self.tool_attempt, self.change_model, self.wait = patch, tools, change_model, wait
        self.requests = []
        self.model_metadata[self.current_model] = {"id": self.current_model, "context_window": 32768,
            "supports_streaming": True, "source": "configured", "verified": True}

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        raise AssertionError("Generation must not silently change request interface")

    async def chat_stream(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.requests.append((messages, tools))
        assert not tools
        if self.wait:
            await asyncio.Event().wait()
        if self.change_model:
            self.current_model = "unexpected-model"
        if self.tool_attempt:
            yield StreamChunk(tool_calls=[ToolCall(id="bad", function=ToolCallFunction(
                name="write_file", arguments='{"path":"/tmp/forbidden","content":"bad"}'))])
        else:
            yield StreamChunk(delta_content=self.patch)

    async def list_models(self):
        return []


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True, text=True, timeout=10).stdout
    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (root / "example.py").write_bytes(b"def twice(value):\n    return value\n")
    git("add", "example.py")
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "baseline")
    patch = "".join(difflib.unified_diff("def twice(value):\n    return value\n".splitlines(keepends=True),
        "def twice(value):\n    return value * 2\n".splitlines(keepends=True),
        fromfile="a/example.py", tofile="b/example.py"))
    return root, patch


async def existing_agent(tmp_path, monkeypatch, provider):
    registry = ProviderRegistry()
    registry.register(provider.name, provider, set_active=True)
    monkeypatch.setattr(ProviderRegistry, "from_config", classmethod(lambda cls, config: registry))
    agent = Agent(config={"agent": {"max_iterations": 2}, "providers": {}, "mcp_servers": {},
        "self_improvement": {"mode": "off"},
        "skills": {"directory": str(tmp_path / "skills"), "active_skills": []},
        "plugins": {"directory": str(tmp_path / "plugins"), "autoload_builtins": False}})
    await agent.initialize()
    return agent


@pytest.mark.asyncio
async def test_existing_agent_generates_only_staged_patch_without_tool_or_host_edits(tmp_path, monkeypatch, repo):
    root, patch = repo
    provider = PatchProvider(patch)
    agent = await existing_agent(tmp_path, monkeypatch, provider)
    engine = CandidateEngine(state_root=tmp_path / "state")
    payload = await engine.generate_code(agent, root, ["example.py"], "Fix twice to multiply by two.")
    assert payload["generation"]["tools_granted"] == []
    assert payload["generation"]["provider"] == "fixture" and payload["generation"]["model"] == "pinned-v1"
    assert provider.requests and all(not schemas for _, schemas in provider.requests)
    assert (root / "example.py").read_text().endswith("return value\n")
    assert "return value * 2" in Path(payload["workspace_path"], "example.py").read_text()
    assert payload["execution_boundary"] == "not_executed"
    assert not engine.validate("code", payload)["passed"]


@pytest.mark.asyncio
async def test_hallucinated_model_tool_cannot_invoke_existing_meta_write(tmp_path, monkeypatch, repo):
    root, patch = repo
    provider = PatchProvider(patch, tools=True)
    agent = await existing_agent(tmp_path, monkeypatch, provider)
    async def forbidden(*args, **kwargs):
        pytest.fail("A hallucinated tool reached the host registry")
    agent.tools._handlers["write_file"] = forbidden
    engine = CandidateEngine(state_root=tmp_path / "state")
    with pytest.raises(CandidateError, match="could not complete"):
        await engine.generate_code(agent, root, ["example.py"], "Propose a scoped fix.")
    assert (root / "example.py").read_text().endswith("return value\n")
    records = list((tmp_path / "state" / "code").glob("*/records/*.json"))
    assert records and all('"state":"destroyed"' in record.read_text().replace(" ", "") for record in records)


@pytest.mark.asyncio
async def test_generation_timeout_closes_native_run_and_snapshot(tmp_path, monkeypatch, repo):
    root, patch = repo
    provider = PatchProvider(patch, wait=True)
    agent = await existing_agent(tmp_path, monkeypatch, provider)
    engine = CandidateEngine(state_root=tmp_path / "state")
    with pytest.raises(asyncio.TimeoutError):
        await engine.generate_code(agent, root, ["example.py"], "Propose a fix.", timeout_seconds=1)
    assert not agent._active_harness_sessions
    assert (root / "example.py").read_text().endswith("return value\n")


@pytest.mark.asyncio
async def test_uninitialized_agent_and_sensitive_scope_fail_before_request(tmp_path, repo):
    root, _ = repo
    engine = CandidateEngine(state_root=tmp_path / "state")
    class Uninitialized:
        _is_initialized = False
    with pytest.raises(CandidateError, match="initialized"):
        await engine.generate_code(Uninitialized(), root, ["example.py"], "Propose a fix.")
    with pytest.raises(CandidateError, match="exact source scope"):
        await engine.generate_code(Uninitialized(), root, [".env"], "Propose a fix.")


@pytest.mark.asyncio
async def test_provider_model_change_cannot_stage_an_unpinned_patch(tmp_path, monkeypatch, repo):
    root, patch = repo
    provider = PatchProvider(patch, change_model=True)
    agent = await existing_agent(tmp_path, monkeypatch, provider)
    engine = CandidateEngine(state_root=tmp_path / "state")
    with pytest.raises(CandidateError, match="pinned provider/model changed"):
        await engine.generate_code(agent, root, ["example.py"], "Propose a fix.")
    assert (root / "example.py").read_text().endswith("return value\n")


@pytest.mark.asyncio
async def test_busy_primary_agent_is_never_reused_for_background_generation(tmp_path, repo):
    root, _ = repo
    class Busy:
        _is_initialized = True
        _active_harness_sessions = {"primary-session": "native"}
    with pytest.raises(CandidateError, match="busy"):
        await CandidateEngine(state_root=tmp_path / "state").generate_code(Busy(), root, ["example.py"], "Propose a fix.")
    assert not (tmp_path / "state").exists()
