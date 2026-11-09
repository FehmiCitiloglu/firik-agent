"""Behavioral coverage of the shared service and the private desktop boundary."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

from firik_agent.application.service import FirikService
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.types import Message
from firik_agent.desktop_rpc.models import ChatStartParams
from firik_agent.desktop_rpc.server import DesktopServer


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({
        "providers": {"firik": {"base_url": "https://example.test/v1", "default_model": "fixture-model"}},
        "disabled_default_mcps": ["web", "computer-use"], "mcp_servers": {}, "browser": {"enabled": False},
    }))
    service = FirikService(config_path=config)
    monkeypatch.setattr(service, "get_key", lambda: "fixture-only-key")
    output = io.BytesIO()
    server = DesktopServer(service, output)
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "system.hello", "params": {"protocol_version": 1}})
    yield server, service, output, config
    server._closing = True
    for run_id in list(server._runs):
        server._close_approvals(run_id)
    service.shutdown()
    service.projects.scheduler.stop()


def request(server, output, method, params=None, request_id=2):
    server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
    return next(row for row in reversed(messages(output)) if row.get("id") == request_id)


def messages(output):
    return [json.loads(line) for line in output.getvalue().splitlines()]


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for desktop behavior")


class Provider:
    name = "firik"
    base_url = "https://example.test/v1"
    model_metadata = {}

    def set_model(self, model):
        self.model = model


class FixtureAgent:
    def __init__(self, delay=.01):
        self.delay = delay
        self.provider = Provider()
        self.providers = type("Providers", (), {"get_active": lambda _: self.provider})()
        from firik_agent.core.tools import ToolRegistry
        self.tools = ToolRegistry()

    async def initialize(self):
        pass

    async def run_stream(self, prompt, session, session_tool_filter, cancel_event):
        assert session_tool_filter is not None
        session.messages.append(Message(role="user", content=prompt))
        yield AgentEvent(type=AgentEventType.MODEL_REQUEST_STARTED, session_id=session.session_id)
        text = ""
        for part in ["Merhaba ", prompt, "!"]:
            await asyncio.sleep(self.delay)
            if cancel_event.is_set():
                return
            text += part
            yield AgentEvent(type=AgentEventType.TEXT_DELTA, session_id=session.session_id, content=part)
        session.messages.append(Message(role="assistant", content=text))
        yield AgentEvent(type=AgentEventType.DONE, session_id=session.session_id, content=text)


def create(server, output):
    return request(server, output, "chat.create")["result"]["conversation_id"]


def test_no_gui_import_and_compatibility_module_identity():
    result = subprocess.run([sys.executable, "-c", """
