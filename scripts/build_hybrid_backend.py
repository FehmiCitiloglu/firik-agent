"""Build the GUI-independent backend that is bundled with Electron."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "PyInstaller", str(root / "packaging/backend/firik-backend.spec"),
                    "--distpath", str(root / "dist/hybrid-backend"),
                    "--workpath", str(root / "build/hybrid-backend"), "--noconfirm", "--clean"],
                   cwd=root, env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(root / "build/hybrid-backend-cache")},
                   check=True)
