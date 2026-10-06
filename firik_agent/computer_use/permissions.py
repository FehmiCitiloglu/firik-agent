"""Cross-platform permission checking, capability detection, and user guidance."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from typing import Dict, Tuple


class PermissionChecker:
    """Detects platform-specific system permissions and environment readiness."""

    @classmethod
    def check_all(cls) -> Dict[str, str]:
        """Returns a status dictionary for permissions on the current platform."""
        current_os = platform.system()
        if current_os == "Darwin":
            return cls._check_macos()
        elif current_os == "Windows":
            return cls._check_windows()
        elif current_os == "Linux":
            return cls._check_linux()
        return {"general": "unknown"}

    @classmethod
    def _check_macos(cls) -> Dict[str, str]:
        results = {
            "accessibility": "granted",
            "screen_recording": "granted",
        }

        # Check accessibility via CoreGraphics / AXIsProcessTrusted
        try:
            import ctypes
            appservices = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            is_trusted = appservices.AXIsProcessTrusted()
            results["accessibility"] = "granted" if is_trusted else "denied"
        except Exception:
            # Fallback check via osascript
            try:
                res = subprocess.run(
                    ["osascript", "-e", 'tell application "System Events" to get name of first process'],
                    capture_output=True,
                    timeout=2,
                )
                results["accessibility"] = "granted" if res.returncode == 0 else "denied"
            except Exception:
                results["accessibility"] = "unknown"

        # Check screen recording via screencapture probe
        try:
            res = subprocess.run(["screencapture", "-c", "-x"], capture_output=True, timeout=2)
            results["screen_recording"] = "granted" if res.returncode == 0 else "denied"
        except Exception:
            results["screen_recording"] = "unknown"

        return results

    @classmethod
    def _check_windows(cls) -> Dict[str, str]:
        results = {
            "ui_automation": "granted",
            "uac_elevation": "standard",
        }
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            results["uac_elevation"] = "elevated" if is_admin else "standard"
        except Exception:
            pass
        return results

    @classmethod
    def _check_linux(cls) -> Dict[str, str]:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        has_x11 = bool(os.environ.get("DISPLAY"))

        results = {}
        if has_wayland or session_type == "wayland":
            results["display_server"] = "wayland"
            results["screen_recording"] = "portal_required"
            results["pointer_control"] = "restricted_by_compositor"
            results["keyboard_control"] = "restricted_by_compositor"
        elif has_x11 or session_type == "x11":
            results["display_server"] = "x11"
            results["screen_recording"] = "granted"
            results["pointer_control"] = "granted"
            results["keyboard_control"] = "granted"
        else:
            results["display_server"] = "headless"
            results["screen_recording"] = "unavailable"
            results["pointer_control"] = "unavailable"
            results["keyboard_control"] = "unavailable"

        return results

    @classmethod
    def get_instructions_for_denied(cls) -> Dict[str, str]:
        """Provides human-readable instructions when a permission is missing."""
        current_os = platform.system()
        if current_os == "Darwin":
            return {
                "accessibility": (
                    "Erişilebilirlik (Accessibility) izni gerekli. Lütfen 'Sistem Ayarları > "
                    "Gizlilik ve Güvenlik > Erişilebilirlik' bölümünden 'firik-agent' veya terminal uygulamasını etkinleştirin."
                ),
                "screen_recording": (
                    "Ekran Kaydı (Screen Recording) izni gerekli. Lütfen 'Sistem Ayarları > "
                    "Gizlilik ve Güvenlik > Ekran ve Sistem Sesi Kaydı' bölümünden 'firik-agent' uygulamasını etkinleştirin."
                ),
            }
        elif current_os == "Linux":
            return {
                "wayland": (
                    "Wayland oturumunda global fare ve klavye kontrolü güvenlik politikası nedeniyle kısıtlıdır. "
                    "Tam otomasyon için X11 oturumu veya uyumlu XDG Desktop Portal ve ydotool yapılandırması önerilir."
                )
            }
        return {}
