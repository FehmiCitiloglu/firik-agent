from firik_agent.core.types import ToolResult
from firik_agent.mcp.protocol import MCPCallToolResult, MCPContentItem
from firik_agent.mcp.results import tool_result_from_mcp


def test_mcp_mixed_content_keeps_images_and_text_in_order():
    result = tool_result_from_mcp(MCPCallToolResult(content=[
        MCPContentItem(type="text", text="Before"),
        MCPContentItem(type="image", data="image-one", mimeType="image/jpeg"),
        MCPContentItem(type="text", text="After"),
        MCPContentItem(type="image", data="image-two"),
    ]), "capture")

    assert isinstance(result, ToolResult)
    assert not result.is_error
    assert result.content == "Before\n[MCP Image: image/jpeg]\nAfter\n[MCP Image: image/png]"
    assert [(part.type, part.text, part.data, part.mime_type) for part in result.parts] == [
        ("text", "Before", None, None),
        ("image", None, "image-one", "image/jpeg"),
        ("text", "After", None, None),
        ("image", None, "image-two", "image/png"),
    ]
