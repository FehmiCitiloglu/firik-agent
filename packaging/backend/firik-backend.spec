# -*- mode: python ; coding: utf-8 -*-
"""Headless sidecar for the hybrid desktop preview. Build on each target OS."""
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).resolve().parents[1]
hidden = collect_submodules("firik_agent", filter=lambda name:
    name != "firik_agent.ui" and not name.startswith("firik_agent.ui."))
hidden.append({"darwin": "keyring.backends.macOS", "win32": "keyring.backends.Windows"}
              .get(sys.platform, "keyring.backends.SecretService"))
data = collect_data_files("firik_agent", excludes=["ui/**"])
data += copy_metadata("firik-agent")
data += copy_metadata("keyring")

analysis = Analysis(
    [str(root / "firik_agent/desktop_rpc/__main__.py")], pathex=[str(root)],
    binaries=[], datas=data, hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "_pytest", "tkinter", "_tkinter", "customtkinter", "tkinterdnd2", "PIL.ImageTk", "playwright"],
    noarchive=False, optimize=0,
)
pyz = PYZ(analysis.pure)
executable = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="firik-backend",
                 debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name="firik-backend")
