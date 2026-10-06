"""Comprehensive test suite for Computer Use: Models, Coordinates, Security, Backends, Service, Tools, MCP, and Providers."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock

from firik_agent.computer_use.backends.mock import MockComputerBackend
from firik_agent.computer_use.coordinates import CoordinateManager
from firik_agent.computer_use.models import (
    ActionBlockedError,
    ClickType,
    ComputerUseError,
    DisplayInfo,
    EnvironmentInfo,
    InvalidCoordinatesError,
    MouseButton,
    Point,
    Region,
    ScreenshotMetadata,
    ScreenshotResult,
    UserCancelledError,
    WindowInfo,
)
from firik_agent.computer_use.mcp_server import ComputerUseMCPServer
from firik_agent.computer_use.screenshots import ScreenshotProcessor
from firik_agent.computer_use.security import ComputerUseConfig, SecurityManager
from firik_agent.computer_use.service import ComputerUseService
from firik_agent.computer_use.tools import (
    register_computer_use_tools,
    unregister_computer_use_tools,
)
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import ContentPart, Message, ModelInfo, ToolCall, ToolCallFunction, ToolResult
from firik_agent.providers.firik import FirikProvider
from firik_agent.providers.openai_provider import OpenAIProvider


# ============================================================================
# 1. Models & Geometry
# ============================================================================

def test_models_geometry():
    pt = Point(x=100, y=200)
    assert pt.x == 100 and pt.y == 200

    reg = Region(x=10, y=20, width=800, height=600)
    assert reg.width == 800 and reg.height == 600

    disp = DisplayInfo(
        id="disp_1",
        name="Main Monitor",
        x=0,
        y=0,
        width=1920,
        height=1080,
        scale_factor=2.0,
        is_primary=True,
    )
    assert disp.is_primary is True
    assert disp.scale_factor == 2.0


# ============================================================================
# 2. Coordinates & Multi-Monitor Logic
# ============================================================================

def test_coordinate_manager_multi_monitor():
    displays = [
        DisplayInfo(id="d1", name="Left Monitor", x=-1920, y=0, width=1920, height=1080, is_primary=False),
        DisplayInfo(id="d2", name="Primary Monitor", x=0, y=0, width=2560, height=1440, is_primary=True),
    ]

    min_x, min_y, max_x, max_y = CoordinateManager.get_virtual_bounds(displays)
    assert min_x == -1920
    assert min_y == 0
    assert max_x == 2560
    assert max_y == 1440

    # Test display lookup
    d_left = CoordinateManager.find_display_for_point(displays, -500, 500)
    assert d_left is not None and d_left.id == "d1"

    d_primary = CoordinateManager.find_display_for_point(displays, 100, 100)
    assert d_primary is not None and d_primary.id == "d2"

    d_none = CoordinateManager.find_display_for_point(displays, 5000, 5000)
    assert d_none is None

    # Test clamping
    clamped = CoordinateManager.validate_and_clamp(displays, 5000, 5000, strict=False)
    assert clamped.x == 2559
    assert clamped.y == 1439

    with pytest.raises(InvalidCoordinatesError):
        CoordinateManager.validate_and_clamp(displays, 5000, 5000, strict=True)

    # Scaling transforms
    phys_x, phys_y = CoordinateManager.logical_to_physical(100, 200, scale_factor=2.0)
    assert phys_x == 200 and phys_y == 400
    log_x, log_y = CoordinateManager.physical_to_logical(200, 400, scale_factor=2.0)
    assert log_x == 100 and log_y == 200


# ============================================================================
# 3. Security Manager & Emergency Stop
# ============================================================================

def test_security_manager_emergency_stop():
    config = ComputerUseConfig(max_actions_per_turn=5)
    sec = SecurityManager(config)

    assert not sec.is_stopped()
    sec.check_stop()  # Should not raise

    sec.request_stop()
    assert sec.is_stopped()

    with pytest.raises(UserCancelledError):
        sec.check_stop()

    sec.reset_stop()
    assert not sec.is_stopped()
    sec.check_stop()


def test_security_manager_blocked_apps():
    sec = SecurityManager()
    assert sec.is_app_blocked("Terminal")
    assert sec.is_app_blocked("cmd.exe")
    assert sec.is_app_blocked("System Settings")
    assert not sec.is_app_blocked("Google Chrome")
    assert not sec.is_app_blocked("Slack")


def test_security_manager_sensitive_redaction_and_destructive():
    sec = SecurityManager()
    assert sec.is_text_destructive("rm -rf /")
    assert sec.is_text_destructive("Confirm Purchase now")
    assert not sec.is_text_destructive("Hello world")

    redacted = sec.redact_sensitive_text("Authorization: Bearer my-secret-api-key-123456")
    assert "my-secret-api-key-123456" not in redacted
    assert "[REDACTED]" in redacted


# ============================================================================
# 4. Screenshot Processing & Multimodal Formatting
# ============================================================================

def test_screenshot_processor():
    from PIL import Image
    import io

    # Create dummy raw PNG bytes
    img = Image.new("RGB", (800, 600), color=(73, 109, 137))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    result = ScreenshotProcessor.process_image(
        raw_bytes=raw_bytes,
        scale_factor=2.0,
        fmt="png",
    )

    assert result.metadata.logical_width == 400
    assert result.metadata.logical_height == 300
    assert result.metadata.scale_factor == 2.0
    assert result.mime_type == "image/png"
    assert len(result.base64_data) > 0
    assert "[Screenshot: 400x300 logical" in result.text_summary


# ============================================================================
# 5. Mock Backend Functionality
# ============================================================================

def test_mock_backend():
    backend = MockComputerBackend()
    env = backend.detect_environment()
    assert env.os == "MockOS"

    displays = backend.get_displays()
    assert len(displays) == 2
    assert displays[0].is_primary is True

    # Screen capture
    raw_bytes, scale, ox, oy = backend.capture_screen()
    assert len(raw_bytes) > 0
    assert scale == 2.0


    # Pointer & Mouse
    backend.move_pointer(150, 250)
    assert backend.cursor_x == 150 and backend.cursor_y == 250

    backend.mouse_click(150, 250, button=MouseButton.RIGHT, click_type=ClickType.DOUBLE)
    backend.mouse_drag(10, 20, 100, 200)
    backend.mouse_scroll(0, -5)

    # Keyboard & Clipboard
    backend.type_text("Hello from test!")
    backend.press_key("Return")
    backend.hotkey(["Ctrl", "c"])

    backend.set_clipboard("Test Clipboard")
    assert backend.get_clipboard() == "Test Clipboard"

    # Windows & Accessibility
    windows = backend.list_windows()
    assert len(windows) > 0
    assert backend.focus_window(windows[0].id) is True

    tree = backend.get_accessibility_tree()
    assert tree is not None
    assert tree.role == "AXWindow"
    assert len(tree.children) == 2

    # Check action history recorded
    assert len(backend.action_history) >= 8


# ============================================================================
# 6. Canonical ComputerUseService Orchestrator
# ============================================================================

def test_computer_use_service_lifecycle():
    backend = MockComputerBackend()
    service = ComputerUseService(backend=backend)

    # Environment & displays
    env = service.environment()
    assert env.os == "MockOS"
    displays = service.get_displays()
    assert len(displays) == 2


    # Screenshot
    ss = service.screenshot()
    assert ss.metadata.logical_width == 1920
    assert ss.mime_type == "image/png"

    # Pointer and clicks
    click_res = service.mouse_click(50, 75, button=MouseButton.LEFT)
    assert click_res["success"] is True

    type_res = service.type_text("Autonomous test")
    assert type_res["chars_typed"] == 15

    clip_res = service.set_clipboard("Secret data")
    assert clip_res["success"] is True
    assert service.get_clipboard() == "Secret data"

    # Emergency stop
    service.stop()
    assert service.is_stopped()
    with pytest.raises(UserCancelledError):
        service.mouse_click(10, 10)

    service.reset_stop()
    assert not service.is_stopped()
    assert service.mouse_click(10, 10)["success"] is True


# ============================================================================
# 7. Native Agent Tools Registration & Execution
# ============================================================================

@pytest.mark.asyncio
async def test_native_agent_tools_execution():
    registry = ToolRegistry()
    service = ComputerUseService(use_mock=True)

    register_computer_use_tools(registry, service)
    tools = registry.list_tools()
    assert len(tools) == 16

    # 1. Test computer_environment
    call_env = ToolCall(id="call_1", type="function", function=ToolCallFunction(name="computer_environment", arguments="{}"))
    res_env = await registry.execute(call_env)
    assert not res_env.is_error
    env_data = json.loads(res_env.content)
    assert "os" in env_data

    # 2. Test computer_screenshot with multimodal parts
    call_ss = ToolCall(id="call_2", type="function", function=ToolCallFunction(name="computer_screenshot", arguments="{}"))
    res_ss = await registry.execute(call_ss)
    assert not res_ss.is_error
    assert "[Screenshot:" in res_ss.content
    assert res_ss.parts is not None
    assert len(res_ss.parts) == 1
    assert res_ss.parts[0].type == "image"
    assert res_ss.parts[0].mime_type == "image/png"

    # 3. Test computer_click
    call_click = ToolCall(
        id="call_3",
        type="function",
        function=ToolCallFunction(name="computer_click", arguments=json.dumps({"x": 100, "y": 200, "button": "left"})),
    )
    res_click = await registry.execute(call_click)
    assert not res_click.is_error
    assert "success" in res_click.content

    # 4. Test unregister
    removed = unregister_computer_use_tools(registry)
    assert len(removed) == 16
    assert len(registry.list_tools()) == 0


# ============================================================================
# 8. MCP Server Stdio JSON-RPC Protocol
# ============================================================================

def test_mcp_server_protocol():
    server = ComputerUseMCPServer(use_mock=True)

    # 1. Initialize
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = server.handle_request(init_req)
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "firik-computer-use"

    # 2. Tools list
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    resp = server.handle_request(list_req)
    tools = resp["result"]["tools"]
    assert len(tools) == 16
    tool_names = [t["name"] for t in tools]
    assert "computer_screenshot" in tool_names
    assert "computer_click" in tool_names

    # 3. Tools call: screenshot (multimodal return)
    call_ss = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "computer_screenshot", "arguments": {}},
    }
    resp = server.handle_request(call_ss)
    content = resp["result"]["content"]
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image"
    assert "data" in content[1]

    # 4. Tools call: click
    call_click = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "computer_click", "arguments": {"x": 200, "y": 300, "button": "left"}},
    }
    resp = server.handle_request(call_click)
    assert not resp["result"].get("isError")

    # 5. Tools call: unknown
    call_unknown = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "non_existent_tool", "arguments": {}},
    }
    resp = server.handle_request(call_unknown)
    assert resp["result"]["isError"] is True


# ============================================================================
# 9. Multimodal Message Serialization for Vision Models
# ============================================================================

def test_multimodal_provider_formatting():
    # 1. FirikProvider with vision model (qwen3-vl-30b)
    firik_prov = FirikProvider(api_key="test_key")
    firik_prov.set_model("qwen3-vl-30b")

    img_part = ContentPart(type="image", data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", mime_type="image/png")
    tool_msg = Message(
        role="tool",
        name="computer_screenshot",
        tool_call_id="call_1",
        content="[Screenshot: 800x600]",
        parts=[img_part],
    )

    payload = firik_prov._prepare_payload([tool_msg])
    msg_dict = payload["messages"][0]
    # Vision model receives array of {type: text/image_url}
    assert isinstance(msg_dict["content"], list)
    assert msg_dict["content"][0]["type"] == "text"
    assert msg_dict["content"][1]["type"] == "image_url"
    assert "data:image/png;base64," in msg_dict["content"][1]["image_url"]["url"]

    # 2. FirikProvider with non-vision model (glm-5.3)
    firik_prov.set_model("glm-5.3")
    payload_non_vision = firik_prov._prepare_payload([tool_msg])
    # Non-vision model receives clean text string, NOT a base64 dump
    assert isinstance(payload_non_vision["messages"][0]["content"], str)
    assert payload_non_vision["messages"][0]["content"] == "[Screenshot: 800x600]"

    # 3. OpenAIProvider with vision model (gpt-4o)
    openai_prov = OpenAIProvider(api_key="test_key", default_model="gpt-4o")
    openai_payload = openai_prov._prepare_payload([tool_msg])
    assert isinstance(openai_payload["messages"][0]["content"], list)
    assert openai_payload["messages"][0]["content"][1]["type"] == "image_url"