import sys
import firik_agent.application.service as application
import firik_agent.ui.service as legacy
assert application is legacy
assert not any(name.split('.')[0] in {'tkinter', 'customtkinter', 'tkinterdnd2'} for name in sys.modules)
print('headless')
"""], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "headless"


def test_checked_in_contracts_match_python_source():
    from scripts.export_desktop_contracts import export
    path = Path(__file__).parents[1] / "packages/contracts/wire.schema.json"
    assert json.loads(path.read_text()) == export()


def test_protocol_rejects_versions_extra_fields_and_untrusted_method(desktop):
    server, _, output, _ = desktop
    assert request(server, output, "system.hello", {"protocol_version": 2})["error"]["code"] == -32602
    assert request(server, output, "system.hello", {"protocol_version": True})["error"]["code"] == -32602
    invalid = request(server, output, "credentials.set", {"api_key": "fixture-secret", "unexpected": "fixture-secret"})
    assert invalid["error"]["code"] == -32602
    assert "fixture-secret" not in json.dumps(invalid)
    assert request(server, output, "shell.execute", {"command": "whoami"})["error"]["code"] == -32601
    assert request(server, output, "chat.start", {"conversation_id": "unknown", "prompt": "hello",
                                                "model": "fixture-model"})["error"]["code"] == -32004
    assert not server._owned_chats


def test_snapshot_exposes_key_presence_never_key_or_raw_config(desktop):
    server, _, output, _ = desktop
    result = request(server, output, "state.snapshot")["result"]
    assert result["settings"]["has_api_key"] is True
    assert "fixture-only-key" not in json.dumps(result)
    assert "config" not in result
    assert result["models"]["models"][0] == "fixture-model"


def test_stream_uses_existing_agent_service_and_persists_context(desktop, monkeypatch):
    server, service, output, _ = desktop
    monkeypatch.setattr(service, "get_agent", lambda sid: FixtureAgent())
    sid = create(server, output)
    result = request(server, output, "chat.start", {"conversation_id": sid, "prompt": "dünya", "model": "fixture-model"})["result"]
    wait_until(lambda: server._runs[result["run_id"]].status == "completed")
    state = request(server, output, "chat.get", {"conversation_id": sid})["result"]
    assert [row["content"] for row in state["messages"]] == ["dünya", "Merhaba dünya!"]
    assert all(not row["pending"] for row in state["messages"])
    stored = service.chat_history.load(sid)
    assert stored["context"]["messages"][-1]["content"] == "Merhaba dünya!"
    assert stored["transcript"][-1]["pending"] is False
    events = [row["params"] for row in messages(output) if row.get("method") == "desktop.event"]
    assert events and [row["sequence"] for row in events] == sorted({row["sequence"] for row in events})
    assert all(row["backend_instance_id"] == server.instance_id for row in events)


def test_cancel_and_approvals_are_bound_to_run_without_affecting_other_chat(desktop, monkeypatch):
    server, service, output, _ = desktop
    monkeypatch.setattr(service, "get_agent", lambda sid: FixtureAgent(.15))
    first, second = create(server, output), create(server, output)
    one = request(server, output, "chat.start", {"conversation_id": first, "prompt": "bir", "model": "fixture-model"})["result"]
    two = request(server, output, "chat.start", {"conversation_id": second, "prompt": "iki", "model": "fixture-model"})["result"]
    decisions = []
    server._approval(one["run_id"], {"tool_name": "fixture", "arguments": {"api_key": "fixture-only-key"}}, decisions.append)
    approval = next(iter(server._approvals.values()))[0]
    assert "fixture-only-key" not in approval.preview
    rejected = request(server, output, "approval.resolve", {"approval_id": approval.approval_id,
                                                           "run_id": two["run_id"], "allowed": True})
    assert rejected["error"]["code"] == -32004
    assert decisions == []
    assert request(server, output, "settings.update", {"base_url": "https://other.test/v1", "default_model": "other", "timeout": 30})["error"]["code"] == -32009
    request(server, output, "run.cancel", {"run_id": one["run_id"]})
    wait_until(lambda: server._runs[one["run_id"]].status == "cancelled")
    wait_until(lambda: server._runs[two["run_id"]].status == "completed")
    assert decisions == [False]
    assert not server._approvals
    assert service.chat_history.load(second)["transcript"][-1]["content"] == "Merhaba iki!"


def test_same_chat_cannot_start_twice_and_blank_prompt_is_rejected(desktop, monkeypatch):
    server, service, output, _ = desktop
    monkeypatch.setattr(service, "get_agent", lambda sid: FixtureAgent(.2))
    sid = create(server, output)
    params = {"conversation_id": sid, "prompt": "bir", "model": "fixture-model"}
    request(server, output, "chat.start", params)
    assert request(server, output, "chat.start", params)["error"]["code"] == -32009
    assert request(server, output, "chat.start", {**params, "prompt": " "})["error"]["code"] == -32602


def test_endpoint_changes_clear_cached_provider_instances(desktop):
    server, service, output, _ = desktop
    service._session_agents["previous"] = object()
    service.agent = object()
    result = request(server, output, "settings.update", {"base_url": "https://new.test/v1/", "default_model": "next", "timeout": 30})
    assert result["result"]["base_url"] == "https://new.test/v1"
    assert not service._session_agents and service.agent is None
    assert service._provider_cfg["base_url"] == "https://new.test/v1"


def test_independent_backend_cannot_open_same_data_directory(desktop):
    _, _, _, config = desktop
    result = subprocess.run([sys.executable, "-m", "firik_agent.desktop_rpc", "--config", str(config)],
                            input=b"", capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b"FIRIK_BACKEND_DATA_IN_USE" in result.stderr
    assert result.stdout == b""


def test_stdio_process_handshake_shutdown_and_protocol_stdout(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"disabled_default_mcps": ["web", "computer-use"],
                                     "providers": {"firik": {"base_url": "https://fixture.invalid/v1",
                                                              "api_key_env": "FIRIK_DESKTOP_TEST_KEY"}}}))
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "system.hello", "params": {"protocol_version": 1}},
             {"jsonrpc": "2.0", "id": 2, "method": "system.shutdown", "params": {}}]
    result = subprocess.run([sys.executable, "-m", "firik_agent.desktop_rpc", "--config", str(config)],
                            input="\n".join(json.dumps(row) for row in lines) + "\n",
                            capture_output=True, text=True, timeout=15,
                            env={**os.environ, "FIRIK_DESKTOP_TEST_KEY": "firik_llm_local_desktop_fixture"})
    assert result.returncode == 0, result.stderr
    responses = [json.loads(row) for row in result.stdout.splitlines()]
    assert responses[0]["result"]["protocol_version"] == 1
    assert "browser-typescript" not in responses[0]["result"]["capabilities"]
    assert responses[1]["result"] == {"ok": True}
