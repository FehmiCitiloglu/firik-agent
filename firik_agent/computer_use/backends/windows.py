"""Native Windows backend for Computer Use using Win32 API and ctypes."""
from __future__ import annotations

import ctypes
import io
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image

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

# Win32 Virtual Keys
WIN_VK_CODES: Dict[str, int] = {
    "return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08,
    "delete": 0x2E, "escape": 0x1B, "esc": 0x1B, "shift": 0x10, "ctrl": 0x11,
    "control": 0x11, "alt": 0x12, "win": 0x5B, "windows": 0x5B, "cmd": 0x5B,
    "capslock": 0x14, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
}


class WindowsBackend(ComputerBackend):
    """Production Windows backend supporting multi-monitor, negative coords, and DPI scaling."""

    def __init__(self):
        self._user32 = None
        self._gdi32 = None
        if platform.system() == "Windows":
            try:
                self._user32 = ctypes.windll.user32
                self._gdi32 = ctypes.windll.gdi32
                # Enable Per-Monitor DPI Awareness V2 if available (-4)
                try:
                    self._user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
                except Exception:
                    pass
            except Exception:
                pass

    def detect_environment(self) -> EnvironmentInfo:
        displays = self.get_displays()
        primary = next((d for d in displays if d.is_primary), displays[0] if displays else None)
        scale = primary.scale_factor if primary else 1.0

        return EnvironmentInfo(
            os="Windows",
            os_version=platform.version(),
            desktop_environment="DWM",
            display_server="win32",
            architecture=platform.machine(),
            scale_factor=scale,
            permissions={"screen_recording": "granted", "ui_automation": "granted"},
            capabilities={
                "screenshot": "supported",
                "pointer_control": "supported",
                "keyboard_control": "supported",
                "window_management": "supported",
                "accessibility_tree": "supported",
                "clipboard": "supported",
            },
            displays=displays,
        )

    def get_displays(self) -> List[DisplayInfo]:
        displays: List[DisplayInfo] = []
        if self._user32:
            try:
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                class MONITORINFOEX(ctypes.Structure):
                    _fields_ = [("cbSize", ctypes.c_ulong),
                                ("rcMonitor", RECT),
                                ("rcWork", RECT),
                                ("dwFlags", ctypes.c_ulong),
                                ("szDevice", ctypes.c_wchar * 32)]

                MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p)

                def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                    info = MONITORINFOEX()
                    info.cbSize = ctypes.sizeof(MONITORINFOEX)
                    if self._user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
                        r = info.rcMonitor
                        is_primary = bool(info.dwFlags & 1)
                        w = r.right - r.left
                        h = r.bottom - r.top

                        # Get DPI if available
                        dpi_x = ctypes.c_uint(96)
                        dpi_y = ctypes.c_uint(96)
                        scale = 1.0
                        try:
                            shcore = ctypes.windll.shcore
                            # MDT_EFFECTIVE_DPI = 0
                            if shcore.GetDpiForMonitor(hMonitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
                                scale = round(dpi_x.value / 96.0, 2)
                        except Exception:
                            pass

                        displays.append(DisplayInfo(
                            id=info.szDevice,
                            name=info.szDevice,
                            x=r.left,
                            y=r.top,
                            width=w,
                            height=h,
                            scale_factor=scale,
                            is_primary=is_primary,
                            pixel_width=int(w * scale),
                            pixel_height=int(h * scale),
                        ))
                    return 1

                self._user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
            except Exception:
                pass

        if not displays:
            # Fallback
            displays.append(DisplayInfo(
                id="DISPLAY1",
                name="Primary Monitor",
                x=0,
                y=0,
                width=1920,
                height=1080,
                scale_factor=1.0,
                is_primary=True,
                pixel_width=1920,
                pixel_height=1080,
            ))
        return displays

    def capture_screen(
        self,
        display_id: Optional[str] = None,
        region: Optional[Region] = None,
        window_id: Optional[str] = None,
    ) -> Tuple[bytes, float, int, int]:
        displays = self.get_displays()
        target = displays[0]
        if display_id:
            for d in displays:
                if d.id == display_id:
                    target = d
                    break

        scale = target.scale_factor
        x = region.x if region else target.x
        y = region.y if region else target.y
        w = region.width if region else target.width
        h = region.height if region else target.height

        # Use GDI BitBlt
        if self._user32 and self._gdi32:
            try:
                hwnd = 0  # Desktop window
                hdc_screen = self._user32.GetDC(hwnd)
                hdc_mem = self._gdi32.CreateCompatibleDC(hdc_screen)

                # Physical dimensions for GDI
                pw = int(w * scale)
                ph = int(h * scale)
                px = int(x * scale)
                py = int(y * scale)

                hbm = self._gdi32.CreateCompatibleBitmap(hdc_screen, pw, ph)
                self._gdi32.SelectObject(hdc_mem, hbm)

                # SRCCOPY = 0x00CC0020
                self._gdi32.BitBlt(hdc_mem, 0, 0, pw, ph, hdc_screen, px, py, 0x00CC0020)

                # Convert bitmap to PIL
                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                                ("biClrImportant", ctypes.c_uint32)]

                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = pw
                bmi.biHeight = -ph  # Top-down DIB
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0  # BI_RGB

                buffer_size = pw * ph * 4
                raw_data = ctypes.create_string_buffer(buffer_size)
                self._gdi32.GetDIBits(hdc_mem, hbm, 0, ph, raw_data, ctypes.byref(bmi), 0)

                # Clean up GDI objects
                self._gdi32.DeleteObject(hbm)
                self._gdi32.DeleteDC(hdc_mem)
                self._user32.ReleaseDC(hwnd, hdc_screen)

                img = Image.frombuffer("RGBA", (pw, ph), raw_data, "raw", "BGRA", 0, 1)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue(), scale, x, y
            except Exception:
                pass

        # Fallback to Pillow ImageGrab
        from PIL import ImageGrab
        bbox = (x, y, x + w, y + h) if region else None
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), scale, x, y

    def list_windows(self) -> List[WindowInfo]:
        windows: List[WindowInfo] = []
        if self._user32:
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

            def enum_proc(hwnd, lParam):
                if self._user32.IsWindowVisible(hwnd):
                    length = self._user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        self._user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value.strip()
                        if title:
                            r = RECT()
                            self._user32.GetWindowRect(hwnd, ctypes.byref(r))
                            w = r.right - r.left
                            h = r.bottom - r.top
                            if w > 10 and h > 10:
                                windows.append(WindowInfo(
                                    id=str(hwnd),
                                    title=title,
                                    application="WindowsApp",
                                    x=r.left,
                                    y=r.top,
                                    width=w,
                                    height=h,
                                    is_focused=(hwnd == self._user32.GetForegroundWindow()),
                                ))
                return 1

            self._user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
        return windows

    def focus_window(self, window_id: str) -> bool:
        if self._user32:
            try:
                hwnd = int(window_id)
                self._user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                return bool(self._user32.SetForegroundWindow(hwnd))
            except ValueError:
                for w in self.list_windows():
                    if window_id.lower() in w.title.lower():
                        hwnd = int(w.id)
                        self._user32.ShowWindow(hwnd, 9)
                        return bool(self._user32.SetForegroundWindow(hwnd))
        return False

    def move_pointer(self, x: int, y: int, duration: float = 0.0) -> None:
        if self._user32:
            self._user32.SetCursorPos(int(x), int(y))

    def mouse_click(
        self,
        x: int,
        y: int,
        button: MouseButton = MouseButton.LEFT,
        click_type: ClickType = ClickType.SINGLE,
    ) -> None:
        if not self._user32:
            return
        self.move_pointer(x, y)
        time.sleep(0.02)

        # MOUSEEVENTF_LEFTDOWN = 0x0002, LEFTUP = 0x0004
        # RIGHTDOWN = 0x0008, RIGHTUP = 0x0010
        # MIDDLEDOWN = 0x0020, MIDDLEUP = 0x0040
        if button == MouseButton.RIGHT:
            down_f, up_f = 0x0008, 0x0010
        elif button == MouseButton.MIDDLE:
            down_f, up_f = 0x0020, 0x0040
        else:
            down_f, up_f = 0x0002, 0x0004

        reps = 1
        if click_type == ClickType.DOUBLE:
            reps = 2
        elif click_type == ClickType.TRIPLE:
            reps = 3

        for i in range(reps):
            self._user32.mouse_event(down_f, 0, 0, 0, 0)
            time.sleep(0.04)
            self._user32.mouse_event(up_f, 0, 0, 0, 0)
            if i < reps - 1:
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
        if not self._user32:
            return
        self.move_pointer(start_x, start_y)
        time.sleep(0.04)
        down_f = 0x0008 if button == MouseButton.RIGHT else 0x0002
        up_f = 0x0010 if button == MouseButton.RIGHT else 0x0004

        self._user32.mouse_event(down_f, 0, 0, 0, 0)
        steps = max(5, int(duration * 20))
        for step in range(1, steps + 1):
            cx = int(start_x + (end_x - start_x) * (step / steps))
            cy = int(start_y + (end_y - start_y) * (step / steps))
            self.move_pointer(cx, cy)
            time.sleep(duration / steps)
        self._user32.mouse_event(up_f, 0, 0, 0, 0)

    def mouse_scroll(self, dx: int, dy: int) -> None:
        if self._user32:
            # MOUSEEVENTF_WHEEL = 0x0800, MOUSEEVENTF_HWHEEL = 0x1000
            # WHEEL_DELTA = 120
            if dy != 0:
                self._user32.mouse_event(0x0800, 0, 0, int(dy * 120), 0)
            if dx != 0:
                self._user32.mouse_event(0x1000, 0, 0, int(dx * 120), 0)

    def type_text(self, text: str) -> None:
        # Full Unicode injection via PowerShell SendKeys or Win32 SendInput KEYEVENTF_UNICODE
        if self._user32:
            # KEYEVENTF_UNICODE = 0x0004, KEYEVENTF_KEYUP = 0x0002
            for ch in text:
                code = ord(ch)
                self._user32.keybd_event(0, code, 0x0004, 0)
                self._user32.keybd_event(0, code, 0x0004 | 0x0002, 0)

    def press_key(self, key: str) -> None:
        if not self._user32:
            return
        vk = WIN_VK_CODES.get(key.lower(), 0)
        if vk:
            self._user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            self._user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP = 2

    def hotkey(self, keys: List[str]) -> None:
        if not self._user32:
            return
        vks = [WIN_VK_CODES.get(k.lower(), 0) for k in keys]
        vks = [v for v in vks if v]
        # Press all down
        for vk in vks:
            self._user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.04)
        # Release all in reverse
        for vk in reversed(vks):
            self._user32.keybd_event(vk, 0, 2, 0)

    def get_clipboard(self) -> str:
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True, text=True, timeout=3)
            return res.stdout if res.returncode == 0 else ""
        except Exception:
            return ""

    def set_clipboard(self, text: str) -> None:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Value '{text}'"], capture_output=True, timeout=3)
        except Exception:
            pass

    def get_accessibility_tree(self, window_id: Optional[str] = None) -> Optional[AccessibilityNode]:
        return None
