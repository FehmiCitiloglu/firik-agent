"""Native macOS backend for Computer Use using CoreGraphics, Quartz, and AppKit."""
from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from firik_agent.computer_use.backends.base import ComputerBackend
from firik_agent.computer_use.models import (
    AccessibilityNode,
    ClickType,
    DisplayInfo,
    EnvironmentInfo,
    MouseButton,
    PermissionDeniedError,
    Region,
    WindowInfo,
)
from firik_agent.computer_use.permissions import PermissionChecker

# CoreGraphics & Carbon virtual keycodes for common keys
MAC_KEY_CODES: Dict[str, int] = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "backspace": 51,
    "delete": 117, "escape": 53, "esc": 53, "command": 55, "cmd": 55,
    "shift": 56, "capslock": 57, "option": 58, "alt": 58, "control": 59,
    "ctrl": 59, "rightshift": 60, "rightoption": 61, "rightcontrol": 62,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "pageup": 116, "pagedown": 121, "home": 115, "end": 119,
}


class MacOSBackend(ComputerBackend):
    """Production macOS backend supporting Retina, multi-display, and Cocoa event taps."""

    def __init__(self):
        self._quartz = None
        self._app_services = None
        try:
            self._quartz = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            self._app_services = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        except Exception:
            pass

    def detect_environment(self) -> EnvironmentInfo:
        perms = PermissionChecker.check_all()
        displays = self.get_displays()
        primary = next((d for d in displays if d.is_primary), displays[0] if displays else None)
        scale = primary.scale_factor if primary else 2.0

        return EnvironmentInfo(
            os="macOS",
            os_version=platform.mac_ver()[0] or platform.release(),
            desktop_environment="Aqua",
            display_server="quartz",
            architecture=platform.machine(),
            scale_factor=scale,
            permissions=perms,
            capabilities={
                "screenshot": "supported",
                "pointer_control": "supported" if perms.get("accessibility") == "granted" else "permission_required",
                "keyboard_control": "supported" if perms.get("accessibility") == "granted" else "permission_required",
                "window_management": "supported",
                "accessibility_tree": "supported",
                "clipboard": "supported",
            },
            displays=displays,
        )

    def get_displays(self) -> List[DisplayInfo]:
        displays: List[DisplayInfo] = []
        if self._quartz:
            try:
                max_displays = 32
                display_ids = (ctypes.c_uint32 * max_displays)()
                display_count = ctypes.c_uint32(0)

                # CGGetActiveDisplayList
                if self._quartz.CGGetActiveDisplayList(max_displays, display_ids, ctypes.byref(display_count)) == 0:
                    class CGRect(ctypes.Structure):
                        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double),
                                    ("width", ctypes.c_double), ("height", ctypes.c_double)]

                    self._quartz.CGDisplayBounds.restype = CGRect
                    self._quartz.CGDisplayBounds.argtypes = [ctypes.c_uint32]

                    for i in range(display_count.value):
                        did = display_ids[i]
                        bounds = self._quartz.CGDisplayBounds(did)
                        px_w = self._quartz.CGDisplayPixelsWide(did)
                        px_h = self._quartz.CGDisplayPixelsHigh(did)
                        scale = round(px_w / bounds.width, 1) if bounds.width > 0 else 1.0
                        is_main = bool(self._quartz.CGDisplayIsMain(did))

                        displays.append(DisplayInfo(
                            id=str(did),
                            name=f"Display {did}" + (" (Primary)" if is_main else ""),
                            x=int(bounds.x),
                            y=int(bounds.y),
                            width=int(bounds.width),
                            height=int(bounds.height),
                            scale_factor=scale,
                            is_primary=is_main,
                            pixel_width=px_w,
                            pixel_height=px_h,
                        ))
            except Exception:
                pass

        if not displays:
            # Fallback single display
            displays.append(DisplayInfo(
                id="main",
                name="Main Display",
                x=0,
                y=0,
                width=1440,
                height=900,
                scale_factor=2.0,
                is_primary=True,
                pixel_width=2880,
                pixel_height=1800,
            ))
        return displays

    def capture_screen(
        self,
        display_id: Optional[str] = None,
        region: Optional[Region] = None,
        window_id: Optional[str] = None,
    ) -> Tuple[bytes, float, int, int]:
        displays = self.get_displays()
        target_display = displays[0]
        if display_id:
            for d in displays:
                if d.id == display_id:
                    target_display = d
                    break

        scale = target_display.scale_factor
        origin_x = target_display.x
        origin_y = target_display.y

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            cmd = ["screencapture", "-x"]  # -x disables capture sound

            if window_id:
                # Capture window by window ID
                try:
                    wid_int = int(window_id)
                    cmd.extend(["-l", str(wid_int)])
                except ValueError:
                    pass
            elif region:
                origin_x = region.x
                origin_y = region.y
                cmd.extend(["-R", f"{region.x},{region.y},{region.width},{region.height}"])
            elif display_id:
                cmd.extend(["-D", str(target_display.id)])

            cmd.append(str(tmp_path))

            res = subprocess.run(cmd, capture_output=True, timeout=10)
            if res.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise PermissionDeniedError(
                    "Ekran görüntüsü alınamadı. Screen Recording izni verilmemiş olabilir.",
                    recovery_hint="Sistem Ayarları > Gizlilik ve Güvenlik > Ekran Kaydı iznini kontrol edin.",
                )

            data = tmp_path.read_bytes()
            return data, scale, origin_x, origin_y
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def list_windows(self) -> List[WindowInfo]:
        windows: List[WindowInfo] = []
        script = """
        tell application "System Events"
            set winList to {}
            set pList to (every process whose visible is true)
            repeat with p in pList
                try
                    set pName to name of p
                    repeat with w in (every window of p)
                        try
                            set wName to name of w
                            set wPos to position of w
                            set wSize to size of w
                            set end of winList to (pName & "|" & wName & "|" & (item 1 of wPos) & "|" & (item 2 of wPos) & "|" & (item 1 of wSize) & "|" & (item 2 of wSize))
                        end try
                    end repeat
                end try
            end repeat
            return winList
        end tell
        """
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                for idx, item in enumerate(res.stdout.strip().split(", ")):
                    parts = item.split("|")
                    if len(parts) >= 6:
                        p_name, w_name, x, y, w, h = parts[:6]
                        windows.append(WindowInfo(
                            id=f"win_{idx}",
                            title=w_name.strip() or p_name.strip(),
                            application=p_name.strip(),
                            x=int(x.strip() or 0),
                            y=int(y.strip() or 0),
                            width=int(w.strip() or 0),
                            height=int(h.strip() or 0),
                            is_focused=(idx == 0),
                        ))
        except Exception:
            pass
        return windows

    def focus_window(self, window_id: str) -> bool:
        # Search window or application name
        script = f"""
        tell application "{window_id}"
            activate
        end tell
        """
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
            return res.returncode == 0
        except Exception:
            return False

    def move_pointer(self, x: int, y: int, duration: float = 0.0) -> None:
        if self._quartz:
            class CGPoint(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            pt = CGPoint(float(x), float(y))
            # kCGEventMouseMoved = 5
            ev = self._quartz.CGEventCreateMouseEvent(None, 5, pt, 0)
            self._quartz.CGEventPost(0, ev)  # kCGHIDEventTap = 0
            if ev:
                self._quartz.CFRelease(ev)

    def mouse_click(
        self,
        x: int,
        y: int,
        button: MouseButton = MouseButton.LEFT,
        click_type: ClickType = ClickType.SINGLE,
    ) -> None:
        if not self._quartz:
            return

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        pt = CGPoint(float(x), float(y))

        # Event types: LeftDown=1, LeftUp=2, RightDown=3, RightUp=4
        if button == MouseButton.RIGHT:
            down_type, up_type = 3, 4
            cg_button = 1
        else:
            down_type, up_type = 1, 2
            cg_button = 0

        repeat_count = 1
        if click_type == ClickType.DOUBLE:
            repeat_count = 2
        elif click_type == ClickType.TRIPLE:
            repeat_count = 3

        for i in range(1, repeat_count + 1):
            down = self._quartz.CGEventCreateMouseEvent(None, down_type, pt, cg_button)
            # Set click state (1 for single, 2 for double)
            self._quartz.CGEventSetIntegerValueField(down, 1, i)
            self._quartz.CGEventPost(0, down)
            if down:
                self._quartz.CFRelease(down)

            time.sleep(0.05)

            up = self._quartz.CGEventCreateMouseEvent(None, up_type, pt, cg_button)
            self._quartz.CGEventSetIntegerValueField(up, 1, i)
            self._quartz.CGEventPost(0, up)
            if up:
                self._quartz.CFRelease(up)

            if i < repeat_count:
                time.sleep(0.08)

    def mouse_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: MouseButton = MouseButton.LEFT,
        duration: float = 0.5,
    ) -> None:
        if not self._quartz:
            return
        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        # Move to start
        self.move_pointer(start_x, start_y)
        time.sleep(0.05)

        # Mouse Down
        p_start = CGPoint(float(start_x), float(start_y))
        down = self._quartz.CGEventCreateMouseEvent(None, 1, p_start, 0)
        self._quartz.CGEventPost(0, down)
        if down:
            self._quartz.CFRelease(down)

        steps = max(5, int(duration * 20))
        for step in range(1, steps + 1):
            curr_x = start_x + (end_x - start_x) * (step / steps)
            curr_y = start_y + (end_y - start_y) * (step / steps)
            pt = CGPoint(float(curr_x), float(curr_y))
            # kCGEventLeftMouseDragged = 6
            drag_ev = self._quartz.CGEventCreateMouseEvent(None, 6, pt, 0)
            self._quartz.CGEventPost(0, drag_ev)
            if drag_ev:
                self._quartz.CFRelease(drag_ev)
            time.sleep(duration / steps)

        # Mouse Up
        p_end = CGPoint(float(end_x), float(end_y))
        up = self._quartz.CGEventCreateMouseEvent(None, 2, p_end, 0)
        self._quartz.CGEventPost(0, up)
        if up:
            self._quartz.CFRelease(up)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        if self._quartz:
            # CGEventCreateScrollWheelEvent2(source, units, wheelCount, wheel1, wheel2, wheel3)
            # units: 0 = pixel, 1 = line
            ev = self._quartz.CGEventCreateScrollWheelEvent2(None, 1, 2, dy, dx)
            self._quartz.CGEventPost(0, ev)
            if ev:
                self._quartz.CFRelease(ev)

    def type_text(self, text: str) -> None:
        # Safe Unicode typing via osascript System Events keystroke
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)

    def press_key(self, key: str) -> None:
        key_lower = key.lower()
        code = MAC_KEY_CODES.get(key_lower)
        if code is not None and self._quartz:
            down = self._quartz.CGEventCreateKeyboardEvent(None, code, True)
            self._quartz.CGEventPost(0, down)
            if down:
                self._quartz.CFRelease(down)

            time.sleep(0.02)

            up = self._quartz.CGEventCreateKeyboardEvent(None, code, False)
            self._quartz.CGEventPost(0, up)
            if up:
                self._quartz.CFRelease(up)
        else:
            # Fallback to key code or keystroke
            script = f'tell application "System Events" to key code {code if code else 36}'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=2)

    def hotkey(self, keys: List[str]) -> None:
        # AppleScript combination handler
        # e.g. keys: ["cmd", "c"] or ["command", "shift", "p"]
        mods = []
        main_key = ""
        for k in keys:
            kl = k.lower()
            if kl in ("cmd", "command"):
                mods.append("command down")
            elif kl in ("ctrl", "control"):
                mods.append("control down")
            elif kl in ("alt", "option"):
                mods.append("option down")
            elif kl == "shift":
                mods.append("shift down")
            else:
                main_key = k

        if not main_key:
            return

        using_clause = f" using {{{', '.join(mods)}}}" if mods else ""
        code = MAC_KEY_CODES.get(main_key.lower())
        if code:
            script = f'tell application "System Events" to key code {code}{using_clause}'
        else:
            script = f'tell application "System Events" to keystroke "{main_key}"{using_clause}'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)

    def get_clipboard(self) -> str:
        try:
            res = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""

    def set_clipboard(self, text: str) -> None:
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(input=text.encode("utf-8"), timeout=2)
        except Exception:
            pass

    def get_accessibility_tree(self, window_id: Optional[str] = None) -> Optional[AccessibilityNode]:
        # Basic accessible tree of frontmost app
        script = """
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            set wList to {}
            try
                set frontWin to front window of frontApp
                set wName to name of frontWin
                set btnList to {}
                repeat with b in (every button of frontWin)
                    try
                        set end of btnList to name of b
                    end try
                end repeat
                return appName & "|" & wName & "|" & (btnList as string)
            end try
            return appName
        end tell
        """
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("|")
                app_name = parts[0]
                win_name = parts[1] if len(parts) > 1 else ""
                buttons = parts[2].split(", ") if len(parts) > 2 else []
                children = [
                    AccessibilityNode(role="AXButton", name=b.strip(), label=b.strip())
                    for b in buttons if b.strip()
                ]
                return AccessibilityNode(
                    role="AXWindow",
                    name=win_name or app_name,
                    label=app_name,
                    children=children,
                )
        except Exception:
            pass
        return None
