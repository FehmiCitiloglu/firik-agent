"""In-memory mock ComputerBackend for unit tests and headless CI environments."""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw

from firik_agent.computer_use.backends.base import ComputerBackend
from firik_agent.computer_use.models import (
    AccessibilityNode,
    ClickType,
    DisplayInfo,
    EnvironmentInfo,
    MouseButton,
    Region,
    WindowInfo,
)


class MockComputerBackend(ComputerBackend):
    """Fully functional mock backend that records actions without touching real hardware."""

    def __init__(self, displays: Optional[List[DisplayInfo]] = None):
        self.displays = displays or [
            DisplayInfo(
                id="display_1",
                name="Mock Primary Display",
                x=0,
                y=0,
                width=1920,
                height=1080,
                scale_factor=2.0,
                is_primary=True,
                pixel_width=3840,
                pixel_height=2160,
            ),
            DisplayInfo(
                id="display_2",
                name="Mock Secondary Display",
                x=1920,
                y=0,
                width=1920,
                height=1080,
                scale_factor=1.0,
                is_primary=False,
                pixel_width=1920,
                pixel_height=1080,
            ),
        ]
        self.windows = [
            WindowInfo(
                id="win_1",
                title="Mock Web Browser - Firik Agent",
                application="Browser",
                x=100,
                y=100,
                width=1200,
                height=800,
                is_focused=True,
                is_minimized=False,
                is_visible=True,
            ),
            WindowInfo(
                id="win_2",
                title="Code Editor",
                application="Code",
                x=200,
                y=200,
                width=1000,
                height=700,
                is_focused=False,
                is_minimized=False,
                is_visible=True,
            ),
        ]
        self.cursor_x = 0
        self.cursor_y = 0
        self.clipboard = "mock clipboard text"
        self.history: List[Dict[str, Any]] = []

    @property
    def action_history(self) -> List[Dict[str, Any]]:
        return self.history

    def detect_environment(self) -> EnvironmentInfo:

        return EnvironmentInfo(
            os="MockOS",
            os_version="1.0.0",
            desktop_environment="MockDesktop",
            display_server="mock",
            architecture="x86_64",
            scale_factor=2.0,
            permissions={"screen_recording": "granted", "accessibility": "granted"},
            capabilities={
                "screenshot": "supported",
                "pointer_control": "supported",
                "keyboard_control": "supported",
                "window_management": "supported",
                "accessibility_tree": "supported",
                "clipboard": "supported",
            },
            displays=self.displays,
        )

    def get_displays(self) -> List[DisplayInfo]:
        return list(self.displays)

    def capture_screen(
        self,
        display_id: Optional[str] = None,
        region: Optional[Region] = None,
        window_id: Optional[str] = None,
    ) -> Tuple[bytes, float, int, int]:
        target_display = self.displays[0]
        if display_id:
            for d in self.displays:
                if d.id == display_id:
                    target_display = d
                    break

        scale = target_display.scale_factor
        w = region.width if region else target_display.width
        h = region.height if region else target_display.height
        ox = region.x if region else target_display.x
        oy = region.y if region else target_display.y

        # Generate test synthetic image
        pw = int(w * scale)
        ph = int(h * scale)
        img = Image.new("RGB", (max(10, pw), max(10, ph)), color=(24, 32, 47))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, pw - 10, ph - 10], outline=(56, 189, 248), width=3)
        draw.text((20, 20), f"Mock Screen {target_display.name} [{w}x{h}]", fill=(255, 255, 255))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self.history.append({"action": "screenshot", "display": target_display.id, "region": region})
        return buf.getvalue(), scale, ox, oy

    def list_windows(self) -> List[WindowInfo]:
        return list(self.windows)

    def focus_window(self, window_id: str) -> bool:
        for w in self.windows:
            w.is_focused = (w.id == window_id or w.title.lower() == window_id.lower() or w.application.lower() == window_id.lower())
        self.history.append({"action": "focus_window", "window_id": window_id})
        return any(w.is_focused for w in self.windows)

    def move_pointer(self, x: int, y: int, duration: float = 0.0) -> None:
        self.cursor_x = x
        self.cursor_y = y
        self.history.append({"action": "move_pointer", "x": x, "y": y, "duration": duration})

    def mouse_click(
        self,
        x: int,
        y: int,
        button: MouseButton = MouseButton.LEFT,
        click_type: ClickType = ClickType.SINGLE,
    ) -> None:
        self.cursor_x = x
        self.cursor_y = y
        self.history.append({
            "action": "mouse_click",
            "x": x,
            "y": y,
            "button": button.value,
            "click_type": click_type.value,
        })

    def mouse_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: MouseButton = MouseButton.LEFT,
        duration: float = 0.5,
    ) -> None:
        self.cursor_x = end_x
        self.cursor_y = end_y
        self.history.append({
            "action": "mouse_drag",
            "start_x": start_x,
            "start_y": start_y,
            "end_x": end_x,
            "end_y": end_y,
            "button": button.value,
            "duration": duration,
        })

    def mouse_scroll(self, dx: int, dy: int) -> None:
        self.history.append({"action": "mouse_scroll", "dx": dx, "dy": dy})

    def type_text(self, text: str) -> None:
        self.history.append({"action": "type_text", "text": text})

    def press_key(self, key: str) -> None:
        self.history.append({"action": "press_key", "key": key})

    def hotkey(self, keys: List[str]) -> None:
        self.history.append({"action": "hotkey", "keys": list(keys)})

    def get_clipboard(self) -> str:
        self.history.append({"action": "get_clipboard"})
        return self.clipboard

    def set_clipboard(self, text: str) -> None:
        self.clipboard = text
        self.history.append({"action": "set_clipboard", "text": text})

    def get_accessibility_tree(self, window_id: Optional[str] = None) -> Optional[AccessibilityNode]:
        self.history.append({"action": "get_accessibility_tree", "window_id": window_id})
        return AccessibilityNode(
            role="AXWindow",
            name="Mock Application Window",
            label="Main Window",
            value="",
            enabled=True,
            focused=True,
            bounds={"x": 100, "y": 100, "width": 1200, "height": 800},
            children=[
                AccessibilityNode(
                    role="AXButton",
                    name="Submit",
                    label="Submit Button",
                    bounds={"x": 150, "y": 200, "width": 100, "height": 30},
                ),
                AccessibilityNode(
                    role="AXTextField",
                    name="Username",
                    label="Username Field",
                    bounds={"x": 150, "y": 250, "width": 200, "height": 30},
                ),
            ],
        )


MockBackend = MockComputerBackend
