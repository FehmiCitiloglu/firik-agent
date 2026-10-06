"""Canonical ComputerUseService unifying backends, coordinates, security, and audit logging."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from firik_agent.computer_use.backends import get_backend
from firik_agent.computer_use.backends.base import ComputerBackend
from firik_agent.computer_use.coordinates import CoordinateManager
from firik_agent.computer_use.models import (
    AccessibilityNode,
    ClickType,
    DisplayInfo,
    EnvironmentInfo,
    MouseButton,
    Point,
    Region,
    ScreenshotResult,
    WindowInfo,
)
from firik_agent.computer_use.screenshots import ScreenshotProcessor
from firik_agent.computer_use.security import (
    ActionBlockedError,
    ComputerUseConfig,
    SecurityManager,
    UserCancelledError,
)

logger = logging.getLogger(__name__)


class ComputerUseService:
    """Canonical service managing cross-platform computer use operations.

    Both the native agent tools and the standalone MCP server delegate directly
    to this service to ensure uniform security, coordinate validation, and audit tracking.
    """

    def __init__(
        self,
        backend: Optional[ComputerBackend] = None,
        config: Optional[ComputerUseConfig] = None,
        use_mock: bool = False,
    ) -> None:
        self.config = config or ComputerUseConfig()
        self.backend = backend or get_backend(use_mock=use_mock)
        self.security = SecurityManager(self.config)
        self.coordinates = CoordinateManager()
        self._cached_displays: Optional[List[DisplayInfo]] = None
        self._last_display_refresh = 0.0

    # --- Emergency Stop & Configuration ---

    def stop(self) -> None:
        """Emergency stop: halts all pending and future desktop actions immediately."""
        self.security.request_stop()

    def reset_stop(self) -> None:
        """Resets the emergency stop state."""
        self.security.reset_stop()

    def is_stopped(self) -> bool:
        """Returns True if an emergency stop has been requested."""
        return self.security.is_stopped()

    # --- Core Operations ---

    def environment(self) -> EnvironmentInfo:
        """Discovers OS details, display server, permissions, and supported capabilities."""
        self.security.check_stop()
        env = self.backend.detect_environment()
        displays = self.get_displays()
        env.displays = displays
        return env

    def get_displays(self, force_refresh: bool = False) -> List[DisplayInfo]:
        """Discovers all connected physical/logical displays with bounds and scale factors."""
        self.security.check_stop()
        now = time.time()
        if self._cached_displays is None or force_refresh or (now - self._last_display_refresh > 5.0):
            self._cached_displays = self.backend.get_displays()
            self._last_display_refresh = now
        return self._cached_displays

    def screenshot(
        self,
        display_id: Optional[str] = None,
        region: Optional[Region] = None,
        window_id: Optional[str] = None,
        max_width: Optional[int] = None,
        max_height: Optional[int] = None,
        quality: Optional[int] = None,
        fmt: str = "png",
    ) -> ScreenshotResult:
        """Captures a screenshot with automatic Retina/DPI scaling and multimodal encoding."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_screenshots:
            raise ActionBlockedError("Screenshot capture is disabled by security policy.")

        raw_bytes, scale_factor, ox, oy = self.backend.capture_screen(
            display_id=display_id,
            region=region,
            window_id=window_id,
        )

        res = ScreenshotProcessor.process_image(
            raw_bytes=raw_bytes,
            scale_factor=scale_factor,
            origin_x=ox,
            origin_y=oy,
            display_id=display_id,
            window_id=window_id,
            fmt=fmt,
            quality=quality,
            max_width=max_width,
            max_height=max_height,
        )

        self.security.log_action(
            action="screenshot",
            details={
                "display_id": display_id,
                "window_id": window_id,
                "region": region.model_dump() if region else None,
                "dimensions": f"{res.metadata.logical_width}x{res.metadata.logical_height}",
            },
            status="success",
            duration=time.time() - t0,
        )
        return res

    def list_windows(self) -> List[WindowInfo]:
        """Lists active windows with titles, coordinates, and application names."""
        self.security.check_stop()
        self.security.check_rate_limit()
        return self.backend.list_windows()

    def focus_window(self, window_id: str) -> Dict[str, Any]:
        """Brings the specified window to the foreground."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_window_control:
            raise ActionBlockedError("Window management is disabled by security policy.")

        # Check if the target window belongs to a blocked app
        windows = self.backend.list_windows()
        target_win = next((w for w in windows if w.id == window_id), None)
        if target_win and self.security.is_app_blocked(target_win.application):
            raise ActionBlockedError(
                f"Window '{target_win.title}' belongs to blocked application '{target_win.application}'."
            )

        success = self.backend.focus_window(window_id)
        self.security.log_action(
            action="focus_window",
            details={"window_id": window_id, "app": target_win.application if target_win else "unknown"},
            status="success" if success else "failed",
            duration=time.time() - t0,
        )
        return {"success": success, "window_id": window_id}

    def move_pointer(self, x: int, y: int, duration: float = 0.0) -> Dict[str, Any]:
        """Moves pointer to logical coordinates (x, y) with boundary validation."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_pointer:
            raise ActionBlockedError("Pointer control is disabled by security policy.")

        displays = self.get_displays()
        pt = self.coordinates.validate_and_clamp(displays, x, y, strict=False)

        self.backend.move_pointer(pt.x, pt.y, duration=duration)
        self.security.log_action(
            action="move_pointer",
            details={"x": pt.x, "y": pt.y, "duration": duration},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "x": pt.x, "y": pt.y}

    def mouse_click(
        self,
        x: int,
        y: int,
        button: MouseButton = MouseButton.LEFT,
        click_type: ClickType = ClickType.SINGLE,
    ) -> Dict[str, Any]:
        """Performs a mouse click at logical coordinates (x, y)."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_pointer:
            raise ActionBlockedError("Pointer control is disabled by security policy.")

        displays = self.get_displays()
        pt = self.coordinates.validate_and_clamp(displays, x, y, strict=False)

        self.backend.mouse_click(pt.x, pt.y, button=button, click_type=click_type)
        self.security.log_action(
            action="mouse_click",
            details={"x": pt.x, "y": pt.y, "button": button.value, "click_type": click_type.value},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "x": pt.x, "y": pt.y, "button": button.value, "click_type": click_type.value}

    def mouse_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: MouseButton = MouseButton.LEFT,
        duration: float = 0.5,
    ) -> Dict[str, Any]:
        """Drags the cursor from start coordinates to end coordinates."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_pointer:
            raise ActionBlockedError("Pointer control is disabled by security policy.")

        displays = self.get_displays()
        start_pt = self.coordinates.validate_and_clamp(displays, start_x, start_y, strict=False)
        end_pt = self.coordinates.validate_and_clamp(displays, end_x, end_y, strict=False)

        self.backend.mouse_drag(
            start_x=start_pt.x,
            start_y=start_pt.y,
            end_x=end_pt.x,
            end_y=end_pt.y,
            button=button,
            duration=duration,
        )
        self.security.log_action(
            action="mouse_drag",
            details={
                "start": {"x": start_pt.x, "y": start_pt.y},
                "end": {"x": end_pt.x, "y": end_pt.y},
                "button": button.value,
                "duration": duration,
            },
            status="success",
            duration=time.time() - t0,
        )
        return {
            "success": True,
            "start": {"x": start_pt.x, "y": start_pt.y},
            "end": {"x": end_pt.x, "y": end_pt.y},
        }

    def mouse_scroll(self, dx: int, dy: int) -> Dict[str, Any]:
        """Scrolls vertically (dy) and horizontally (dx)."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_pointer:
            raise ActionBlockedError("Pointer control is disabled by security policy.")

        self.backend.mouse_scroll(dx=dx, dy=dy)
        self.security.log_action(
            action="mouse_scroll",
            details={"dx": dx, "dy": dy},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "dx": dx, "dy": dy}

    def type_text(self, text: str) -> Dict[str, Any]:
        """Types Unicode text into the currently focused control."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_keyboard:
            raise ActionBlockedError("Keyboard control is disabled by security policy.")

        # Check for potentially destructive or dangerous text
        if self.security.is_text_destructive(text):
            logger.warning("Potentially destructive text entry detected: %s", text[:30])

        self.backend.type_text(text)
        self.security.log_action(
            action="type_text",
            details={"length": len(text), "preview": text[:20]},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "chars_typed": len(text)}

    def press_key(self, key: str) -> Dict[str, Any]:
        """Presses and releases a single key."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_keyboard:
            raise ActionBlockedError("Keyboard control is disabled by security policy.")

        self.backend.press_key(key)
        self.security.log_action(
            action="press_key",
            details={"key": key},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "key": key}

    def hotkey(self, keys: List[str]) -> Dict[str, Any]:
        """Presses a combination of keys together."""
        t0 = time.time()
        self.security.check_stop()
        self.security.check_rate_limit()

        if not self.config.allow_keyboard:
            raise ActionBlockedError("Keyboard control is disabled by security policy.")

        self.backend.hotkey(keys)
        self.security.log_action(
            action="hotkey",
            details={"keys": keys},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "keys": keys}

    def get_clipboard(self) -> str:
        """Retrieves text from the system clipboard."""
        t0 = time.time()
        self.security.check_stop()

        if not self.config.allow_clipboard:
            raise ActionBlockedError("Clipboard access is disabled by security policy.")

        content = self.backend.get_clipboard()
        self.security.log_action(
            action="get_clipboard",
            details={"content_length": len(content)},
            status="success",
            duration=time.time() - t0,
        )
        return content

    def set_clipboard(self, text: str) -> Dict[str, Any]:
        """Sets text on the system clipboard."""
        t0 = time.time()
        self.security.check_stop()

        if not self.config.allow_clipboard:
            raise ActionBlockedError("Clipboard access is disabled by security policy.")

        self.backend.set_clipboard(text)
        self.security.log_action(
            action="set_clipboard",
            details={"content_length": len(text)},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "length": len(text)}

    def wait(self, seconds: float) -> Dict[str, Any]:
        """Pauses execution for a brief duration (clamped to max 30s) while monitoring stop signals."""
        t0 = time.time()
        self.security.check_stop()

        sleep_duration = max(0.0, min(float(seconds), 30.0))
        interval = 0.1
        elapsed = 0.0
        while elapsed < sleep_duration:
            self.security.check_stop()
            chunk = min(interval, sleep_duration - elapsed)
            time.sleep(chunk)
            elapsed += chunk

        self.security.log_action(
            action="wait",
            details={"requested_seconds": seconds, "slept_seconds": elapsed},
            status="success",
            duration=time.time() - t0,
        )
        return {"success": True, "slept": elapsed}

    def accessibility_snapshot(self, window_id: Optional[str] = None) -> Optional[AccessibilityNode]:
        """Retrieves accessibility / UI element tree for the focused window."""
        t0 = time.time()
        self.security.check_stop()

        if not self.config.allow_accessibility:
            raise ActionBlockedError("Accessibility inspection is disabled by security policy.")

        tree = self.backend.get_accessibility_tree(window_id=window_id)
        self.security.log_action(
            action="accessibility_snapshot",
            details={"window_id": window_id, "found": tree is not None},
            status="success",
            duration=time.time() - t0,
        )
        return tree
