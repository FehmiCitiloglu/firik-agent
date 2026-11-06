"""Optional browser configuration. Upgrades never grant browser authority."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
    locale: str = Field(default="en-US", min_length=2, max_length=32)
    timezone_id: str = Field(default="UTC", min_length=1, max_length=64)
