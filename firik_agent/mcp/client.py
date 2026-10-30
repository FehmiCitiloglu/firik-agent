from __future__ import annotations
import asyncio
import threading
import json
import logging
import os
import shutil
import time
from typing import Any, Callable, Dict, List, Optional
import httpx

from firik_agent.mcp.models import redact_secrets
from firik_agent.mcp.protocol import (
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPCallToolResult,
    MCPContentItem,
    MCPToolSchema,
)

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Model Context Protocol (MCP) Client.
    Supports:
    - Stdio transport (spawns subprocess and speaks newline-delimited JSON-RPC 2.0)
    - SSE / HTTP transport (queries MCP HTTP endpoints)
    """

    def __init__(
        self,
        name: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        url: Optional[str] = None,
        cwd: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
    ):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.url = url
        self.cwd = cwd
        self.headers = headers or {}
        self.on_log = on_log
        self.is_stdio = bool(command)
        self._builtin_server = None
        self._builtin_lock = threading.Lock()

        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id: int = 1
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._is_connected: bool = False
        self._server_info: Dict[str, Any] = {}
        self._tools_cache: List[MCPToolSchema] = []
        self._pid: Optional[int] = None
        self._exit_code: Optional[int] = None
        self._connected_at: Optional[float] = None
        self._known_secrets: List[str] = list(self.env.values())

    @property
    def is_connected(self) -> bool:
        if self.is_stdio and self._process:
            if self._process.returncode is not None:
                self._is_connected = False
                self._exit_code = self._process.returncode
        return self._is_connected

    @property
    def pid(self) -> Optional[int]:
        if self._process:
            return self._process.pid
        return self._pid

    @property
    def exit_code(self) -> Optional[int]:
        if self._process and self._process.returncode is not None:
            return self._process.returncode
        return self._exit_code

    @property
    def connected_at(self) -> Optional[float]:
        return self._connected_at

    @property
    def server_info(self) -> Dict[str, Any]:
        return self._server_info

    def _log(self, level: str, message: str) -> None:
        clean_msg = redact_secrets(message, self._known_secrets)
        if self.on_log:
            try:
                self.on_log(level, clean_msg)
            except Exception:
                pass
        if level == "ERROR":
            logger.error("MCP [%s]: %s", self.name, clean_msg)
        elif level == "STDERR":
            logger.debug("MCP [%s stderr]: %s", self.name, clean_msg)
        else:
            logger.info("MCP [%s]: %s", self.name, clean_msg)

    async def connect(self) -> None:
        """Start the process / connection and perform MCP handshake."""
        if self.is_connected:
            return

        self._log("INFO", f"Connecting to MCP server '{self.name}'...")
        t0 = time.time()
        if self.command == "builtin:computer-use":
            from firik_agent.computer_use.mcp_server import ComputerUseMCPServer
            self._builtin_server = await asyncio.to_thread(ComputerUseMCPServer)
        elif self.command == "builtin:web":
            from firik_agent.mcp.web_server import WebMCPServer
            self._builtin_server = WebMCPServer()
        elif self.is_stdio:
            await self._connect_stdio()
        elif self.url:
            await self._connect_http()
        else:
            raise ValueError(f"MCP client '{self.name}' must have either command or url specified.")

        await self._initialize()
        self._is_connected = True
        self._connected_at = time.time()
        duration = (self._connected_at - t0) * 1000
        self._log("INFO", f"Connected to MCP server '{self.name}' in {duration:.1f}ms")

    async def _connect_stdio(self) -> None:
        cmd_env = os.environ.copy()
        cmd_env.update(self.env)

        exec_cmd = (shutil.which(self.command) if self.command else None) or self.command
        if not exec_cmd:
            raise FileNotFoundError(f"Command not found: '{self.command}'")

        self._log("INFO", f"Launching stdio process: {exec_cmd} {' '.join(self.args)}")
        
        # Verify working directory if specified
        valid_cwd = self.cwd if (self.cwd and os.path.isdir(self.cwd)) else None

        self._process = await asyncio.create_subprocess_exec(
            exec_cmd,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=cmd_env,
            cwd=valid_cwd,
        )
        self._pid = self._process.pid
        self._log("INFO", f"Process started with PID {self._pid}")

        self._read_task = asyncio.create_task(self._listen_stdout())
        self._stderr_task = asyncio.create_task(self._listen_stderr())

    async def _connect_http(self) -> None:
        # Handshake directly via HTTP / SSE
        self._log("INFO", f"Initializing HTTP endpoint: {self.url}")
        self._is_connected = True

    async def _listen_stdout(self) -> None:
        assert self._process and self._process.stdout
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                msg = json.loads(line_str)
                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending_requests:
                    future = self._pending_requests.pop(req_id)
                    if not future.done():
                        future.set_result(msg)
                else:
                    method = msg.get("method")
                    if method:
                        self._log("DEBUG", f"Notification: {method}")
            except json.JSONDecodeError:
                self._log("DEBUG", f"Non-JSON stdout line: {line_str}")

        self._is_connected = False
        if self._process and self._process.returncode is not None:
            self._exit_code = self._process.returncode
            self._log("INFO", f"Process terminated with exit code {self._exit_code}")

    async def _listen_stderr(self) -> None:
        assert self._process and self._process.stderr
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if line_str:
                self._log("STDERR", line_str)

    async def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Any:
        req_id = self._request_id
        self._request_id += 1

        request = JSONRPCRequest(id=req_id, method=method, params=params)

        if self._builtin_server is not None:
            server = self._builtin_server
            def handle():
                # Native actions from multiple chats must not interleave.
                with self._builtin_lock:
                    return server.handle_request(request.model_dump())
            response = await asyncio.wait_for(asyncio.to_thread(handle), timeout=timeout)
            if response and response.get("error"):
                raise RuntimeError(response["error"].get("message", "Built-in MCP error"))
            return response.get("result") if response else None

        if self.is_stdio:
            if not self._process or not self._process.stdin:
                raise RuntimeError(f"MCP server '{self.name}' stdio process is not running.")

            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self._pending_requests[req_id] = future

            data = (request.model_dump_json() + "\n").encode("utf-8")
            self._process.stdin.write(data)
            await self._process.stdin.drain()

            try:
                raw_resp = await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                self._pending_requests.pop(req_id, None)
                raise TimeoutError(f"MCP '{self.name}' timed out waiting for response to '{method}'")

            if "error" in raw_resp and raw_resp["error"]:
                err = raw_resp["error"]
                msg = err.get("message", "Unknown error")
                code = err.get("code", "")
                raise RuntimeError(f"MCP Error ({code}): {msg}")

            return raw_resp.get("result")
        else:
            # HTTP / SSE transport
            assert self.url
            merged_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            merged_headers.update(self.headers)

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self.url, json=request.model_dump(), headers=merged_headers)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data and data["error"]:
                    err = data["error"]
                    msg = err.get("message", "Unknown error")
                    code = err.get("code", "")
                    raise RuntimeError(f"MCP Error ({code}): {msg}")
                return data.get("result")

    async def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        notif = JSONRPCNotification(method=method, params=params)
        if self.is_stdio and self._process and self._process.stdin:
            data = (notif.model_dump_json() + "\n").encode("utf-8")
            self._process.stdin.write(data)
            await self._process.stdin.drain()

    async def _initialize(self) -> None:
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": False},
                "sampling": {},
            },
            "clientInfo": {
                "name": "firik-agent",
                "version": "0.2.4",
            },
        }
        res = await self._send_request("initialize", params, timeout=15.0)
        self._server_info = res or {}
        await self._send_notification("notifications/initialized")
        info = self._server_info.get("serverInfo", {})
        self._log("INFO", f"Initialized: {info.get('name', 'unknown')} v{info.get('version', '')}")

    async def list_tools(self) -> List[MCPToolSchema]:
        """Query tools available from this MCP server."""
        res = await self._send_request("tools/list", {}, timeout=15.0)
        raw_tools = res.get("tools", []) if isinstance(res, dict) else []
        tools: List[MCPToolSchema] = []
        for t in raw_tools:
            tools.append(
                MCPToolSchema(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    inputSchema=t.get("inputSchema", {"type": "object"}),
                )
            )
        self._tools_cache = tools
        self._log("INFO", f"Discovered {len(tools)} tools: {[t.name for t in tools]}")
        return tools

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> MCPCallToolResult:
        """Execute a tool on this MCP server."""
        self._log("INFO", f"Calling tool '{name}' with {len(arguments)} arguments")
        res = await self._send_request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=60.0,
        )
        if not isinstance(res, dict):
            return MCPCallToolResult(content=[MCPContentItem(type="text", text=str(res))])

        content_items = []
        for c in res.get("content", []):
            content_items.append(
                MCPContentItem(
                    type=c.get("type", "text"),
                    text=c.get("text"),
                    data=c.get("data"),
                    mimeType=c.get("mimeType"),
                )
            )

        return MCPCallToolResult(
            content=content_items,
            isError=res.get("isError", False),
        )

    def stop_local_actions(self) -> None:
        if self._builtin_server and self.command == "builtin:computer-use":
            self._builtin_server.service.stop()

    async def disconnect(self, grace_period: float = 3.0) -> None:
        """Shutdown subprocess and clean up with graceful termination followed by kill."""
        self._is_connected = False
        if self._builtin_server:
            self.stop_local_actions()
            self._builtin_server = None
        if self._read_task:
            self._read_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()

        for req_id, fut in list(self._pending_requests.items()):
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()

        if self._process:
            try:
                if self._process.returncode is None:
                    self._process.terminate()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=grace_period)
                    except asyncio.TimeoutError:
                        self._log("DEBUG", f"Process did not terminate in {grace_period}s, sending kill")
                        self._process.kill()
                        await asyncio.wait_for(self._process.wait(), timeout=2.0)
                self._exit_code = self._process.returncode
            except Exception as e:
                self._log("DEBUG", f"Exception during disconnect: {e}")
            finally:
                self._process = None

        self._log("INFO", f"Disconnected MCP server '{self.name}'")
