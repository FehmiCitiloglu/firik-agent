import asyncio

import pytest

from firik_agent.computer_use.mcp_server import ComputerUseMCPServer
from firik_agent.mcp.client import MCPClient
from firik_agent.mcp.defaults import add_desktop_defaults


def test_desktop_defaults_preserve_custom_settings_and_opt_out():
    config = {"mcp_servers": {"other": {"command": "custom"}}}
    add_desktop_defaults(config)
    assert config["mcp_servers"]["computer-use"]["default_for_chat"]
    assert config["mcp_servers"]["other"]["command"] == "custom"
    config["mcp_servers"]["computer-use"]["enabled"] = False
    add_desktop_defaults(config)
    assert not config["mcp_servers"]["computer-use"]["enabled"]
    removed = {"mcp_servers": {}, "disabled_default_mcps": ["computer-use"]}
    add_desktop_defaults(removed)
    assert not removed["mcp_servers"]


@pytest.mark.asyncio
async def test_builtin_server_handshake_tools_and_error_result_without_subprocess(monkeypatch):
    server = ComputerUseMCPServer(use_mock=True)
    monkeypatch.setattr("firik_agent.computer_use.mcp_server.ComputerUseMCPServer", lambda: server)
    client = MCPClient("computer-use", command="builtin:computer-use")
    try:
        await client.connect()
        assert client.is_connected
        assert client.pid is None
        assert "computer_screenshot" in {tool.name for tool in await client.list_tools()}
        result = await client.call_tool("computer_screenshot", {})
        assert not result.isError
        assert any(item.type == "image" for item in result.content)
        invalid = await client.call_tool("unknown_tool", {})
        assert invalid.isError
        client.stop_local_actions()
        stopped = await client.call_tool("computer_screenshot", {})
        assert stopped.isError
        assert server.service.security.is_stopped()
    finally:
        await client.disconnect()
    assert not client.is_connected


@pytest.mark.asyncio
async def test_builtin_calls_run_off_runtime_thread_and_serialize(monkeypatch):
    import threading
    import time
    server = ComputerUseMCPServer(use_mock=True)
    monkeypatch.setattr("firik_agent.computer_use.mcp_server.ComputerUseMCPServer", lambda: server)
    client = MCPClient("computer-use", command="builtin:computer-use")
    await client.connect()
    threads = []
    concurrent = 0
    peak = 0
    original = server.handle_request
    def handle(request):
        nonlocal concurrent, peak
        threads.append(threading.get_ident())
        concurrent += 1
        peak = max(peak, concurrent)
        time.sleep(0.02)
        try:
            return original(request)
        finally:
            concurrent -= 1
    monkeypatch.setattr(server, "handle_request", handle)
    try:
        await asyncio.gather(client.list_tools(), client.list_tools())
        assert peak == 1
        assert all(thread != threading.get_ident() for thread in threads)
    finally:
        await client.disconnect()
