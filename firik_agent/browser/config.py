"""Optional browser configuration. Upgrades never grant browser authority."""
from __future__ import annotations

from datetime import datetime
import locale as _locale
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field


_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")


def resolve_browser_locale(value: str = "auto") -> str:
    """Resolve 'auto' to the host OS BCP-47 locale, falling back to 'en-US'."""
    cleaned = (value or "").strip()
    if cleaned and cleaned.lower() != "auto":
        return cleaned
    candidates = [
        os.environ.get("FIRIK_BROWSER_LOCALE", ""),
        os.environ.get("LC_ALL", ""),
        os.environ.get("LC_MESSAGES", ""),
        os.environ.get("LANG", ""),
    ]
    try:
        detected = _locale.getlocale()[0]
        if detected:
            candidates.append(detected)
    except Exception:
        pass
    for raw in candidates:
        tag = raw.split(".", 1)[0].split("@", 1)[0].strip().replace("_", "-")
        if not tag or tag.upper() in {"C", "POSIX"}:
            continue
        if _LOCALE_RE.fullmatch(tag):
            parts = tag.split("-")
            formatted = [parts[0].lower()]
            for part in parts[1:]:
                formatted.append(part.upper() if len(part) == 2 and part.isalpha() else part)
            return "-".join(formatted)
    return "en-US"


def resolve_browser_timezone(value: str = "auto") -> str:
    """Resolve 'auto' to a valid host IANA timezone, falling back to 'UTC'."""
    cleaned = (value or "").strip()
    if cleaned and cleaned.lower() != "auto":
        return cleaned
    candidates: list[str] = []
    tz_env = os.environ.get("TZ", "").strip().lstrip(":")
    if tz_env:
        candidates.append(tz_env)
    try:
        tzinfo = datetime.now().astimezone().tzinfo
        key = getattr(tzinfo, "key", None)
        if isinstance(key, str) and key:
            candidates.append(key)
    except Exception:
        pass
    if os.name != "nt":
        try:
            real = os.path.realpath("/etc/localtime")
            if "/zoneinfo/" in real:
                candidates.append(real.split("/zoneinfo/", 1)[1])
        except OSError:
            pass
    for candidate in candidates:
        if not candidate or len(candidate) > 64:
            continue
        try:
            ZoneInfo(candidate)
            return candidate
        except Exception:
            continue
    return "UTC"


def configure_frozen_browser_cache():
    """Keep frozen Playwright on the explicitly installed external browser cache.

    Playwright otherwise forces PLAYWRIGHT_BROWSERS_PATH=0 for frozen apps,
    which expects Chromium inside the bundle. Firik distributes only its driver.
    Respect any host-provided override and never create/download anything here.
    """
    import os
    from pathlib import Path
    import sys
    if not getattr(sys, "frozen", False) or "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        return
    if sys.platform == "darwin":
        cache = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        cache = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cache / "ms-playwright")


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, strict=True)

    enabled: bool = False
    headless: bool = True
    strict_network_isolation: bool = False
    max_sessions: int = Field(default=3, ge=1, le=16)
    max_tabs_per_session: int = Field(default=8, ge=1, le=32)
    max_actions_per_task: int = Field(default=100, ge=1, le=10000)
    action_timeout_ms: int = Field(default=15000, ge=100, le=60000)
    navigation_timeout_ms: int = Field(default=30000, ge=100, le=120000)
    launch_timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    max_snapshot_chars: int = Field(default=16000, ge=100, le=64000)
    max_diagnostics: int = Field(default=100, ge=1, le=1000)
    max_screenshot_bytes: int = Field(default=8_000_000, ge=10000, le=32_000_000)
    approval_ttl_seconds: int = Field(default=60, ge=1, le=300)
    viewport_width: int = Field(default=1280, ge=320, le=2560)
    viewport_height: int = Field(default=800, ge=240, le=1440)
    locale: str = Field(default="auto", min_length=2, max_length=32)
    timezone_id: str = Field(default="auto", min_length=1, max_length=64)
