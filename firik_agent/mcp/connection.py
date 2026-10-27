from __future__ import annotations
import asyncio
from collections import deque
import datetime
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import ContentPart, ToolCall, ToolDefinition, ToolResult
from firik_agent.mcp.client import MCPClient
from firik_agent.mcp.models import (
    MCPLogEntry,
    MCPServerConfig,
    MCPStatus,
    MCPTestResult,
    MCPTransport,
    delete_all_mcp_secrets,
    redact_secrets,
    resolve_server_env,
)
from firik_agent.mcp.protocol import MCPCallToolResult, MCPToolSchema

logger = logging.getLogger(__name__)


class MCPConnection:
    """Manages the runtime state and process lifecycle of a single MCP server."""

    def __init__(self, config: MCPServerConfig, on_status_change: Optional[Callable[[str, MCPStatus], None]] = None):
        self.config = config
        self.on_status_change = on_status_change
        self.status: MCPStatus = MCPStatus.CONFIGURED
        self.client: Optional[MCPClient] = None
        self.last_error: Optional[str] = None
        self.last_connected_at: Optional[float] = None
        self.restart_count: int = 0
        self.cached_tools: List[MCPToolSchema] = []
        self.logs: deque[MCPLogEntry] = deque(maxlen=500)
        self.used_by_sessions: Set[str] = set()
        self._lock = asyncio.Lock()

    def add_log(self, level: str, message: str) -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        clean_msg = redact_secrets(message)
        self.logs.append(MCPLogEntry(timestamp=timestamp, level=level, message=clean_msg))

    def _set_status(self, new_status: MCPStatus, error: Optional[str] = None) -> None:
        self.status = new_status
        if error is not None:
            self.last_error = error
        if new_status == MCPStatus.CONNECTED:
            self.last_connected_at = time.time()
            self.last_error = None
        if self.on_status_change:
            try:
                self.on_status_change(self.config.name, new_status)
            except Exception:
                pass

    async def connect(self) -> None:
        """Start the MCP client and perform initialization."""
        async with self._lock:
            if self.status == MCPStatus.CONNECTED and self.client and self.client.is_connected:
                return

            self._set_status(MCPStatus.STARTING)
            self.add_log("INFO", f"Connecting to MCP server '{self.config.name}'...")

            try:
                resolved_env = resolve_server_env(self.config)

                client = MCPClient(
                    name=self.config.name,
                    command=self.config.command,
                    args=self.config.args,
                    env=resolved_env,
                    url=self.config.url,
                    cwd=self.config.cwd,
                    headers=self.config.headers,
                    on_log=self.add_log,
                )

                await client.connect()
                tools = await client.list_tools()

                self.client = client
                self.cached_tools = tools
                self._set_status(MCPStatus.CONNECTED)
                self.add_log("INFO", f"Successfully connected. Registered {len(tools)} tools.")

            except Exception as e:
                err_msg = str(e)
                self._set_status(MCPStatus.ERROR, error=err_msg)
                self.add_log("ERROR", f"Connection failed: {err_msg}")
                if self.client:
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    self.client = None
                raise

    async def disconnect(self, force: bool = False, grace_period: float = 3.0) -> None:
        """Disconnect the server and stop subprocess."""
        async with self._lock:
            if not force and self.used_by_sessions and self.config.autostart:
                self.add_log("INFO", f"Server still in use by {len(self.used_by_sessions)} session(s); keeping connected.")
                return

            self._set_status(MCPStatus.DISCONNECTING)
            self.add_log("INFO", f"Disconnecting server '{self.config.name}'...")

            if self.client:
                try:
                    await self.client.disconnect(grace_period=grace_period)
                except Exception as e:
                    self.add_log("ERROR", f"Error during disconnect: {e}")
                finally:
                    self.client = None

            self._set_status(MCPStatus.DISCONNECTED)
            self.add_log("INFO", "Server disconnected.")

    async def restart(self) -> None:
        """Restart connection with fresh process."""
        self.restart_count += 1
        self.add_log("INFO", f"Restarting server '{self.config.name}' (attempt #{self.restart_count})...")
        await self.disconnect(force=True)
        await self.connect()

    async def test(self) -> MCPTestResult:
        """Test connection independently and return detailed diagnostic result without mutating current connection."""
        t0 = time.time()
        resolved_env = resolve_server_env(self.config)
        test_client = MCPClient(
            name=f"test_{self.config.name}",
            command=self.config.command,
            args=self.config.args,
            env=resolved_env,
            url=self.config.url,
            cwd=self.config.cwd,
            headers=self.config.headers,
        )

        try:
            await asyncio.wait_for(test_client.connect(), timeout=15.0)
            server_info = test_client.server_info
            capabilities = server_info.get("capabilities", {})
            s_info = server_info.get("serverInfo", {})
            tools = await asyncio.wait_for(test_client.list_tools(), timeout=10.0)
            duration_ms = (time.time() - t0) * 1000

            return MCPTestResult(
                success=True,
                process_started=True,
                initialize_succeeded=True,
                protocol_version=server_info.get("protocolVersion", "2024-11-05"),
                server_name=s_info.get("name"),
                server_version=s_info.get("version"),
                capabilities=capabilities,
                tool_count=len(tools),
                tool_names=[t.name for t in tools],
                tools=tools,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.time() - t0) * 1000
            return MCPTestResult(
                success=False,
                process_started=bool(test_client.pid or test_client.is_connected),
                initialize_succeeded=False,
                duration_ms=duration_ms,
                error=str(e),
            )
        finally:
            try:
                await test_client.disconnect()
            except Exception:
                pass

    async def refresh_tools(self) -> List[MCPToolSchema]:
        """Fetch updated tool schemas from running server."""
        if not self.client or not self.client.is_connected:
            return self.cached_tools

        try:
            tools = await self.client.list_tools()
            self.cached_tools = tools
            self.add_log("INFO", f"Tools refreshed: {len(tools)} available.")
            return tools
        except Exception as e:
            self.add_log("ERROR", f"Failed refreshing tools: {e}")
            raise

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> MCPCallToolResult:
        """Forward a tool call to the running client."""
        if not self.client or not self.client.is_connected:
            # Try auto-reconnect
            self.add_log("INFO", f"Server not connected; attempting reconnect for tool '{name}'")
            await self.connect()

        assert self.client
        return await self.client.call_tool(name, arguments)

    def to_status_dict(self) -> Dict[str, Any]:
        """Summary dictionary for GUI and status reporting."""
        cmd_or_url = self.config.command or self.config.url or "-"
        if self.config.args:
            cmd_or_url += " " + " ".join(self.config.args)

        conn_time_str = "-"
        if self.last_connected_at:
            conn_time_str = datetime.datetime.fromtimestamp(self.last_connected_at).strftime("%H:%M:%S")

        return {
            "name": self.config.name,
            "status": self.status.value,
            "transport": self.config.transport.value,
            "command_or_url": cmd_or_url,
            "command": self.config.command,
            "args": self.config.args,
            "url": self.config.url,
            "cwd": self.config.cwd,
            "tool_count": len(self.cached_tools),
            "tool_names": [t.name for t in self.cached_tools],
            "autostart": self.config.autostart,
            "default_for_chat": self.config.default_for_chat,
            "last_error": self.last_error,
            "last_connected_time": conn_time_str,
            "pid": self.client.pid if self.client else None,
            "used_by_sessions": list(self.used_by_sessions),
        }


