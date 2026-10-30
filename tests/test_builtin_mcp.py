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
    removed = {"mcp_servers": {}, "disabled_default_mcps": ["computer-use", "web"]}
    add_desktop_defaults(removed)
    assert not removed["mcp_servers"]


@pytest.mark.asyncio
async def test_web_builtin_discovery_search_and_open(monkeypatch):
    import json
    from firik_agent.mcp.web_server import WebMCPServer

    def fake_get(self, url, params=None):
        if params:
            assert params == {"q": "Firik"}
            return url, '<a class="result__a" href="https://example.com">Example</a>', "text/html"
        return "https://example.com/final", '<h1>Title</h1><script>ignored</script><p>Hello world</p>', "text/html"

    monkeypatch.setattr(WebMCPServer, "_get", fake_get)
    client = MCPClient("web", command="builtin:web")
    await client.connect()
    try:
        assert {tool.name for tool in await client.list_tools()} == {"web_search", "web_open"}
        found = await client.call_tool("web_search", {"query": "Firik"})
        assert json.loads(found.content[0].text)["results"][0]["url"] == "https://example.com"
        opened = await client.call_tool("web_open", {"url": "https://example.com"})
        page = json.loads(opened.content[0].text)
        assert page["url"] == "https://example.com/final"
        assert page["text"] == "Title\nHello world"
        assert (await client.call_tool("web_search", {"query": ""})).isError
    finally:
        await client.disconnect()


def test_web_http_limits_and_search_failure(monkeypatch):
    import httpx
    from firik_agent.mcp.web_server import WebMCPServer

    server = WebMCPServer()
    with pytest.raises(ValueError):
        server._get("file:///etc/passwd")
    real_client = httpx.Client
    def client(**kwargs):
        return real_client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"pdf")), **kwargs)
    monkeypatch.setattr(httpx, "Client", client)
    with pytest.raises(ValueError, match="Unsupported content type"):
        server._get("https://example.com")


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
