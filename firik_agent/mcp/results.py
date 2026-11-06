"""Convert MCP output without treating remote failures as successful evidence."""
from __future__ import annotations

from firik_agent.core.types import ContentPart, ToolResult
from firik_agent.mcp.protocol import MCPCallToolResult


def tool_result_from_mcp(result: MCPCallToolResult, name: str) -> str | ToolResult:
    if result.isError:
        # A server-reported failure does not prove that a remote mutation had
        # no effect. Keep replay protection, while letting the agent work with
        # other evidence. Remote error bodies may contain credentials or data.
        return ToolResult(
            tool_call_id="",
            name=name,
            is_error=True,
            content=(
                "MCP tool reported an error. Continue with available evidence or independent tools, "
                "and explain any remaining gaps in the answer. The operation may have had side effects; "
                "inspect server state before repeating it."
            ),
            metadata={
                "failure_kind": "tool_outcome_unknown",
                "request_phase": "tool_dispatched",
                "requires_reconciliation": True,
                "retry_eligible": False,
            },
        )

    parts: list[ContentPart] = []
    texts: list[str] = []
    for item in result.content:
        if item.type == "text" and item.text:
            texts.append(item.text)
            parts.append(ContentPart(type="text", text=item.text))
        elif item.type == "image" and item.data:
            mime_type = item.mimeType or "image/png"
            parts.append(ContentPart(type="image", data=item.data, mime_type=mime_type))
            texts.append(f"[MCP Image: {mime_type}]")
    content = "\n".join(texts) if texts else "Success (empty output)"
    if any(part.type == "image" for part in parts):
        return ToolResult(tool_call_id="", name=name, content=content, parts=parts)
    return content