class MCPConnectionManager:
    """Central pool and lifecycle manager for all configured MCP connections."""

    def __init__(self, tool_registry: Optional[ToolRegistry] = None):
        self.tool_registry = tool_registry or ToolRegistry()
        self.connections: Dict[str, MCPConnection] = {}
        self._listeners: List[Callable[[str, MCPStatus], None]] = []

    def add_listener(self, callback: Callable[[str, MCPStatus], None]) -> None:
        self._listeners.append(callback)

    def _on_connection_status(self, name: str, status: MCPStatus) -> None:
        for cb in self._listeners:
            try:
                cb(name, status)
            except Exception:
                pass

    def load_from_config(self, mcp_servers_dict: Dict[str, Any]) -> None:
        """Initialize or update connections from config.yaml dictionary."""
        for name, raw_conf in mcp_servers_dict.items():
            if not isinstance(raw_conf, dict):
                continue
            cfg = MCPServerConfig.from_dict(name, raw_conf)
            if name in self.connections:
                self.connections[name].config = cfg
            else:
                conn = MCPConnection(cfg, on_status_change=self._on_connection_status)
                self.connections[name] = conn

    def get_connection(self, name: str) -> Optional[MCPConnection]:
        return self.connections.get(name)

    def list_status(self) -> List[Dict[str, Any]]:
        return [conn.to_status_dict() for conn in self.connections.values()]

    async def connect_server(self, name: str) -> None:
        conn = self.connections.get(name)
        if not conn:
            raise KeyError(f"MCP server '{name}' not found.")
        await conn.connect()
        self._register_tools_in_registry(conn)

    async def disconnect_server(self, name: str, force: bool = False) -> None:
        conn = self.connections.get(name)
        if not conn:
            return
        await conn.disconnect(force=force)

    async def restart_server(self, name: str) -> None:
        conn = self.connections.get(name)
        if not conn:
            raise KeyError(f"MCP server '{name}' not found.")
        await conn.restart()
        self._register_tools_in_registry(conn)

    async def test_server(self, name: str, temp_config: Optional[MCPServerConfig] = None) -> MCPTestResult:
        if temp_config:
            conn = MCPConnection(temp_config)
            return await conn.test()
        conn = self.connections.get(name)
        if not conn:
            raise KeyError(f"MCP server '{name}' not found.")
        return await conn.test()

    async def add_server(self, config: MCPServerConfig, connect_now: bool = False) -> None:
        if config.name in self.connections:
            await self.remove_server(config.name)

        conn = MCPConnection(config, on_status_change=self._on_connection_status)
        self.connections[config.name] = conn

        if connect_now or config.autostart:
            try:
                await conn.connect()
                self._register_tools_in_registry(conn)
            except Exception as e:
                logger.warning("MCP server '%s' autostart failed: %s", config.name, e)

    async def update_server(self, name: str, new_config: MCPServerConfig) -> None:
        """Safely update configuration: disconnect old, atomically apply new, and reconnect if was connected."""
        old_conn = self.connections.get(name)
        was_connected = old_conn.status == MCPStatus.CONNECTED if old_conn else False

        if old_conn:
            await old_conn.disconnect(force=True)
            self.tool_registry.unregister_by_source(f"mcp:{name}")

        new_conn = MCPConnection(new_config, on_status_change=self._on_connection_status)
        if name != new_config.name and name in self.connections:
            del self.connections[name]
        self.connections[new_config.name] = new_conn

        if was_connected or new_config.autostart:
            await new_conn.connect()
            self._register_tools_in_registry(new_conn)

    async def remove_server(self, name: str) -> bool:
        """Remove server entirely from runtime, unregister tools and clean up secrets."""
        conn = self.connections.pop(name, None)
        if not conn:
            return False

        await conn.disconnect(force=True)
        self.tool_registry.unregister_by_source(f"mcp:{name}")
        delete_all_mcp_secrets(name)
        return True

    def duplicate_server(self, name: str, new_name: str) -> MCPServerConfig:
        conn = self.connections.get(name)
        if not conn:
            raise KeyError(f"Server '{name}' not found.")
        d = conn.config.to_dict()
        new_cfg = MCPServerConfig.from_dict(new_name, d)
        new_conn = MCPConnection(new_cfg, on_status_change=self._on_connection_status)
        self.connections[new_name] = new_conn
        return new_cfg

    async def acquire_for_session(self, session_id: str, server_name: str) -> bool:
        """Associate session with server, starting connection if not already running."""
        conn = self.connections.get(server_name)
        if not conn or not conn.config.enabled:
            return False
        conn.used_by_sessions.add(session_id)
        if conn.status != MCPStatus.CONNECTED:
            await conn.connect()
            self._register_tools_in_registry(conn)
        return True

    async def release_for_session(self, session_id: str, server_name: str) -> None:
        """Disassociate session from server."""
        conn = self.connections.get(server_name)
        if not conn:
            return
        conn.used_by_sessions.discard(session_id)
        if not conn.used_by_sessions and not conn.config.autostart:
            # Configurable idle policy: stop non-autostart lazy servers
            await conn.disconnect()

    def remove_session_from_all(self, session_id: str) -> None:
        for conn in self.connections.values():
            conn.used_by_sessions.discard(session_id)

    def _register_tools_in_registry(self, conn: MCPConnection) -> None:
        """Register all discovered tools from an MCP server into the tool registry."""
        server_name = conn.config.name
        self.tool_registry.unregister_by_source(f"mcp:{server_name}")

        for t in conn.cached_tools:
            clean_name = f"mcp_{server_name}_{t.name}" if not t.name.startswith("mcp_") else t.name
            tool_def = ToolDefinition(
                name=clean_name,
                description=f"[{server_name} MCP tool] {t.description or t.name}",
                parameters=t.inputSchema,
                source=f"mcp:{server_name}",
            )

            orig_tool_name = t.name

            def _make_handler(c: MCPConnection, orig_name: str):
                async def handler(**kwargs):
                    res = await c.call_tool(orig_name, kwargs)
                    if res.isError:
                        return ToolResult(tool_call_id="", name=orig_name, is_error=True,
                            content="MCP tool reported an error; inspect server state before repeating the operation.",
                            metadata={"failure_kind": "tool_outcome_unknown", "request_phase": "tool_dispatched",
                                      "requires_reconciliation": True, "retry_eligible": False})
                    parts: List[ContentPart] = []
                    texts: List[str] = []
                    for item in res.content:
                        if item.type == "text" and item.text:
                            texts.append(item.text)
                            parts.append(ContentPart(type="text", text=item.text))
                        elif item.type == "image" and item.data:
                            parts.append(ContentPart(type="image", data=item.data, mime_type=item.mimeType or "image/png"))
                            texts.append(f"[MCP Image: {item.mimeType or 'image/png'}]")
                    content_str = "\n".join(texts) if texts else "Success (empty output)"
                    if any(p.type == "image" for p in parts):
                        return ToolResult(tool_call_id="", name=orig_name, content=content_str, parts=parts, is_error=False)
                    return content_str

                return handler

            handler = _make_handler(conn, orig_tool_name)
            self.tool_registry.register(tool_def, handler)

    async def autostart_servers(self) -> None:
        """Connect all servers marked with autostart=True."""
        for conn in self.connections.values():
            if conn.config.autostart and conn.config.enabled:
                try:
                    await conn.connect()
                    self._register_tools_in_registry(conn)
                except Exception as e:
                    logger.warning("Failed to autostart MCP server '%s': %s", conn.config.name, e)

    async def shutdown(self) -> None:
        """Gracefully disconnect all running servers."""
        for conn in list(self.connections.values()):
            try:
                await conn.disconnect(force=True)
            except Exception as e:
                logger.error("Error shutting down MCP connection '%s': %s", conn.config.name, e)
