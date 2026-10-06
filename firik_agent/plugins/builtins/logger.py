from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
from firik_agent.core.types import Message, ToolCall, ToolResult
from firik_agent.plugins.base import BasePlugin

logger = logging.getLogger("firik.plugins.logger")


class LoggerPlugin(BasePlugin):
    """Logs lifecycle events, tool executions, and model round-trips for debugging and auditing."""

    name = "logger"
    description = "Audit logging of agent lifecycle events, tool calls, and LLM requests"
    version = "1.0.0"

    async def on_user_message(self, text: str) -> Optional[str]:
        logger.info("[Plugin:Logger] User prompt received: %s", text[:120])
        return None

    async def on_model_request(
        self,
        messages: List[Message],
        tools: List[Dict[str, Any]],
    ) -> None:
        logger.debug(
            "[Plugin:Logger] Outgoing request with %d messages, %d tools",
            len(messages),
            len(tools),
        )

    async def on_model_response(self, message: Message) -> None:
        has_tools = bool(message.tool_calls)
        logger.debug(
            "[Plugin:Logger] Received response (has_tool_calls=%s, content_len=%d)",
            has_tools,
            len(message.content or ""),
        )

    async def on_tool_call(self, tool_call: ToolCall) -> None:
        logger.info(
            "[Plugin:Logger] Tool invoking: %s with args: %s",
            tool_call.function.name,
            tool_call.function.arguments,
        )

    async def on_tool_result(self, result: ToolResult) -> None:
        logger.info(
            "[Plugin:Logger] Tool result for %s (is_error=%s): %s",
            result.name,
            result.is_error,
            result.content[:150],
        )

    async def on_error(self, error: Exception) -> None:
        logger.error("[Plugin:Logger] Agent encountered error: %s", error)
