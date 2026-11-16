"""Real service/SQLite/agent/Git coverage of the migrated desktop surfaces."""
from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest
import yaml

from firik_agent.application.service import FirikService
from firik_agent.core.types import StreamChunk, ToolCall, ToolCallFunction
from firik_agent.desktop_rpc.server import DesktopServer
from firik_agent.projects.models import Project
from firik_agent.providers.registry import ProviderRegistry


def eventually(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "Desktop workspace operation timed out"


def frames(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    state = {"requests": [], "delay": .01, "edit": False}

    class Provider:
        name = "firik"
        base_url = "https://example.test/v1"
        model_metadata = {}

        def set_model(self, model):
            self.model = model

        async def chat_stream(self, messages, **kwargs):
            state["requests"].append([m.model_dump() for m in messages])
            await asyncio.sleep(state["delay"])
            if state["edit"] and not any(m.name == "update_plan" for m in messages):
                yield StreamChunk(tool_calls=[ToolCall(id="plan", function=ToolCallFunction(name="update_plan",
                    arguments=json.dumps({"steps": [{"id": "s1", "title": "Değeri güncelle",
                        "acceptance_criteria": ["main.py yeni değeri içerir"], "verification": "read_file main.py"}]}))),
                    ToolCall(id="read-main", function=ToolCallFunction(name="read_file", arguments='{"path":"main.py"}'))])
            elif state["edit"] and not any(m.name == "edit_file" for m in messages):
                read = next(json.loads(m.content) for m in reversed(messages) if m.role == "tool" and m.name == "read_file")
                yield StreamChunk(tool_calls=[ToolCall(id="edit-main", function=ToolCallFunction(
                    name="edit_file", arguments=json.dumps({"path": "main.py", "old_text": "value = 1", "new_text": "value = 2", "expected_hash": read["hash"], "step_id": "s1"})))])
            else:
                yield StreamChunk(delta_content="Proje yanıtı tamamlandı.")

    def providers(_):
        registry = ProviderRegistry()
        registry.register("firik", Provider())
        registry.set_active("firik")
        return registry

    monkeypatch.setattr(ProviderRegistry, "from_config", providers)
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "providers": {"firik": {"base_url": "https://example.test/v1", "default_model": "fixture-model",
            "model_capabilities": {"fixture-model": {"source": "verified_config", "supports_tools": True}}}},
        "disabled_default_mcps": ["web", "computer-use"], "mcp_servers": {}, "browser": {"enabled": False},
        "skills": {"autoload_builtins": False, "active_skills": []}, "plugins": {"autoload_builtins": False},
    }))
    service = FirikService(config_path=config)
    monkeypatch.setattr(service, "get_key", lambda: "fixture-only-key")
    roots = {}
    for project_id in ("p1", "p2"):
        root = tmp_path / project_id
        root.mkdir()
        (root / "main.py").write_text("value = 1\n")
        git(root, "init", "-b", "main")
        git(root, "add", "main.py")
        git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "Initial")
        service.projects.db.create_project(Project(id=project_id, name=project_id, local_path=str(root)))
        roots[project_id] = root
    output = io.BytesIO()
    server = DesktopServer(service, output)
    async def host(method, params, **options):
        # This suite has no browser pages. Acknowledge the normal lifecycle
        # messages that Electron's BrowserHost handles in the UI smoke test.
        assert method in {"browser.configureExecutor", "browser.revoke"}
        return {"ok": True, "payload": {}}
    monkeypatch.setattr(server, "request_host", host)
    next_id = 0

    def call(method, params=None, *, wait=True):
        nonlocal next_id
        next_id += 1
        request_id = next_id
        server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        if not wait:
            return request_id
        eventually(lambda: any(row.get("id") == request_id for row in frames(output)))
        return next(row for row in frames(output) if row.get("id") == request_id)

    call("system.hello", {"protocol_version": 1})
    yield server, service, call, state, roots, output
    server.workspaces.close()
    server._closing = True
    service.shutdown()
    service.projects.scheduler.stop()


def coding_params(call, project="p1"):
    opened = call("coding.open", {"project_id": project})["result"]
    return {"project_id": project, "session_id": opened["session_id"]}


def test_coding_stream_uses_real_context_agent_and_durable_history(workspace):
    server, service, call, state, roots, output = workspace
    p = coding_params(call)
    result = call("coding.start", {**p, "prompt": "main.py dosyasını açıkla", "model": "fixture-model", "mode": "ask"})["result"]
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
    final = call("coding.get", p)["result"]
    assert not final["busy"] and not final["error"]
    assert any("Proje yanıtı" in row["content"] for row in final["messages"])
    record = service.coding_store.get_run(final["record_id"], project_id="p1")
    assert record and record.session_id == p["session_id"]
    assert state["requests"] and any("value = 1" in str(row) for row in state["requests"][0])
    assert (roots["p1"] / "main.py").read_text() == "value = 1\n"
    assert call("coding.history", {"project_id": "p1"})["result"]["records"][0]["record_id"] == record.id
    restored = call("coding.open", {"project_id": "p1", "record_id": record.id})["result"]
    assert restored["session_id"] == p["session_id"]
    assert call("coding.get", {**p, "project_id": "p2"})["error"]["code"] == -32004
    assert call("chat.get", {"conversation_id": p["session_id"]})["error"]["code"] == -32004
    assert call("coding.control", {**p, "run_id": result["run_id"], "action": "cancel"})["error"]["code"] == -32004
    snapshot = call("state.snapshot")["result"]
    assert all(row["conversation_id"] != p["session_id"] for row in snapshot["conversations"])
    assert any(row.get("params", {}).get("kind") == "coding" for row in frames(output))


