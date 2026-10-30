from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Optional
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import ContentPart, ToolDefinition, ToolResult
from firik_agent.mcp.client import MCPClient


logger = logging.getLogger(__name__)


class MCPManager:
    """Manages active MCP servers and registers their tools into the agent's ToolRegistry."""

    def __init__(self, tool_registry: ToolRegistry):
        self.tool_registry = tool_registry
        self.clients: Dict[str, MCPClient] = {}
        self.server_configs: Dict[str, Dict[str, Any]] = {}

    async def add_server(
        self,
        name: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
    ) -> List[str]:
        """
        Add and connect an MCP server, automatically discovering and registering its tools.
        Returns the list of registered tool names.
        """
        if name in self.clients:
            await self.remove_server(name)

        client = MCPClient(
            name=name,
            command=command,
            args=args,
            env=env,
            url=url,
        )

        await client.connect()
        self.clients[name] = client
        self.server_configs[name] = {
            "command": command,
            "args": args or [],
            "env": env or {},
            "url": url,
        }

        # Discover tools
        mcp_tools = await client.list_tools()
        registered_tools: List[str] = []

        for t in mcp_tools:
            # We prefix tool name to avoid collisions across servers
            clean_name = f"mcp_{name}_{t.name}" if not t.name.startswith("mcp_") else t.name
            tool_def = ToolDefinition(
                name=clean_name,
                description=f"[{name} MCP tool] {t.description or t.name}",
                parameters=t.inputSchema,
                source=f"mcp:{name}",
                execution_boundary="remote_mcp",
            )

            # Create a closure capturing client and original tool name
            orig_tool_name = t.name

            async def _make_handler(cl: MCPClient, orig_name: str):
                async def handler(**kwargs):
                    res = await cl.call_tool(orig_name, kwargs)
                    if res.isError:
                        return ToolResult(tool_call_id="", name=orig_name, is_error=True,
                            content="MCP tool reported an error; inspect server state before repeating the operation.",
                            metadata={"failure_kind": "tool_outcome_unknown", "request_phase": "tool_dispatched",
                                      "requires_reconciliation": True, "retry_eligible": False})
                    parts: List[ContentPart] = []
                    texts: List[str] = []
                    for c in res.content:
                        if c.type == "text" and c.text:
                            texts.append(c.text)
                            parts.append(ContentPart(type="text", text=c.text))
                        elif c.type == "image" and c.data:
                            parts.append(ContentPart(
                                type="image",
                                data=c.data,
                                mime_type=c.mimeType or "image/png",
                            ))
                            texts.append(f"[MCP Image: {c.mimeType or 'image/png'}]")
                    content_str = "\n".join(texts) if texts else "Success (empty output)"
                    if any(p.type == "image" for p in parts):
                        return ToolResult(
                            tool_call_id="",
                            name=orig_name,
                            content=content_str,
                            parts=parts,
                            is_error=False,
                        )
                    return content_str

                return handler


            handler = await _make_handler(client, orig_tool_name)
            self.tool_registry.register(tool_def, handler)
            registered_tools.append(clean_name)
            logger.info("Registered MCP tool '%s' from server '%s'", clean_name, name)

        return registered_tools

    async def remove_server(self, name: str) -> bool:
        """Disconnect an MCP server and unregister its tools."""
        if name not in self.clients:
            return False

        client = self.clients.pop(name)
        self.server_configs.pop(name, None)
        await client.disconnect()

        # Unregister tools from this source
        removed = self.tool_registry.unregister_by_source(f"mcp:{name}")
        logger.info("Removed MCP server '%s' and unlinked %d tools: %s", name, len(removed), removed)
        return True

    def list_servers(self) -> List[Dict[str, Any]]:
        """Return information about configured and active MCP servers."""
        res = []
        for name, client in self.clients.items():
            conf = self.server_configs.get(name, {})
            tools = [
                t.name for t in self.tool_registry.list_tools()
                if t.source == f"mcp:{name}"
            ]
            res.append({
                "name": name,
                "status": "connected" if client.is_connected else "disconnected",
                "type": "stdio" if client.is_stdio else "http",
                "command": conf.get("command"),
                "args": conf.get("args"),
                "url": conf.get("url"),
                "tools": tools,
            })
        return res

    async def shutdown(self) -> None:
        """Gracefully disconnect all active MCP clients."""
        for name in list(self.clients.keys()):
            await self.remove_server(name)
