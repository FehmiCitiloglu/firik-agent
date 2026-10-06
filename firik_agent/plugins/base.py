from __future__ import annotations
from abc import ABC
from typing import Any, Callable, Dict, List, Optional, Tuple
from firik_agent.core.types import Message, ToolCall, ToolDefinition, ToolResult


class BasePlugin(ABC):
    """
    Base class for Agent plugins.
    Plugins can hook into the agent lifecycle and provide custom tools.
    """

    name: str = "base_plugin"
    description: str = "Base plugin"
    version: str = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.is_enabled = True

    def get_tools(self) -> List[Tuple[ToolDefinition, Callable[..., Any]]]:
        """Return list of (ToolDefinition, Callable) pairs provided by this plugin."""
        return []

    async def on_agent_init(self, agent: Any) -> None:
        """Called when agent finishes initialization."""
        pass

    async def on_user_message(self, text: str) -> Optional[str]:
        """
        Called when a user inputs a message.
        Can return modified text, or None to keep original.
        """
        return None

    async def on_model_request(
        self,
        messages: List[Message],
        tools: List[Dict[str, Any]],
    ) -> None:
        """Called right before sending messages to the LLM provider."""
        pass

    async def on_model_response(self, message: Message) -> None:
        """Called when LLM provider returns a response message."""
        pass

    async def on_tool_call(self, tool_call: ToolCall) -> None:
        """Called right before a tool is executed."""
        pass

    async def on_tool_result(self, result: ToolResult) -> None:
        """Called right after a tool finishes execution."""
        pass

    async def on_error(self, error: Exception) -> None:
        """Called when an unhandled error occurs during execution."""
        pass
