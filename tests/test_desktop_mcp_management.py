"""Real desktop RPC MCP configuration, process lifecycle and secret boundaries."""
import io
import json
from pathlib import Path
import sys
import time

import pytest
import yaml

from firik_agent.application.service import FirikService
from firik_agent.desktop_rpc.server import DesktopServer
from firik_agent.mcp.models import MCPTestResult


@pytest.fixture
def desktop(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"providers": {"firik": {"base_url": "https://example.test/v1",
        "default_model": "fixture-model"}}, "disabled_default_mcps": ["web", "computer-use"],
        "mcp_servers": {}, "browser": {"enabled": False}}))
    service = FirikService(config_path=config)
    output = io.BytesIO()
    server = DesktopServer(service, output)
    sequence = 0

    def request(method, params=None):
        nonlocal sequence
        sequence += 1
        request_id = sequence
        server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = [json.loads(line) for line in output.getvalue().splitlines()]
            response = next((row for row in rows if row.get("id") == request_id), None)
            if response:
                # Completion callbacks clear the operation gate before the next
                # request, just as they do before returning to Electron's reader.
                while server.mcp.busy and "error" not in response and method in {"mcp.save", "mcp.remove", "mcp.command"}:
                    if time.monotonic() >= deadline:
                        raise AssertionError("MCP operation did not complete")
                    time.sleep(.001)
                return response
            time.sleep(.01)
        raise AssertionError(f"No response to {method}")

    yield server, service, output, config, request
    server._closing = True
    service.shutdown()
    service.projects.scheduler.stop()


def local_config(**extra):
    return {"name": "fixture-mcp", "command": sys.executable,
        "args": [str(Path(__file__).parents[1] / "examples/mock_mcp_server.py")], **extra}


def hello(request):
    result = request("system.hello", {"protocol_version": 1})
    assert "mcp-management" in result["result"]["capabilities"]


def test_mcp_configuration_lifecycle_and_chat_cleanup(desktop):
    server, service, _, path, request = desktop
    hello(request)
    assert request("mcp.list")["result"]["servers"] == []
    config = local_config(default_for_chat=True)
    added = request("mcp.save", {"mode": "create", "config": config})
    assert added["result"]["servers"][0]["status"] == "configured"
    assert request("mcp.save", {"mode": "create", "config": config})["error"]["code"] == -32009
    assert request("state.snapshot")["result"]["mcps"][0]["name"] == config["name"]
    assert request("mcp.command", {"name": config["name"], "command": "connect"})["result"]["tool_count"] == 3
    tested = request("mcp.command", {"name": config["name"], "command": "test"})["result"]
    assert tested["success"] and "text_transformer" in tested["tool_names"]
    assert request("mcp.command", {"name": config["name"], "command": "restart"})["result"]["success"]
    chat = request("chat.create")["result"]
    assert config["name"] in chat["active_mcp_servers"]
    edited = request("mcp.save", {"mode": "update", "config": {**config, "env": {"MODE": "updated"}}})
    assert edited["result"]["servers"][0]["status"] == "connected"
    assert yaml.safe_load(path.read_text())["mcp_servers"][config["name"]]["env"] == {"MODE": "updated"}
    assert request("mcp.command", {"name": config["name"], "command": "disconnect"})["result"]["success"]
    assert request("mcp.remove", {"name": config["name"]})["result"]["servers"] == []
    assert config["name"] not in request("chat.get", {"conversation_id": chat["conversation_id"]})["result"]["active_mcp_servers"]
    assert yaml.safe_load(path.read_text())["mcp_servers"] == {}
    assert config["name"] not in service.mcp_manager.connections


