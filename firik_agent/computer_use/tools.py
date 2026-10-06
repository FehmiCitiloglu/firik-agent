"""Native Agent Tool adapters for Computer Use."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from firik_agent.computer_use.models import (
    ClickType,
    ComputerUseError,
    MouseButton,
    Region,
)
from firik_agent.computer_use.service import ComputerUseService
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import ContentPart, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

COMPUTER_USE_TOOL_SOURCE = "computer-use"


def register_computer_use_tools(
    registry: ToolRegistry,
    service: ComputerUseService,
) -> None:
    """Registers all 16 computer use tools with the agent's ToolRegistry."""

    # 1. computer_environment
    async def handle_environment(**kwargs) -> Dict[str, Any]:
        env = service.environment()
        return env.model_dump()

    registry.register(
        ToolDefinition(
            name="computer_environment",
            description="Returns details about the host operating system, display server, screen scaling, security permissions, and supported computer control capabilities.",
            parameters={"type": "object", "properties": {}},
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_environment,
    )

    # 2. computer_get_displays
    async def handle_get_displays(**kwargs) -> List[Dict[str, Any]]:
        displays = service.get_displays(force_refresh=True)
        return [d.model_dump() for d in displays]


    registry.register(
        ToolDefinition(
            name="computer_get_displays",
            description="Discovers all connected physical/logical displays with bounds (x, y, width, height), scale factor, and primary display status.",
            parameters={"type": "object", "properties": {}},
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_get_displays,
    )

    # 3. computer_screenshot
    async def handle_screenshot(
        display_id: Optional[str] = None,
        window_id: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        quality: Optional[int] = None,
        format: str = "png",
        **kwargs,
    ) -> ToolResult:
        region = None
        if x is not None and y is not None and width is not None and height is not None:
            region = Region(x=int(x), y=int(y), width=int(width), height=int(height))

        res = service.screenshot(
            display_id=display_id,
            region=region,
            window_id=window_id,
            quality=quality,
            fmt=format,
        )

        return ToolResult(
            tool_call_id="",
            name="computer_screenshot",
            content=res.text_summary,
            parts=[
                ContentPart(
                    type="image",
                    data=res.base64_data,
                    mime_type=res.mime_type,
                )
            ],
        )

    registry.register(
        ToolDefinition(
            name="computer_screenshot",
            description="Takes a screenshot of the whole screen, a specific monitor, a rectangular region, or an active window with automatic Retina/HiDPI scaling.",
            parameters={
                "type": "object",
                "properties": {
                    "display_id": {"type": "string", "description": "Optional display identifier"},
                    "window_id": {"type": "string", "description": "Optional window identifier"},
                    "x": {"type": "integer", "description": "Optional region origin x (logical)"},
                    "y": {"type": "integer", "description": "Optional region origin y (logical)"},
                    "width": {"type": "integer", "description": "Optional region width (logical)"},
                    "height": {"type": "integer", "description": "Optional region height (logical)"},
                    "quality": {"type": "integer", "description": "JPEG/PNG quality (1-100)"},
                    "format": {"type": "string", "enum": ["png", "jpeg"], "default": "png"},
                },
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_screenshot,
    )

    # 4. computer_list_windows
    async def handle_list_windows(**kwargs) -> List[Dict[str, Any]]:
        windows = service.list_windows()
        return [w.model_dump() for w in windows]


    registry.register(
        ToolDefinition(
            name="computer_list_windows",
            description="Lists currently open desktop windows with their window IDs, application names, titles, and bounding boxes.",
            parameters={"type": "object", "properties": {}},
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_list_windows,
    )

    # 5. computer_focus_window
    async def handle_focus_window(window_id: str, **kwargs) -> Dict[str, Any]:
        return service.focus_window(window_id=str(window_id))

    registry.register(
        ToolDefinition(
            name="computer_focus_window",
            description="Brings a specific window to the foreground and focuses it.",
            parameters={
                "type": "object",
                "properties": {
                    "window_id": {"type": "string", "description": "ID of the window to focus"}
                },
                "required": ["window_id"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_focus_window,
    )

    # 6. computer_move_pointer
    async def handle_move_pointer(x: int, y: int, duration: float = 0.0, **kwargs) -> Dict[str, Any]:
        return service.move_pointer(x=int(x), y=int(y), duration=float(duration))

    registry.register(
        ToolDefinition(
            name="computer_move_pointer",
            description="Moves the mouse cursor to specific logical desktop coordinates (x, y).",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Logical x coordinate"},
                    "y": {"type": "integer", "description": "Logical y coordinate"},
                    "duration": {"type": "number", "description": "Smooth movement duration in seconds", "default": 0.0},
                },
                "required": ["x", "y"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_move_pointer,
    )

    # 7. computer_click
    async def handle_click(
        x: int,
        y: int,
        button: str = "left",
        click_type: str = "single",
        **kwargs,
    ) -> Dict[str, Any]:
        btn = MouseButton(button.lower()) if button.lower() in ("left", "right", "middle") else MouseButton.LEFT
        ctype = ClickType(click_type.lower()) if click_type.lower() in ("single", "double", "triple") else ClickType.SINGLE
        return service.mouse_click(x=int(x), y=int(y), button=btn, click_type=ctype)

    registry.register(
        ToolDefinition(
            name="computer_click",
            description="Clicks the mouse at specified logical coordinates (x, y) with specified button and click type.",
            parameters={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "Logical x coordinate to click"},
                    "y": {"type": "integer", "description": "Logical y coordinate to click"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                    "click_type": {"type": "string", "enum": ["single", "double", "triple"], "default": "single"},
                },
                "required": ["x", "y"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_click,
    )

    # 8. computer_drag
    async def handle_drag(
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: str = "left",
        duration: float = 0.5,
        **kwargs,
    ) -> Dict[str, Any]:
        btn = MouseButton(button.lower()) if button.lower() in ("left", "right", "middle") else MouseButton.LEFT
        return service.mouse_drag(
            start_x=int(start_x),
            start_y=int(start_y),
            end_x=int(end_x),
            end_y=int(end_y),
            button=btn,
            duration=float(duration),
        )

    registry.register(
        ToolDefinition(
            name="computer_drag",
            description="Drags the mouse cursor from start coordinates to end coordinates.",
            parameters={
                "type": "object",
                "properties": {
                    "start_x": {"type": "integer", "description": "Start x coordinate"},
                    "start_y": {"type": "integer", "description": "Start y coordinate"},
                    "end_x": {"type": "integer", "description": "End x coordinate"},
                    "end_y": {"type": "integer", "description": "End y coordinate"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                    "duration": {"type": "number", "description": "Drag duration in seconds", "default": 0.5},
                },
                "required": ["start_x", "start_y", "end_x", "end_y"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_drag,
    )

    # 9. computer_scroll
    async def handle_scroll(dx: int = 0, dy: int = 0, **kwargs) -> Dict[str, Any]:
        return service.mouse_scroll(dx=int(dx), dy=int(dy))

    registry.register(
        ToolDefinition(
            name="computer_scroll",
            description="Scrolls the mouse wheel vertically (positive = up, negative = down) and horizontally.",
            parameters={
                "type": "object",
                "properties": {
                    "dy": {"type": "integer", "description": "Vertical scroll clicks (positive up, negative down)", "default": 0},
                    "dx": {"type": "integer", "description": "Horizontal scroll clicks (positive right, negative left)", "default": 0},
                },
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_scroll,
    )

    # 10. computer_type_text
    async def handle_type_text(text: str, **kwargs) -> Dict[str, Any]:
        return service.type_text(text=str(text))

    registry.register(
        ToolDefinition(
            name="computer_type_text",
            description="Types Unicode text into the currently active element as keystrokes.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to type"}
                },
                "required": ["text"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_type_text,
    )

    # 11. computer_key
    async def handle_key(key: str, **kwargs) -> Dict[str, Any]:
        return service.press_key(key=str(key))

    registry.register(
        ToolDefinition(
            name="computer_key",
            description="Presses and releases a single key (e.g. 'Return', 'Escape', 'Tab', 'BackSpace', 'Space', 'Up', 'Down', etc.).",
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name to press (e.g. 'Return', 'Escape', 'Tab')"}
                },
                "required": ["key"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_key,
    )

    # 12. computer_hotkey
    async def handle_hotkey(keys: List[str], **kwargs) -> Dict[str, Any]:
        return service.hotkey(keys=[str(k) for k in keys])

    registry.register(
        ToolDefinition(
            name="computer_hotkey",
            description="Presses and releases a key combination simultaneously (e.g. ['Command', 'c'] or ['Ctrl', 'Shift', 'p']).",
            parameters={
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of key names to press together",
                    }
                },
                "required": ["keys"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_hotkey,
    )

    # 13. computer_get_clipboard
    async def handle_get_clipboard(**kwargs) -> Dict[str, str]:
        text = service.get_clipboard()
        return {"clipboard_content": text}

    registry.register(
        ToolDefinition(
            name="computer_get_clipboard",
            description="Reads the current text contents from the system clipboard.",
            parameters={"type": "object", "properties": {}},
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_get_clipboard,
    )

    # 14. computer_set_clipboard
    async def handle_set_clipboard(text: str, **kwargs) -> Dict[str, Any]:
        return service.set_clipboard(text=str(text))

    registry.register(
        ToolDefinition(
            name="computer_set_clipboard",
            description="Writes text contents to the system clipboard.",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy to clipboard"}
                },
                "required": ["text"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_set_clipboard,
    )

    # 15. computer_wait
    async def handle_wait(seconds: float, **kwargs) -> Dict[str, Any]:
        return service.wait(seconds=float(seconds))

    registry.register(
        ToolDefinition(
            name="computer_wait",
            description="Pauses execution for a specified number of seconds (max 30s) to allow GUI animations or page loads to finish.",
            parameters={
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "Number of seconds to wait (0.1 to 30.0)"}
                },
                "required": ["seconds"],
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_wait,
    )

    # 16. computer_accessibility_snapshot
    async def handle_accessibility_snapshot(window_id: Optional[str] = None, **kwargs) -> Optional[Dict[str, Any]]:
        tree = service.accessibility_snapshot(window_id=window_id)
        return tree.model_dump() if tree else None


    registry.register(
        ToolDefinition(
            name="computer_accessibility_snapshot",
            description="Retrieves the accessibility / UI automation element tree for an active window or application.",
            parameters={
                "type": "object",
                "properties": {
                    "window_id": {"type": "string", "description": "Optional window ID"}
                },
            },
            source=COMPUTER_USE_TOOL_SOURCE,
        ),
        handle_accessibility_snapshot,
    )

    logger.info("Successfully registered 16 computer-use native tools.")


def unregister_computer_use_tools(registry: ToolRegistry) -> List[str]:
    """Unregisters all computer use tools from the registry."""
    return registry.unregister_by_source(COMPUTER_USE_TOOL_SOURCE)
