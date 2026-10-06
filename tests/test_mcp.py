from __future__ import annotations
import sys
from pathlib import Path
import pytest
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import ToolCall, ToolCallFunction
from firik_agent.mcp.client import MCPClient
from firik_agent.mcp.manager import MCPManager


@pytest.mark.asyncio
async def test_mcp_client_direct():
    mock_server = str(Path(__file__).parent.parent / "examples" / "mock_mcp_server.py")
    client = MCPClient(
        name="test_mock",
        command=sys.executable,
        args=[mock_server],
    )

    await client.connect()
    assert client.is_connected is True

    # 1. Test tools/list
    tools = await client.list_tools()
    tool_names = [t.name for t in tools]
    assert "get_system_time" in tool_names
    assert "text_transformer" in tool_names
    assert "query_sensor" in tool_names

    # 2. Test tools/call
    res = await client.call_tool("text_transformer", {"text": "hello", "operation": "reverse"})
    assert res.isError is False
    assert len(res.content) == 1
    assert res.content[0].text == "olleh"

    await client.disconnect()


@pytest.mark.asyncio
async def test_mcp_manager_integration():
    mock_server = str(Path(__file__).parent.parent / "examples" / "mock_mcp_server.py")
    tools = ToolRegistry()
    manager = MCPManager(tools)

    # 1. Add server
    registered = await manager.add_server(
        name="local_mock",
        command=sys.executable,
        args=[mock_server],
    )
    assert len(registered) == 3
    assert "mcp_local_mock_text_transformer" in registered

    # 2. Execute tool through ToolRegistry
    tc = ToolCall(
        id="call_1",
        function=ToolCallFunction(
            name="mcp_local_mock_text_transformer",
            arguments='{"text": "firik", "operation": "uppercase"}',
        ),
    )
    result = await tools.execute(tc)
    assert result.is_error is False
    assert result.content == "FIRIK"

    # 3. List servers
    servers = manager.list_servers()
    assert len(servers) == 1
    assert servers[0]["name"] == "local_mock"
    assert len(servers[0]["tools"]) == 3

    # 4. Remove server
    ok = await manager.remove_server("local_mock")
    assert ok is True
    assert tools.get_tool("mcp_local_mock_text_transformer") is None
