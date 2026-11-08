"""Offline regressions for honest MCP/computer screenshot delivery feedback."""
import json

import pytest

from firik_agent.core.types import ContentPart, Message, ToolCall, ToolCallFunction
from firik_agent.mcp.protocol import MCPCallToolResult, MCPContentItem
from firik_agent.mcp.results import tool_result_from_mcp
from firik_agent.providers.firik import FirikProvider
from firik_agent.providers.openai_provider import OpenAIProvider


IMAGE = "private-base64-image-data"
NOTICE = "Image data was not delivered to the selected model"


def screenshot_message():
    result = tool_result_from_mcp(MCPCallToolResult(content=[
        MCPContentItem(type="text", text="Screenshot captured: 800x600"),
        MCPContentItem(type="image", data=IMAGE, mimeType="image/png"),
    ]), "computer_screenshot")
    result.tool_call_id = "capture"
    result.metadata["private_host_metadata"] = "must stay out of provider context"
    return result.to_message()


@pytest.mark.parametrize("provider_type,model,metadata,delivered", [
    (FirikProvider, "deepseek-v4.1-flash", {}, False),
    (FirikProvider, "deepseek-v4.1-flash", {"source": "provider", "supports_vision": True}, True),
    (FirikProvider, "qwen3-vl-30b", {"source": "provider", "supports_vision": False}, False),
    (FirikProvider, "qwen3-vl-30b", {}, True),
    (OpenAIProvider, "explicit-model", {"source": "verified_config", "supports_vision": True}, True),
    (OpenAIProvider, "gpt-4o", {"source": "verified_probe", "supports_vision": False}, False),
    (OpenAIProvider, "explicit-model", {}, False),
    (OpenAIProvider, "gpt-4o", {}, True),
    (FirikProvider, "deepseek-v4.1-flash", {"supports_vision": True}, False),
    (OpenAIProvider, "gpt-4o", {"supports_vision": False}, True),
    (FirikProvider, "deepseek-v4.1-flash", {"source": "provider", "supports_vision": True, "verified": False}, False),
])
def test_explicit_capabilities_override_legacy_image_heuristic(provider_type, model, metadata, delivered):
    provider = provider_type(default_model=model, api_key="unused")
    provider.model_metadata[model] = metadata
    message = screenshot_message()
    before = message.model_dump()

    payload = provider._prepare_payload([message])
    row = payload["messages"][0]
    encoded = json.dumps(payload)

    assert (IMAGE in encoded) is delivered
    assert (NOTICE in encoded) is not delivered
    assert "Screenshot captured: 800x600" in encoded
    assert row["role"] == "tool" and row["tool_call_id"] == "capture"
    assert row["name"] == "computer_screenshot"
    assert "private_host_metadata" not in encoded
    assert message.model_dump() == before
    assert provider.current_model == model
    if not delivered:
        assert isinstance(row["content"], str)
        assert "Do not infer what the image shows or repeat the capture" in row["content"]
        assert "Use available text or DOM observations instead" in row["content"]


@pytest.mark.parametrize("provider_type", [FirikProvider, OpenAIProvider])
def test_no_vision_feedback_preserves_tool_batch_protocol_and_original_images(provider_type):
    provider = provider_type(default_model="deepseek-v4.1-flash", api_key="unused")
    image = screenshot_message()
    messages = [Message(role="assistant", tool_calls=[
        ToolCall(id="capture", function=ToolCallFunction(name="computer_screenshot", arguments="{}")),
        ToolCall(id="windows", function=ToolCallFunction(name="computer_list_windows", arguments="{}")),
    ]), image, Message(role="tool", name="computer_list_windows", tool_call_id="windows", content="Finder")]
    before = [message.model_dump() for message in messages]

    rows = provider._prepare_payload(messages)["messages"]
    assert [row["role"] for row in rows] == ["assistant", "tool", "tool"]
    assert NOTICE in rows[1]["content"]
    assert rows[2]["content"] == "Finder"
    assert IMAGE not in json.dumps(rows)
    assert [message.model_dump() for message in messages] == before

    provider.model_metadata[provider.current_model] = {"source": "provider", "supports_vision": True}
    rows = provider._prepare_payload(messages)["messages"]
    assert IMAGE in json.dumps(rows)
    assert NOTICE not in json.dumps(rows)
    assert [message.model_dump() for message in messages] == before


@pytest.mark.parametrize("content", [None, "Existing summary", [{"type": "text", "text": "Existing summary"}]])
def test_omitted_image_preserves_text_parts_without_mutating_history(content):
    provider = FirikProvider(default_model="deepseek-v4.1-flash", api_key="unused")
    message = Message(role="tool", name="computer_screenshot", tool_call_id="capture", content=content,
        parts=[ContentPart(type="text", text="Accessible text"), ContentPart(type="image", data=IMAGE)])
    before = message.model_dump()
    row = provider._prepare_payload([message])["messages"][0]
    encoded = json.dumps(row)
    assert "Accessible text" in encoded and NOTICE in encoded
    assert IMAGE not in encoded
    if content:
        assert "Existing summary" in encoded
    assert message.model_dump() == before


def test_native_browser_artifact_path_keeps_its_independent_opt_in():
    provider = FirikProvider(default_model="deepseek-v4.1-flash", api_key="unused")
    provider.model_metadata[provider.current_model] = {"source": "provider", "supports_vision": True}
    message = Message(role="tool", name="browser_screenshot", tool_call_id="capture", content="Artifact ready",
        parts=[ContentPart(type="image", data=IMAGE)], execution={"browser_model_image": True})
    rows = provider._prepare_payload([message])["messages"]
    assert rows == [{"role": "tool", "name": "browser_screenshot", "tool_call_id": "capture", "content": "Artifact ready"}]
    assert NOTICE not in json.dumps(rows)
    assert IMAGE not in json.dumps(rows)
