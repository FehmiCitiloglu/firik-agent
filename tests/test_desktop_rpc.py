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
    assert "browser-typescript" in responses[0]["result"]["capabilities"]
    assert responses[1]["result"] == {"ok": True}


def test_browser_configure_and_command_rpc(desktop):
    server, service, output, _ = desktop
    sid = create(server, output)
    chat = request(server, output, "chat.get", {"conversation_id": sid}, request_id=10)
    assert chat["result"]["browser"]["enabled"] is False

    # Mock host handler responses when server sends reverse RPC to host
    host_requests = []
    original_send = server.send

    def intercept_send(msg):
        original_send(msg)
        if isinstance(msg, dict) and msg.get("method") == "browser.configureExecutor":
            host_requests.append(msg)
            server.handle({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}})
        if isinstance(msg, dict) and msg.get("method") == "browser.revoke":
            host_requests.append(msg)
            # Reply to reverse RPC
            server.handle({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True, "payload": {}}})

    server.send = intercept_send

    # Configure browser
    server.handle({"jsonrpc": "2.0", "id": 11, "method": "browser.configure",
                   "params": {"conversation_id": sid, "enabled": True, "origins": ["https://example.com"],
                              "private_origins": [], "screenshots": True}})

    wait_until(lambda: any(row.get("id") == 11 for row in messages(output)))
    conf_res = next(row for row in reversed(messages(output)) if row.get("id") == 11)
    assert conf_res["result"]["grant"]["enabled"] is True
    assert conf_res["result"]["grant"]["screenshots"] is True
    assert conf_res["result"]["grant"]["origins"] == ["https://example.com"]


def test_reverse_rpc_invalid_result_finishes_future_instead_of_timing_out(desktop):
    from pydantic import ValidationError
    server, _, _, _ = desktop
    def respond(message):
        server.handle({"jsonrpc": "2.0", "id": message["id"], "result": {"ok": "invalid"}})
    server.send = respond
    with pytest.raises(ValidationError):
        asyncio.run(server.request_host("browser.revoke", {"close_all": True}, timeout=.5))
    assert server._host_pending == {}


def test_remote_browser_forwards_source_limits_and_classifies_lost_action_ack():
    from types import SimpleNamespace
    from firik_agent.browser.config import BrowserConfig
    from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity
    from firik_agent.desktop_rpc.browser_client import RemoteBrowserManager
    calls = []
    async def host(method, params, **kwargs):
        calls.append((method, params))
        if method == "browser.configureExecutor":
            return {"ok": True}
        raise TimeoutError("lost acknowledgement")
    config = BrowserConfig(enabled=True, max_tabs_per_session=1, navigation_timeout_ms=4000)
    manager = RemoteBrowserManager(SimpleNamespace(request_host=host), config)
    identity, grant = BrowserIdentity("remote"), BrowserGrant(enabled=True)
    with pytest.raises(BrowserError) as error:
        asyncio.run(manager.dispatch(identity, grant, "navigate", {"url": "https://example.com"}))
    assert error.value.code == "tool_outcome_unknown"
    assert error.value.dispatched and error.value.requires_reconciliation
    assert calls[0] == ("browser.configureExecutor", {"config": config.model_dump()})
    assert len(calls) == 2
    with pytest.raises(BrowserError) as error:
        asyncio.run(manager.dispatch(identity, grant, "snapshot", {}))
    assert not error.value.dispatched and not error.value.requires_reconciliation
    assert len([c for c in calls if c[0] == "browser.configureExecutor"]) == 1
    config.strict_network_isolation = True
    with pytest.raises(BrowserError, match="Strict network isolation"):
        asyncio.run(manager.dispatch(identity, grant, "start", {}))
    assert len(calls) == 3


def test_config_failure_does_not_claim_an_action_was_dispatched():
    from types import SimpleNamespace
    from firik_agent.browser.config import BrowserConfig
    from firik_agent.browser.models import BrowserGrant, BrowserIdentity
    from firik_agent.desktop_rpc.browser_client import RemoteBrowserManager
    calls = []
    async def host(method, params, **kwargs):
        calls.append(method)
        raise TimeoutError("configuration not acknowledged")
    manager = RemoteBrowserManager(SimpleNamespace(request_host=host), BrowserConfig(enabled=True))
    with pytest.raises(TimeoutError):
        asyncio.run(manager.dispatch(BrowserIdentity("remote"), BrowserGrant(enabled=True), "navigate", {}))
    assert calls == ["browser.configureExecutor"]


def test_remote_screenshot_keeps_existing_artifact_ownership_and_hides_path(tmp_path):
    from types import SimpleNamespace
    from firik_agent.browser.config import BrowserConfig
    from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity
    from firik_agent.desktop_rpc.browser_client import RemoteBrowserManager
    screenshot = tmp_path / "worker-shot.png"
    screenshot.write_bytes(b"\x89PNG-private-fixture")
    async def host(method, params, **kwargs):
        if method == "browser.configureExecutor":
            return {"ok": True}
        return {"ok": True, "payload": {"artifact_id": "worker-id", "artifact_path": str(screenshot), "model_image": False}}
    manager = RemoteBrowserManager(SimpleNamespace(request_host=host), BrowserConfig(enabled=True))
    identity = BrowserIdentity("remote")
    try:
        payload = asyncio.run(manager.dispatch(identity, BrowserGrant(enabled=True, allow_screenshots=True), "screenshot", {}))
        assert "artifact_path" not in payload
        assert payload["artifact_id"] != "worker-id"
        assert manager.artifact_path(identity, payload["artifact_id"]).read_bytes() == b"\x89PNG-private-fixture"
        assert not screenshot.exists()
        with pytest.raises(BrowserError):
            manager.artifact_path(BrowserIdentity("other"), payload["artifact_id"])
        manager.artifacts.remove_owner(identity)
        with pytest.raises(BrowserError):
            manager.artifact_path(identity, payload["artifact_id"])
    finally:
        manager.artifacts.close()


