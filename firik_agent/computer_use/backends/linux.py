"""Linux platform backend for Computer Use supporting X11 and Wayland detection."""
from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from firik_agent.computer_use.backends.base import ComputerBackend
from firik_agent.computer_use.models import (
    AccessibilityNode,
    CapabilityUnavailableError,
    ClickType,
    ComputerUseError,
    DisplayInfo,
    EnvironmentInfo,
    MouseButton,
    Point,
    Region,
    ScrollDirection,
    WindowInfo,
)
from firik_agent.computer_use.permissions import PermissionChecker



class LinuxComputerBackend(ComputerBackend):
    """Linux backend with X11 and Wayland detection and graceful fallback handling."""

    def __init__(self) -> None:
        self._session_type = self._detect_session_type()
        self._has_xdotool = shutil.which("xdotool") is not None
        self._has_wmctrl = shutil.which("wmctrl") is not None
        self._has_xclip = shutil.which("xclip") is not None
        self._has_wl_clipboard = shutil.which("wl-copy") is not None and shutil.which("wl-paste") is not None
        self._has_grim = shutil.which("grim") is not None
        self._has_scrot = shutil.which("scrot") is not None
        self._has_ydotool = shutil.which("ydotool") is not None

    def _detect_session_type(self) -> str:
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session:
            return session
        if os.environ.get("WAYLAND_DISPLAY"):
            return "wayland"
        if os.environ.get("DISPLAY"):
            return "x11"
        return "unknown"

    def detect_environment(self) -> EnvironmentInfo:
        desktop_env = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "unknown"
        perms = PermissionChecker.check_linux_permissions()

        caps = {
            "screenshot": True,
            "pointer_control": self._session_type == "x11" or self._has_ydotool,
            "keyboard_control": self._session_type == "x11" or self._has_ydotool,
            "window_management": self._session_type == "x11" and (self._has_wmctrl or self._has_xdotool),
            "accessibility_tree": False,
            "clipboard": self._has_xclip or self._has_wl_clipboard,
        }

        return EnvironmentInfo(
            os="linux",
            os_version=platform.release(),
            desktop_environment=desktop_env,
            display_server=self._session_type,
            architecture=platform.machine(),
            scale_factor=1.0,
            permissions=perms,
            capabilities=caps,
            details={
                "session_type": self._session_type,
                "has_xdotool": self._has_xdotool,
                "has_wmctrl": self._has_wmctrl,
                "has_xclip": self._has_xclip,
                "has_wl_clipboard": self._has_wl_clipboard,
                "has_grim": self._has_grim,
                "has_scrot": self._has_scrot,
                "has_ydotool": self._has_ydotool,
            },
        )

    def get_displays(self) -> List[DisplayInfo]:
        displays: List[DisplayInfo] = []

        # Attempt to parse xrandr if in X11
        if self._session_type == "x11" and shutil.which("xrandr"):
            try:
                proc = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=3)
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        if " connected " in line:
                            parts = line.split()
                            name = parts[0]
                            is_primary = "primary" in parts
                            # find geometry token like 1920x1080+0+0
                            for token in parts:
                                if "+" in token and "x" in token:
                                    try:
                                        dim_part, x_part, y_part = token.split("+")
                                        w_part, h_part = dim_part.split("x")
                                        displays.append(
                                            DisplayInfo(
                                                id=name,
                                                name=name,
                                                x=int(x_part),
                                                y=int(y_part),
                                                width=int(w_part),
                                                height=int(h_part),
                                                scale_factor=1.0,
                                                is_primary=is_primary or len(displays) == 0,
                                            )
                                        )
                                    except Exception:
                                        continue
            except Exception:
                pass

        if displays:
            return displays

        # Fallback to Pillow ImageGrab virtual size
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            return [
                DisplayInfo(
                    id="primary",
                    name="Default Display",
                    x=0,
                    y=0,
                    width=img.width,
                    height=img.height,
                    scale_factor=1.0,
                    is_primary=True,
                )
            ]
        except Exception:
            return [
                DisplayInfo(
                    id="primary",
                    name="Default Display",
                    x=0,
                    y=0,
                    width=1920,
                    height=1080,
                    scale_factor=1.0,
                    is_primary=True,
                )
            ]

    def capture_screen(
        self,
        display_id: Optional[str] = None,
        region: Optional[Region] = None,
        window_id: Optional[str] = None,
    ) -> Tuple[bytes, float, int, int]:
        from PIL import Image

        # 1. Wayland grim capture if available
        if self._session_type == "wayland" and self._has_grim:
            cmd = ["grim"]
            if region:
                cmd.extend(["-g", f"{region.x},{region.y} {region.width}x{region.height}"])
            cmd.append("-")  # stdout
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=5)
                if proc.returncode == 0 and len(proc.stdout) > 0:
                    ox = region.x if region else 0
                    oy = region.y if region else 0
                    return (proc.stdout, 1.0, ox, oy)
            except Exception:
                pass

        # 2. X11 / scrot capture
        if self._has_scrot and (region or display_id):
            # scrot can capture into stdout or tempfile
            pass

        # 3. Pillow ImageGrab fallback
        try:
            from PIL import ImageGrab
            bbox = None
            if region:
                bbox = (region.x, region.y, region.x + region.width, region.y + region.height)
            img = ImageGrab.grab(bbox=bbox)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            ox = region.x if region else 0
            oy = region.y if region else 0
            return (buf.getvalue(), 1.0, ox, oy)
        except Exception as e:
            raise ComputerUseError(f"Screen capture failed on Linux ({self._session_type}): {e}") from e

    def list_windows(self) -> List[WindowInfo]:
        if self._session_type == "wayland":
            return []  # Wayland security isolates windows from enumeration

        windows: List[WindowInfo] = []
        if self._has_wmctrl:
            try:
                # wmctrl -l -G -p -x gives id, desktop, pid, x, y, w, h, class, client, title
                proc = subprocess.run(["wmctrl", "-l", "-G", "-p", "-x"], capture_output=True, text=True, timeout=3)
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        parts = line.split(maxsplit=8)
                        if len(parts) >= 9:
                            win_id = parts[0]
                            pid = int(parts[2]) if parts[2].isdigit() else None
                            x = int(parts[3])
                            y = int(parts[4])
                            w = int(parts[5])
                            h = int(parts[6])
                            app_name = parts[7]
                            title = parts[8]
                            windows.append(
                                WindowInfo(
                                    id=win_id,
                                    title=title,
                                    app_name=app_name,
                                    pid=pid,
                                    x=x,
                                    y=y,
                                    width=w,
                                    height=h,
                                    is_minimized=False,
                                    is_focused=False,
                                )
                            )
                    return windows
            except Exception:
                pass
        return windows

    def focus_window(self, window_id: str) -> bool:
        if self._session_type == "wayland":
            raise CapabilityUnavailableError("Window focusing is not supported by standard Wayland protocols.")

        if self._has_wmctrl:
            res = subprocess.run(["wmctrl", "-ia", window_id], capture_output=True, timeout=3)
            return res.returncode == 0
        if self._has_xdotool:
            res = subprocess.run(["xdotool", "windowactivate", window_id], capture_output=True, timeout=3)
            return res.returncode == 0
        return False

    def move_pointer(self, x: int, y: int, duration: float = 0.0) -> None:
        if self._session_type == "wayland":
            if self._has_ydotool:
                subprocess.run(["ydotool", "mousemove", "--absolute", str(x), str(y)], check=True, timeout=3)
                return
            raise CapabilityUnavailableError("Pointer control is restricted under Wayland without ydotool or portal support.")

        if self._has_xdotool:
            subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=True, timeout=3)
        else:
            raise CapabilityUnavailableError("xdotool is required for mouse control under X11.")

    def mouse_click(
        self,
        x: int,
        y: int,
        button: MouseButton = MouseButton.LEFT,
        click_type: ClickType = ClickType.SINGLE,
    ) -> None:
        self.move_pointer(x, y)
        time.sleep(0.05)

        btn_num = 1
        if button == MouseButton.RIGHT:
            btn_num = 3
        elif button == MouseButton.MIDDLE:
            btn_num = 2

        if self._session_type == "wayland":
            if self._has_ydotool:
                repeat = 2 if click_type == ClickType.DOUBLE else (3 if click_type == ClickType.TRIPLE else 1)
                for _ in range(repeat):
                    subprocess.run(["ydotool", "click", f"0x11{btn_num - 1}"], timeout=2)
                    time.sleep(0.05)
                return
            raise CapabilityUnavailableError("Mouse click is restricted under Wayland without ydotool.")

        if self._has_xdotool:
            cmd = ["xdotool", "click"]
            if click_type == ClickType.DOUBLE:
                cmd.extend(["--repeat", "2", "--delay", "50"])
            elif click_type == ClickType.TRIPLE:
                cmd.extend(["--repeat", "3", "--delay", "50"])
            cmd.append(str(btn_num))
            subprocess.run(cmd, check=True, timeout=3)
        else:
            raise CapabilityUnavailableError("xdotool is required for mouse clicking under X11.")

    def mouse_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: MouseButton = MouseButton.LEFT,
        duration: float = 0.5,
    ) -> None:
        btn_num = 1 if button == MouseButton.LEFT else (3 if button == MouseButton.RIGHT else 2)
        if self._session_type == "wayland":
            raise CapabilityUnavailableError("Mouse dragging is restricted under Wayland compositors.")

        if self._has_xdotool:
            subprocess.run(["xdotool", "mousemove", str(start_x), str(start_y)], check=True, timeout=3)
            time.sleep(0.05)
            subprocess.run(["xdotool", "mousedown", str(btn_num)], check=True, timeout=3)
            time.sleep(duration)
            subprocess.run(["xdotool", "mousemove", str(end_x), str(end_y)], check=True, timeout=3)
            time.sleep(0.05)
            subprocess.run(["xdotool", "mouseup", str(btn_num)], check=True, timeout=3)
        else:
            raise CapabilityUnavailableError("xdotool is required for mouse drag under X11.")

    def mouse_scroll(self, dx: int, dy: int) -> None:
        if self._session_type == "wayland":
            raise CapabilityUnavailableError("Mouse scroll is restricted under Wayland.")

        if self._has_xdotool:
            # Button 4 = scroll up, 5 = scroll down, 6 = left, 7 = right
            if dy > 0:
                for _ in range(abs(dy)):
                    subprocess.run(["xdotool", "click", "4"], timeout=1)
            elif dy < 0:
                for _ in range(abs(dy)):
                    subprocess.run(["xdotool", "click", "5"], timeout=1)

            if dx > 0:
                for _ in range(abs(dx)):
                    subprocess.run(["xdotool", "click", "7"], timeout=1)
            elif dx < 0:
                for _ in range(abs(dx)):
                    subprocess.run(["xdotool", "click", "6"], timeout=1)
        else:
            raise CapabilityUnavailableError("xdotool is required for scrolling under X11.")

    def type_text(self, text: str) -> None:
        if self._session_type == "wayland":
            if self._has_ydotool:
                subprocess.run(["ydotool", "type", text], check=True, timeout=5)
                return
            raise CapabilityUnavailableError("Text typing is restricted under Wayland without ydotool.")

        if self._has_xdotool:
            subprocess.run(["xdotool", "type", "--delay", "12", text], check=True, timeout=10)
        else:
            raise CapabilityUnavailableError("xdotool is required for typing text under X11.")

    def press_key(self, key: str) -> None:
        if self._session_type == "wayland":
            raise CapabilityUnavailableError("Key press is restricted under Wayland without ydotool.")

        if self._has_xdotool:
            # Map common names to X11 keysyms
            key_map = {
                "enter": "Return",
                "return": "Return",
                "backspace": "BackSpace",
                "tab": "Tab",
                "esc": "Escape",
                "escape": "Escape",
                "space": "space",
                "delete": "Delete",
                "up": "Up",
                "down": "Down",
                "left": "Left",
                "right": "Right",
            }
            xkey = key_map.get(key.lower(), key)
            subprocess.run(["xdotool", "key", xkey], check=True, timeout=3)
        else:
            raise CapabilityUnavailableError("xdotool is required for pressing keys under X11.")

    def hotkey(self, keys: List[str]) -> None:
        if self._session_type == "wayland":
            raise CapabilityUnavailableError("Hotkeys are restricted under Wayland without ydotool.")

        if self._has_xdotool:
            mod_map = {
                "ctrl": "ctrl",
                "control": "ctrl",
                "alt": "alt",
                "shift": "shift",
                "super": "super",
                "command": "super",
                "cmd": "super",
                "meta": "super",
            }
            mapped = [mod_map.get(k.lower(), k) for k in keys]
            combo = "+".join(mapped)
            subprocess.run(["xdotool", "key", combo], check=True, timeout=3)
        else:
            raise CapabilityUnavailableError("xdotool is required for hotkeys under X11.")

    def get_clipboard(self) -> str:
        if self._session_type == "wayland" and self._has_wl_clipboard:
            proc = subprocess.run(["wl-paste"], capture_output=True, text=True, timeout=2)
            if proc.returncode == 0:
                return proc.stdout
        if self._has_xclip:
            proc = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=2)
            if proc.returncode == 0:
                return proc.stdout
        return ""

    def set_clipboard(self, text: str) -> None:
        if self._session_type == "wayland" and self._has_wl_clipboard:
            subprocess.run(["wl-copy"], input=text, text=True, timeout=2)
            return
        if self._has_xclip:
            subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text, text=True, timeout=2)

    def get_accessibility_tree(self, window_id: Optional[str] = None) -> Optional[AccessibilityNode]:
        # Accessibility inspection in Linux typically uses AT-SPI2 / DBus
        return None


LinuxBackend = LinuxComputerBackend