def test_coding_edits_wait_for_scoped_approval(workspace):
    server, service, call, state, roots, _ = workspace
    state["edit"] = True
    p = coding_params(call)
    run = call("coding.start", {**p, "prompt": "main.py değerini değiştir", "model": "fixture-model"})["result"]
    eventually(lambda: bool(server.workspaces.coding_state(**p).approvals))
    pending = server.workspaces.coding_state(**p).approvals[0]
    assert (roots["p1"] / "main.py").read_text() == "value = 1\n"
    assert call("coding.control", {**p, "run_id": "stale-run", "action": "approve", "request_id": pending.request_id, "allowed": True})["error"]["code"] == -32004
    assert call("coding.control", {**p, "run_id": run["run_id"], "action": "approve", "request_id": pending.request_id, "allowed": True})["result"]["ok"]
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
    assert (roots["p1"] / "main.py").read_text() == "value = 2\n"
    assert not server.workspaces.coding_state(**p).approvals
    assert service.coding_store.latest_run("p1").file_journal


def test_coding_cancel_keeps_reader_and_other_projects_available(workspace):
    server, service, call, state, _, _ = workspace
    state["delay"] = 2
    p = coding_params(call)
    run = call("coding.start", {**p, "prompt": "Projeyi incele", "model": "fixture-model"})["result"]
    assert call("state.snapshot")["result"]["settings"]["has_api_key"]
    assert call("coding.start", {**p, "prompt": "İkinci istek", "model": "fixture-model"})["error"]["code"] == -32009
    assert call("settings.update", {"base_url": "https://example.test/v1", "default_model": "fixture-model", "timeout": 30})["error"]["code"] == -32009
    preview = call("git.preview", {"project_id": "p1", "tool_id": "stage", "arguments": {"paths": "main.py"}})["result"]
    assert call("git.execute", {"project_id": "p1", "token": preview["token"], "confirmed": True})["error"]["code"] == -32009
    call("coding.control", {**p, "run_id": run["run_id"], "action": "cancel"})
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
    assert server.workspaces.coding_state(**p).status == "cancelled"
    eventually(lambda: p["session_id"] not in service._streams)
    assert call("coding.open", {"project_id": "p2"})["result"]["project_id"] == "p2"


def test_coding_cancel_during_context_preparation_never_dispatches(workspace, monkeypatch):
    server, service, call, state, _, output = workspace
    p = coding_params(call)
    entered, release = threading.Event(), threading.Event()
    original = service.projects.coding.prepare_coding_prompt

    def prepare(*args, **kwargs):
        entered.set()
        release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(service.projects.coding, "prepare_coding_prompt", prepare)
    request_id = call("coding.start", {**p, "prompt": "Projeyi incele", "model": "fixture-model"}, wait=False)
    assert entered.wait(2)
    run_id = server.workspaces.coding_state(**p).run_id
    assert call("coding.control", {**p, "run_id": run_id, "action": "cancel"})["result"]["ok"]
    release.set()
    eventually(lambda: any(row.get("id") == request_id for row in frames(output)))
    assert not server.workspaces.coding_state(**p).busy
    assert not state["requests"]


def test_git_catalogue_preview_confirmation_scope_and_single_use(workspace):
    _, _, call, _, roots, _ = workspace
    overview = call("git.overview", {"project_id": "p1"})["result"]
    assert overview["branch"] == "main" and len(overview["tools"]) > 40
    (roots["p1"] / "main.py").write_text("value = 3\n")
    preview = call("git.preview", {"project_id": "p1", "tool_id": "stage", "arguments": {"paths": "main.py"}})["result"]
    assert preview["requires_confirmation"]
    assert call("git.execute", {"project_id": "p2", "token": preview["token"], "confirmed": True})["error"]["code"] == -32004
    assert call("git.execute", {"project_id": "p1", "token": preview["token"]})["error"]["code"] == -32009
    assert git(roots["p1"], "diff", "--cached") == ""
    assert call("git.execute", {"project_id": "p1", "token": preview["token"], "confirmed": True})["result"]["ok"]
    assert "+value = 3" in git(roots["p1"], "diff", "--cached")
    assert call("git.execute", {"project_id": "p1", "token": preview["token"], "confirmed": True})["error"]["code"] == -32004
    assert "error" in call("git.preview", {"project_id": "p1", "tool_id": "diff", "arguments": {"path": "../p2/main.py"}})


