"""Frozen probe dispatch is explicit and cannot initialize a user profile."""
import sys
from pathlib import Path

import pytest

from firik_agent.ui import app
from firik_agent.browser import cli


def test_browser_smoke_bypasses_user_profile_and_download(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["firik-agent", "--smoke-test", "--check-browser-backend"])
    seen = []
    async def launch(headful):
        seen.append(headful)
        return {"state": "ready", "browser_version": "fixture"}
    monkeypatch.setattr(cli, "_launch_probe", launch)
    monkeypatch.setattr(app, "FirikApp", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("User profile opened")))
    assert app.main() == 0
    assert seen == [False] and '"state": "ready"' in capsys.readouterr().out


def test_browser_smoke_reports_failure_without_fallback(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["firik-agent", "--smoke-test", "--check-browser-backend"])
    async def launch(headful):
        raise RuntimeError("Missing browser")
    monkeypatch.setattr(cli, "_launch_probe", launch)
    monkeypatch.setattr(app, "FirikApp", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Fallback")))
    assert app.main() == 1
    assert "Missing browser" in capsys.readouterr().err


@pytest.mark.parametrize("platform,expected", [
    ("darwin", "/fixture/Library/Caches/ms-playwright"),
    ("win32", "/fixture/AppData/Local/ms-playwright"),
    ("linux", "/fixture/.cache/ms-playwright"),
])
def test_frozen_driver_uses_external_cache_without_downloading(monkeypatch, platform, expected):
    import os
    from firik_agent.browser.config import configure_frozen_browser_cache
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: Path("/fixture"))
    for name in ("PLAYWRIGHT_BROWSERS_PATH", "LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    configure_frozen_browser_cache()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(Path(expected))


def test_frozen_cache_respects_explicit_host_override(monkeypatch):
    import os
    from firik_agent.browser.config import configure_frozen_browser_cache
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/explicit/host/cache")
    configure_frozen_browser_cache()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/explicit/host/cache"
