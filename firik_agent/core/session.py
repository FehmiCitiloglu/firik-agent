from __future__ import annotations
import datetime
import asyncio
import inspect
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from firik_agent.core.compaction import CompactionState
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, ToolCall, ToolResult
from firik_agent.mcp.connection import MCPConnectionManager
from firik_agent.mcp.models import ToolPolicyMode

logger = logging.getLogger(__name__)


class ChatSession:
    """
    Encapsulates all state for an individual chat session:
    - Messages history
    - Model & generation hyperparameters
    - System prompt
    - Per-chat enabled MCP servers (active_mcp_servers)
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        title: str = "Yeni Sohbet",
        model: str = "glm-5.3",
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        active_mcp_servers: Optional[Set[str]] = None,
        enabled_tool_names: Optional[Set[str]] = None,
        reasoning_effort: Optional[str] = None,
        private: bool = False,
        learning_enabled: bool = True,
    ):
        self.session_id: str = session_id or f"session_{uuid4().hex[:12]}"
        self.title: str = title
        self.model: str = model
        self.reasoning_effort: Optional[str] = reasoning_effort
        self.system_prompt: str = system_prompt
        self.temperature: float = temperature
        self.max_tokens: int = max_tokens
        self.active_mcp_servers: Set[str] = set(active_mcp_servers or [])
        self.enabled_tool_names: Optional[Set[str]] = set(enabled_tool_names) if enabled_tool_names is not None else None
        self.messages: List[Message] = []
        self.compaction = CompactionState()
        self.created_at: float = time.time()
        self.private = bool(private)
        self.learning_enabled = bool(learning_enabled)
        # Browser authority is ephemeral and explicitly granted by the host for
        # this conversation. Restoring an old chat never restores authority.
        self.browser_enabled = False
        self.browser_origins: tuple[str, ...] = ()
        self.browser_private_origins: tuple[str, ...] = ()
        self.browser_screenshots = False
        self.browser_tool_approval_required = False
        self.browser_approval_epoch = 0

    def requires_external_tool_approval(self) -> bool:
        # Page instructions remain untrusted after Browser is disabled or a
        # chat is restored. The flag also survives compaction via chat storage.
        if self.browser_enabled or any(message.role == "tool" and (
                (message.name or "").startswith("browser_") or message.execution.get("browser") is True)
                for message in self.messages):
            self.browser_tool_approval_required = True
        return self.browser_tool_approval_required

    def enable_mcp(self, server_name: str) -> None:
        self.active_mcp_servers.add(server_name)

    def disable_mcp(self, server_name: str) -> None:
        self.active_mcp_servers.discard(server_name)

    def is_mcp_enabled(self, server_name: str) -> bool:
        return server_name in self.active_mcp_servers

    def clear(self) -> None:
        self.messages.clear()
        self.compaction.reset()
        self.browser_tool_approval_required = bool(self.browser_enabled)
        self.browser_approval_epoch += 1


class SessionToolFilter:
    """
    Session-scoped tool filtering and execution gatekeeper.

    Ensures that a chat session only sees and can execute:
    - Built-in / Meta tools
    - Active plugin tools
    - MCP tools belonging to servers explicitly enabled in this session's active_mcp_servers.
    """

    def __init__(self, tool_registry: ToolRegistry, connection_manager: Optional[MCPConnectionManager] = None,
                 mcp_registry: Optional[ToolRegistry] = None, browser_adapter=None,
                 external_tool_authorize=None):
        self.tool_registry = tool_registry
        self.connection_manager = connection_manager
        self.mcp_registry = mcp_registry
        self.browser_adapter = browser_adapter
        self.external_tool_authorize = external_tool_authorize
        self._browser_registry = ToolRegistry()
        if browser_adapter is not None:
            async def reserved_browser_handler(**kwargs):
                raise PermissionError("Browser tools require the session authorization adapter.")
            for definition in browser_adapter.definitions():
                self._browser_registry.register(definition, reserved_browser_handler)

    def _browser_allowed(self, tool_name: str, session: ChatSession) -> bool:
        adapter = self.browser_adapter
        return bool(adapter is not None and session.browser_enabled
                    and adapter.identity.conversation_id == session.session_id
                    and (session.enabled_tool_names is None or tool_name in session.enabled_tool_names)
                    and adapter.is_tool_allowed(tool_name))

    def _registry_for(self, name: str) -> ToolRegistry:
        if name.startswith("browser_"):
            return self._browser_registry
        if self.mcp_registry and self.mcp_registry.get_tool(name):
            return self.mcp_registry
        return self.tool_registry

    def is_tool_allowed(self, tool_name: str, session: ChatSession) -> bool:
        """Check if a tool is permitted in the context of the given ChatSession."""
        if tool_name.startswith("browser_"):
            return self._browser_allowed(tool_name, session)
        if session.requires_external_tool_approval() and self.external_tool_authorize is None:
            return False
        tool_def = self._registry_for(tool_name).get_tool(tool_name)
        if not tool_def:
            return False

        source = tool_def.source or ""
        if source.startswith("browser") or source == "native:browser":
            return False  # Browser tools never execute through a global registry.

        # Builtin, Meta, or Plugin tools are always permitted
        if source.startswith("meta:") or source.startswith("builtin") or source.startswith("plugin:"):
            if session.enabled_tool_names is not None and tool_name not in session.enabled_tool_names:
                return False
            return True

        # MCP tools: format is 'mcp:<server_name>'
        if source.startswith("mcp:"):
            server_name = source[4:]
            if server_name not in session.active_mcp_servers:
                return False

            # Check server-level tool policy if available
            if self.connection_manager:
                conn = self.connection_manager.get_connection(server_name)
                if conn:
                    if not conn.config.enabled:
                        return False
                    policy = conn.config.tool_policy
                    if policy.mode == ToolPolicyMode.DENY:
                        return False
                    if policy.allowed_tools and tool_name not in policy.allowed_tools:
                        return False

            if session.enabled_tool_names is not None and tool_name not in session.enabled_tool_names:
                return False

            return True

        return True

    def get_schemas(self, session: ChatSession) -> List[Dict[str, Any]]:
        """Return tool schemas filtered strictly for the given chat session."""
        session.requires_external_tool_approval()
        all_tools = self.tool_registry.list_tools()
        if self.mcp_registry:
            merged = {tool.name: tool for tool in all_tools}
            merged.update({tool.name: tool for tool in self.mcp_registry.list_tools()})
            all_tools = list(merged.values())
        schemas = []
        for t in all_tools:
            if t.name.startswith("browser_"):
                continue
            if self.is_tool_allowed(t.name, session):
                schemas.append(t.to_schema())
        if self.browser_adapter is not None:
            for definition in self.browser_adapter.definitions():
                if self._browser_allowed(definition.name, session):
                    schemas.append(definition.to_schema())
        return schemas

    async def execute(self, tool_call: ToolCall, session: ChatSession) -> ToolResult:
        """Execute a tool call verifying session permissions."""
        name = tool_call.function.name
        if not self.is_tool_allowed(name, session):
            return ToolResult(
                tool_call_id=tool_call.id,
                name=name,
                content=f"Error: Tool '{name}' is not enabled for this chat session.",
                is_error=True,
                metadata={"failure_kind": "permission_denied", "request_phase": "tool_not_dispatched"},
            )

        if name.startswith("browser_"):
            session.requires_external_tool_approval()
            return await self.browser_adapter.execute(tool_call)
        if session.requires_external_tool_approval():
            return await self._execute_external(tool_call, session)
        return await self._registry_for(name).execute(tool_call)

    async def _execute_external(self, tool_call: ToolCall, session: ChatSession) -> ToolResult:
        """A page cannot borrow unrelated tools' ambient host authority."""
        name = tool_call.function.name
        def denied(kind="permission_denied", message="This exact tool operation requires trusted host approval."):
            return ToolResult(tool_call_id=tool_call.id, name=name, content=message, is_error=True,
                metadata={"failure_kind": kind, "request_phase": "tool_not_dispatched"})
        if self.external_tool_authorize is None:
            return denied()
        authority = self.external_tool_authorize
        try:
            raw = tool_call.function.arguments or "{}"
            if len(raw) > 128000:
                raise ValueError()
            def invalid_constant(value):
                raise ValueError()
            arguments = json.loads(raw, parse_constant=invalid_constant)
            if not isinstance(arguments, dict):
                raise ValueError()
            # Dispatch the reviewed canonical copy, never a mutable caller's
            # ToolCall or the argument dictionary supplied to the UI callback.
            canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, allow_nan=False)
            approved_call = tool_call.model_copy(deep=True)
            approved_call.function.arguments = canonical
        except (ValueError, TypeError):
            return denied("invalid_tool_arguments", "Tool arguments must be a bounded valid JSON object.")
        registry = self._registry_for(name)
        definition = registry.get_tool(name)
        if definition is None:
            return denied()
        definition_state = definition.model_dump(mode="json")
        handler = registry._handlers.get(name)
        scope = (session.session_id, session.browser_enabled, session.browser_origins,
                 session.browser_private_origins, session.browser_screenshots, session.browser_approval_epoch)
        try:
            decision = authority(name, json.loads(canonical))
            if inspect.isawaitable(decision):
                decision = await asyncio.wait_for(decision, timeout=120)
        except asyncio.CancelledError:
            raise
        except Exception:
            return denied()
        if decision is not True:
            return denied()
        current = self._registry_for(name)
        if (scope != (session.session_id, session.browser_enabled, session.browser_origins,
                      session.browser_private_origins, session.browser_screenshots, session.browser_approval_epoch)
                or not self.is_tool_allowed(name, session) or current is not registry
                or self.external_tool_authorize is not authority
                or current.get_tool(name) is not definition
                or definition.model_dump(mode="json") != definition_state
                or registry._handlers.get(name) is not handler):
            return denied(message="Tool authority or target changed while approval was pending.")
        result = await registry.execute(approved_call)
        result.metadata.update(host_external_approval=True, browser_untrusted_context=True)
        return result
