"""Standalone stdio JSON-RPC 2.0 MCP server for Computer Use."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from firik_agent.computer_use.models import (
    ClickType,
    ComputerUseError,
    MouseButton,
    Region,
)
from firik_agent.computer_use.service import ComputerUseService
from firik_agent.mcp.protocol import (
    JSONRPCResponse,
    MCPCallToolResult,
    MCPContentItem,
    MCPToolSchema,
)

logger = logging.getLogger(__name__)

SERVER_VERSION = "0.2.4"
SERVER_NAME = "firik-computer-use"
PROTOCOL_VERSION = "2024-11-05"

# Tool Schemas for MCP
TOOLS_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "computer_environment",
        "description": "Returns details about the host operating system, display server, screen scaling, security permissions, and supported computer control capabilities.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "computer_get_displays",
        "description": "Discovers all connected physical/logical displays with bounds (x, y, width, height), scale factor, and primary display status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "computer_screenshot",
        "description": "Takes a screenshot of the whole screen, a specific monitor, a rectangular region, or an active window with automatic Retina/HiDPI scaling.",
        "inputSchema": {
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
    },
    {
        "name": "computer_list_windows",
        "description": "Lists currently open desktop windows with their window IDs, application names, titles, and bounding boxes.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "computer_focus_window",
        "description": "Brings a specific window to the foreground and focuses it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window_id": {"type": "string", "description": "ID of the window to focus"}
            },
            "required": ["window_id"],
        },
    },
    {
        "name": "computer_move_pointer",
        "description": "Moves the mouse cursor to specific logical desktop coordinates (x, y).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Logical x coordinate"},
                "y": {"type": "integer", "description": "Logical y coordinate"},
                "duration": {"type": "number", "description": "Smooth movement duration in seconds", "default": 0.0},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "computer_click",
        "description": "Clicks the mouse at specified logical coordinates (x, y) with specified button and click type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Logical x coordinate to click"},
                "y": {"type": "integer", "description": "Logical y coordinate to click"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "click_type": {"type": "string", "enum": ["single", "double", "triple"], "default": "single"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "computer_drag",
        "description": "Drags the mouse cursor from start coordinates to end coordinates.",
        "inputSchema": {
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
    },
    {
        "name": "computer_scroll",
        "description": "Scrolls the mouse wheel vertically (positive = up, negative = down) and horizontally.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dy": {"type": "integer", "description": "Vertical scroll clicks (positive up, negative down)", "default": 0},
                "dx": {"type": "integer", "description": "Horizontal scroll clicks (positive right, negative left)", "default": 0},
            },
        },
    },
    {
        "name": "computer_type_text",
        "description": "Types Unicode text into the currently active element as keystrokes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"}
            },
            "required": ["text"],
        },
    },
    {
        "name": "computer_key",
        "description": "Presses and releases a single key (e.g. 'Return', 'Escape', 'Tab', 'BackSpace', 'Space', 'Up', 'Down', etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name to press (e.g. 'Return', 'Escape', 'Tab')"}
            },
            "required": ["key"],
        },
    },
    {
        "name": "computer_hotkey",
        "description": "Presses and releases a key combination simultaneously (e.g. ['Command', 'c'] or ['Ctrl', 'Shift', 'p']).",
        "inputSchema": {
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
    },
    {
        "name": "computer_get_clipboard",
        "description": "Reads the current text contents from the system clipboard.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "computer_set_clipboard",
        "description": "Writes text contents to the system clipboard.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy to clipboard"}
            },
            "required": ["text"],
        },
    },
    {
        "name": "computer_wait",
        "description": "Pauses execution for a specified number of seconds (max 30s) to allow GUI animations or page loads to finish.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "Number of seconds to wait (0.1 to 30.0)"}
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "computer_accessibility_snapshot",
        "description": "Retrieves the accessibility / UI automation element tree for an active window or application.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window_id": {"type": "string", "description": "Optional window ID"}
            },
        },
    },
]


class ComputerUseMCPServer:
    """Stdio JSON-RPC 2.0 MCP server wrapping the canonical ComputerUseService."""

    def __init__(self, service: Optional[ComputerUseService] = None, use_mock: bool = False):
        self.service = service or ComputerUseService(use_mock=use_mock)

    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params") or {}

        if not method:
            return None

        # Notifications (no response)
        if method.startswith("notifications/") or req_id is None:
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS_DEFINITIONS,
                },
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments") or {}
            res = self.execute_tool(tool_name, args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": res.model_dump(),
            }

        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {},
            }

        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: '{method}'",
                },
            }

    def execute_tool(self, name: str, args: Dict[str, Any]) -> MCPCallToolResult:
        try:
            if name == "computer_environment":
                env = self.service.environment()
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(env.model_dump(), indent=2))]
                )

            elif name == "computer_get_displays":
                displays = self.service.get_displays(force_refresh=True)
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps([d.model_dump() for d in displays], indent=2))]
                )


            elif name == "computer_screenshot":
                region = None
                x = args.get("x")
                y = args.get("y")
                w = args.get("width")
                h = args.get("height")
                if x is not None and y is not None and w is not None and h is not None:
                    region = Region(x=int(x), y=int(y), width=int(w), height=int(h))

                res = self.service.screenshot(
                    display_id=args.get("display_id"),
                    region=region,
                    window_id=args.get("window_id"),
                    quality=args.get("quality"),
                    fmt=args.get("format", "png"),
                )

                return MCPCallToolResult(
                    content=[
                        MCPContentItem(type="text", text=res.text_summary),
                        MCPContentItem(
                            type="image",
                            data=res.base64_data,
                            mimeType=res.mime_type,
                        ),
                    ]
                )

            elif name == "computer_list_windows":
                wins = self.service.list_windows()
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps([w.model_dump() for w in wins], indent=2))]
                )


            elif name == "computer_focus_window":
                res = self.service.focus_window(window_id=str(args["window_id"]))
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_move_pointer":
                res = self.service.move_pointer(
                    x=int(args["x"]),
                    y=int(args["y"]),
                    duration=float(args.get("duration", 0.0)),
                )
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_click":
                btn = MouseButton(args.get("button", "left").lower())
                ctype = ClickType(args.get("click_type", "single").lower())
                res = self.service.mouse_click(
                    x=int(args["x"]),
                    y=int(args["y"]),
                    button=btn,
                    click_type=ctype,
                )
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_drag":
                btn = MouseButton(args.get("button", "left").lower())
                res = self.service.mouse_drag(
                    start_x=int(args["start_x"]),
                    start_y=int(args["start_y"]),
                    end_x=int(args["end_x"]),
                    end_y=int(args["end_y"]),
                    button=btn,
                    duration=float(args.get("duration", 0.5)),
                )
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_scroll":
                res = self.service.mouse_scroll(
                    dx=int(args.get("dx", 0)),
                    dy=int(args.get("dy", 0)),
                )
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_type_text":
                res = self.service.type_text(text=str(args["text"]))
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_key":
                res = self.service.press_key(key=str(args["key"]))
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_hotkey":
                res = self.service.hotkey(keys=[str(k) for k in args["keys"]])
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_get_clipboard":
                text = self.service.get_clipboard()
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=text)]
                )

            elif name == "computer_set_clipboard":
                res = self.service.set_clipboard(text=str(args["text"]))
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_wait":
                res = self.service.wait(seconds=float(args["seconds"]))
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(res, indent=2))]
                )

            elif name == "computer_accessibility_snapshot":
                tree = self.service.accessibility_snapshot(window_id=args.get("window_id"))
                return MCPCallToolResult(
                    content=[MCPContentItem(type="text", text=json.dumps(tree.model_dump() if tree else None, indent=2))]
                )


            else:
                return MCPCallToolResult(
                    isError=True,
                    content=[MCPContentItem(type="text", text=f"Unknown tool: '{name}'")],
                )

        except Exception as e:
            return MCPCallToolResult(
                isError=True,
                content=[MCPContentItem(type="text", text=f"Error executing {name}: {str(e)}")],
            )

    def run_stdio(self) -> None:
        """Runs the MCP server over standard input/output."""
        for line in sys.stdin:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                req = json.loads(line_str)
                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except Exception as e:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {str(e)}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


def main():
    use_mock = "--mock" in sys.argv
    server = ComputerUseMCPServer(use_mock=use_mock)
    server.run_stdio()


if __name__ == "__main__":
    main()