def test_shutdown_keeps_reverse_rpc_reader_alive_until_browser_cleanup_ack():
    import queue
    import threading
    from types import SimpleNamespace
    frames = queue.Queue()
    output = io.BytesIO()
    cleanup = []
    def stop():
        result = asyncio.run(server.request_host("browser.revoke", {"close_all": True}, timeout=1))
        cleanup.append(result["ok"])
    service = SimpleNamespace(get_key=lambda: "", shutdown=stop,
                              projects=SimpleNamespace(scheduler=SimpleNamespace(stop=lambda: None)))
    server = DesktopServer(service, output)
    class Input:
        def readline(self, _limit):
            return frames.get(timeout=3)
    def send_frame(message):
        frames.put((json.dumps(message) + "\n").encode())
    thread = threading.Thread(target=server.serve, args=(Input(),), daemon=True)
    thread.start()
    try:
        send_frame({"jsonrpc": "2.0", "id": 1, "method": "system.shutdown", "params": {}})
        wait_until(lambda: any(row.get("method") == "browser.revoke" for row in messages(output)))
        reverse = next(row for row in messages(output) if row.get("method") == "browser.revoke")
        assert not any(row.get("id") == 1 for row in messages(output))
        send_frame({"jsonrpc": "2.0", "id": reverse["id"], "result": {"ok": True}})
        wait_until(lambda: any(row.get("id") == 1 for row in messages(output)))
        assert cleanup == [True]
    finally:
        frames.put(b"")
        thread.join(timeout=3)
    assert not thread.is_alive()


def test_remote_browser_retains_owner_when_close_ack_is_lost():
    from types import SimpleNamespace
    from firik_agent.browser.config import BrowserConfig
    from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity
    from firik_agent.desktop_rpc.browser_client import RemoteBrowserManager
    fail_close = True
    async def host(method, params, **kwargs):
        if method == "browser.revoke" and fail_close:
            raise TimeoutError("close acknowledgement lost")
        return {"ok": True}
    manager = RemoteBrowserManager(SimpleNamespace(request_host=host, _closing=False), BrowserConfig(enabled=True))
    identity = BrowserIdentity("retained")
    manager.bind_grant_provider(identity, lambda: BrowserGrant(enabled=True))
    manager._update_cache(identity, "start", {"state": "ready"})
    with pytest.raises(BrowserError) as error:
        asyncio.run(manager.close_owner(identity))
    assert error.value.code == "cleanup_unresolved" and error.value.requires_reconciliation
    assert manager.sessions(identity)[0]["state"] == "failed"
    fail_close = False
    asyncio.run(manager.close_owner(identity))
    assert manager.sessions(identity) == []


def test_browser_resize_command_contract_and_uninitialized_handling(desktop):
    server, _, output, _ = desktop
    sid = create(server, output)
    original_send = server.send
    executed = []

    def intercept_send(msg):
        original_send(msg)
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        if method == "browser.configureExecutor":
            server.handle({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}})
        elif method == "browser.revoke":
            server.handle({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True, "payload": {}}})
        elif method == "browser.execute":
            params = msg.get("params", {})
            op = params.get("operation")
            executed.append((op, params.get("arguments", {})))
            if op == "status" and len([x for x in executed if x[0] == "start"]) == 0:
                payload = {"state": "uninitialized", "tab_count": 0}
            else:
                payload = {
                    "state": "ready",
                    "session_id": "s1",
                    "context_id": "c1",
                    "active_tab_id": "t1",
                    "tab_count": 1,
                }
            server.handle({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True, "payload": payload}})

    server.send = intercept_send
    server.handle({
        "jsonrpc": "2.0",
        "id": 10,
        "method": "browser.configure",
        "params": {"conversation_id": sid, "enabled": True, "origins": ["https://example.com"], "private_origins": [], "screenshots": True},
    })
    wait_until(lambda: any(row.get("id") == 10 for row in messages(output)))

    server.handle({
        "jsonrpc": "2.0",
        "id": 11,
        "method": "browser.userCommand",
        "params": {"conversation_id": sid, "command": "resize", "width": 640, "height": 480},
    })
    wait_until(lambda: any(row.get("id") == 11 for row in messages(output)))
    uninit = next(row for row in reversed(messages(output)) if row.get("id") == 11)
    assert uninit["result"]["state"] == "uninitialized"
    assert executed[-1][0] == "status"

    server.handle({
        "jsonrpc": "2.0",
        "id": 12,
        "method": "browser.userCommand",
        "params": {"conversation_id": sid, "command": "start"},
    })
    wait_until(lambda: any(row.get("id") == 12 for row in messages(output)))

    server.handle({
        "jsonrpc": "2.0",
        "id": 13,
        "method": "browser.userCommand",
        "params": {"conversation_id": sid, "command": "resize", "width": 700, "height": 500},
    })
    wait_until(lambda: any(row.get("id") == 13 for row in messages(output)))
    resized = next(row for row in reversed(messages(output)) if row.get("id") == 13)
    assert resized["result"]["state"] == "ready"
    assert ("resize", {"width": 700, "height": 500}) in executed

    server.handle({
        "jsonrpc": "2.0",
        "id": 14,
        "method": "browser.userCommand",
        "params": {"conversation_id": sid, "command": "resize", "width": 99999, "height": 500},
    })
    wait_until(lambda: any(row.get("id") == 14 for row in messages(output)))
    invalid = next(row for row in reversed(messages(output)) if row.get("id") == 14)
    assert "error" in invalid
