"""The existing native desktop data location, shared by both desktop clients."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def desktop_config_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "firik" / "config.yaml"
