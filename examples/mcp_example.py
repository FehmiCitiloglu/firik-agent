#!/usr/bin/env python3
"""
MCP Integration Example.
Connects the standalone Mock MCP server via stdio and queries tools.
"""
import asyncio
from pathlib import Path
import sys
from firik_agent import Agent

async def main():
    agent = Agent()
    await agent.initialize()

    mock_server_path = str(Path(__file__).parent / "mock_mcp_server.py")

    print("[*] Connecting Mock MCP server over stdio...")
    tools = await agent.mcp.add_server(
        name="telemetry",
        command=sys.executable,
        args=[mock_server_path],
    )
    print(f"[*] MCP Server connected! Registered {len(tools)} tools:")
    for t in tools:
        print(f"    - {t}")

    # Inspect the tools directly
    print("\n[*] Invoking MCP tool directly through agent registry...")
    from firik_agent.core.types import ToolCall, ToolCallFunction
    tc = ToolCall(
        id="call_test_1",
        function=ToolCallFunction(
            name="mcp_telemetry_get_system_time",
            arguments='{"format": "%A, %d %B %Y %H:%M:%S UTC"}',
        ),
    )
    result = await agent.tools.execute(tc)
    print(f"[*] Tool Output: {result.content}")

    # List active servers
    servers = agent.mcp.list_servers()
    print("\n[*] Active MCP Servers:", servers)

    await agent.close()
    print("[*] Agent closed successfully.")

if __name__ == "__main__":
    asyncio.run(main())
