from __future__ import annotations
import datetime
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

    def enable_mcp(self, server_name: str) -> None:
        self.active_mcp_servers.add(server_name)

    def disable_mcp(self, server_name: str) -> None:
        self.active_mcp_servers.discard(server_name)

    def is_mcp_enabled(self, server_name: str) -> bool:
        return server_name in self.active_mcp_servers

    def clear(self) -> None:
        self.messages.clear()
        self.compaction.reset()


class SessionToolFilter:
    """
    Session-scoped tool filtering and execution gatekeeper.

    Ensures that a chat session only sees and can execute:
    - Built-in / Meta tools
    - Active plugin tools
    - MCP tools belonging to servers explicitly enabled in this session's active_mcp_servers.
    """

    def __init__(self, tool_registry: ToolRegistry, connection_manager: Optional[MCPConnectionManager] = None,
                 mcp_registry: Optional[ToolRegistry] = None):
        self.tool_registry = tool_registry
        self.connection_manager = connection_manager
        self.mcp_registry = mcp_registry

    def _registry_for(self, name: str) -> ToolRegistry:
        if self.mcp_registry and self.mcp_registry.get_tool(name):
            return self.mcp_registry
        return self.tool_registry

    def is_tool_allowed(self, tool_name: str, session: ChatSession) -> bool:
        """Check if a tool is permitted in the context of the given ChatSession."""
        tool_def = self._registry_for(tool_name).get_tool(tool_name)
        if not tool_def:
            return False

        source = tool_def.source or ""

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
        all_tools = self.tool_registry.list_tools()
        if self.mcp_registry:
            merged = {tool.name: tool for tool in all_tools}
            merged.update({tool.name: tool for tool in self.mcp_registry.list_tools()})
            all_tools = list(merged.values())
        schemas = []
        for t in all_tools:
            if self.is_tool_allowed(t.name, session):
                schemas.append(t.to_schema())
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

        return await self._registry_for(name).execute(tool_call)