def test_remote_credentials_are_hidden_and_preserved_on_edit(desktop, credential_store):
    _, service, output, path, request = desktop
    hello(request)
    config = {"name": "remote", "transport": "http", "url": "http://127.0.0.1:9876/mcp",
        "headers": {"Authorization": "Bearer fixture-header-secret"}, "secret_env": {"TOKEN": "fixture-env-secret"},
        "tool_policy": {"mode": "confirm", "confirm_tools": ["write"]}}
    response = request("mcp.save", {"mode": "create", "config": config})["result"]
    public = response["servers"][0]["config"]
    assert public["headers"] == {"Authorization": ""}
    assert public["secret_env"] == {"TOKEN": ""}
    saved = yaml.safe_load(path.read_text())["mcp_servers"]["remote"]
    assert saved["secret_env"]["TOKEN"] == "keyring://mcp/remote/TOKEN"
    assert "fixture-env-secret" not in path.read_text()
    public["default_for_chat"] = True
    assert "result" in request("mcp.save", {"mode": "update", "config": public})
    conn = service.mcp_manager.get_connection("remote")
    assert conn.config.headers["Authorization"] == config["headers"]["Authorization"]
    assert conn.config.secret_env["TOKEN"] == saved["secret_env"]["TOKEN"]
    assert conn.config.tool_policy.confirm_tools == ["write"]
    conn.last_error = "failed: Bearer fixture-header-secret; fixture-env-secret"
    request("mcp.list")
    assert b"fixture-header-secret" not in output.getvalue()
    assert b"fixture-env-secret" not in output.getvalue()
    assert b"keyring://" not in output.getvalue()
    request("mcp.remove", {"name": "remote"})
    assert ("firik:mcp:remote", "TOKEN") not in credential_store.values


@pytest.mark.parametrize("config", [local_config(command=" "), local_config(args=[1]),
    local_config(name="bad/name"), {"name": "remote", "transport": "http", "url": "file:///tmp/x"},
    {"name": "remote", "transport": "sse", "url": "https://user:password@example.test"},
    local_config(env={"KEY": 123}), local_config(extra_authority=True)])
def test_invalid_configuration_does_not_modify_disk(desktop, config):
    _, _, _, path, request = desktop
    hello(request)
    before = path.read_text()
    assert request("mcp.save", {"mode": "create", "config": config})["error"]["code"] == -32602
    assert path.read_text() == before


def test_slow_operations_keep_stdio_responsive_and_reject_concurrent_mutations(desktop, monkeypatch):
    server, service, output, _, request = desktop
    assert request("mcp.list")["error"]["code"] == -32000
    hello(request)
    request("mcp.save", {"mode": "create", "config": local_config()})
    callback = []
    monkeypatch.setattr(service, "test_mcp_async", lambda name, **kwargs: callback.append(kwargs["on_success"]))
    server.handle({"jsonrpc": "2.0", "id": "slow", "method": "mcp.command",
        "params": {"name": "fixture-mcp", "command": "test"}})
    assert server.mcp.busy
    assert "result" in request("state.snapshot")
    assert "result" in request("mcp.list")
    assert request("mcp.remove", {"name": "fixture-mcp"})["error"]["code"] == -32009
    callback[0](MCPTestResult(success=False, error="Connection refused"))
    assert not server.mcp.busy
    result = next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == "slow")
    assert not result["result"]["success"]
    assert "result" in request("mcp.remove", {"name": "fixture-mcp"})


def test_disabled_update_disconnects_and_does_not_autostart(desktop):
    _, _, _, _, request = desktop
    hello(request)
    request("mcp.save", {"mode": "create", "config": local_config()})
    request("mcp.command", {"name": "fixture-mcp", "command": "connect"})
    saved = request("mcp.save", {"mode": "update", "config": local_config(enabled=False, autostart=True)})
    assert saved["result"]["servers"][0]["status"] == "configured"
    assert request("mcp.command", {"name": "fixture-mcp", "command": "connect"})["error"]["code"] == -32009
    chat = request("chat.create")["result"]
    assert request("mcp.set", {"conversation_id": chat["conversation_id"], "server_name": "fixture-mcp", "enabled": True})["error"]["code"] == -32009
    assert request("mcp.remove", {"name": "missing"})["error"]["code"] == -32004