def test_saved_project_permissions_are_preserved(workspace):
    server, service, call, state, roots, _ = workspace
    prefs = service.projects.db.get_settings("p1")
    prefs.coding_preferences = {"permissions": {"allow_edits": False}, "policy": {"filesystem.write": "deny"}}
    service.projects.db.save_settings(prefs)
    state["edit"] = True
    p = coding_params(call)
    call("coding.start", {**p, "prompt": "main.py değerini değiştir", "model": "fixture-model"})
    eventually(lambda: not server.workspaces.coding_state(**p).busy)
    assert (roots["p1"] / "main.py").read_text() == "value = 1\n"
    record = service.coding_store.latest_run("p1")
    assert record.permissions["allow_edits"] is False
    assert record.permissions["filesystem.write"] == "deny"


def test_cancel_is_available_when_all_context_workers_are_busy(workspace, monkeypatch):
    server, service, call, state, roots, output = workspace
    for name in ("p3", "p4"):
        root = roots["p1"].parent / name
        root.mkdir()
        service.projects.db.create_project(Project(id=name, name=name, local_path=str(root)))
    sessions = [coding_params(call, name) for name in ("p1", "p2", "p3", "p4")]
    release = threading.Event()
    entered = []
    original = service.projects.coding.prepare_coding_prompt

    def prepare(*args, **kwargs):
        entered.append(args[0])
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(service.projects.coding, "prepare_coding_prompt", prepare)
    requests = [call("coding.start", {**p, "prompt": "Projeyi incele", "model": "fixture-model"}, wait=False) for p in sessions]
    try:
        eventually(lambda: len(entered) == 4, timeout=3)
        for p in sessions:
            run_id = server.workspaces.coding_state(**p).run_id
            assert call("coding.control", {**p, "run_id": run_id, "action": "cancel"})["result"]["ok"]
    finally:
        release.set()
    eventually(lambda: all(any(frame.get("id") == request for frame in frames(output)) for request in requests))
    assert not state["requests"]


def test_voice_capture_review_real_agent_and_scope_invalidation(workspace, monkeypatch):
    server, service, call, state, _, output = workspace
    transcriptions = []

    async def transcribe(audio, model, language):
        transcriptions.append((audio, model, language))
        return "Merhaba sesli asistan"

    monkeypatch.setattr(service, "_transcribe_microphone", transcribe)
    sid = call("chat.create")["result"]["conversation_id"]
    current = call("voice.get", {"conversation_id": sid})["result"]
    assert server.workspaces._voice._recorder is None
    preferences = current["settings"]
    preferences["enabled"] = True
    call("voice.settings", preferences)
    entered = threading.Event()

    class Recorder:
        def record(self, stop, cancel, max_seconds, device=None):
            entered.set()
            eventually(lambda: stop.is_set() or cancel.is_set())
            return b"" if cancel.is_set() else b"RIFF-private-audio-fixture"

    controller = server.workspaces._voice
    controller._recorder = Recorder()
    command = {"conversation_id": sid, "scope_id": current["scope_id"]}
    call("voice.command", {**command, "action": "record"})
    assert entered.wait(1)
    call("voice.command", {**command, "action": "stop_recording"})
    eventually(lambda: bool(server.workspaces._voice_review))
    reviewed = call("voice.get", {"conversation_id": sid})["result"]
    assert reviewed["transcript"] == "Merhaba sesli asistan" and not state["requests"]
    assert transcriptions[0][0] == b"RIFF-private-audio-fixture"
    submitted = call("voice.command", {**command, "action": "submit", "review_id": reviewed["review_id"], "text": "Merhaba sesli asistan"})
    assert "result" in submitted, submitted
    eventually(lambda: all(run.status != "running" for run in server._runs.values()))
    assert service.sessions[sid].messages[-2].execution["origin"] == "voice"
    assert any("Proje yanıtı" in row["content"] for row in call("chat.get", {"conversation_id": sid})["result"]["messages"])
    assert call("voice.command", {**command, "action": "submit", "review_id": reviewed["review_id"], "text": "Tekrar"})["error"]["code"] == -32004
    other_sid = call("chat.create")["result"]["conversation_id"]
    call("voice.get", {"conversation_id": other_sid})
    assert call("voice.command", {**command, "action": "record"})["error"]["code"] == -32004
    assert "RIFF-private-audio-fixture" not in output.getvalue().decode()


def test_unknown_voice_session_and_extra_authority_are_rejected(workspace):
    _, _, call, _, _, _ = workspace
    assert call("voice.get", {"conversation_id": "missing"})["error"]["code"] == -32004
    assert call("coding.start", {"project_id": "p1", "session_id": "missing", "prompt": "x", "model": "m", "permissions": {"allow_edits": True}})["error"]["code"] == -32602
    assert call("git.execute", {"project_id": "p1", "token": "missing", "command": "rm -rf"})["error"]["code"] == -32602
    assert call("coding.start", {"project_id": "p1", "session_id": "missing", "prompt": " ", "model": "m"})["error"]["code"] == -32602
