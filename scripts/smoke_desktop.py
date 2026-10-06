"""Exercise the frozen GUI, including bundled Tcl/Tk and assets, without an API key."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
if sys.platform == "darwin":
    executable = root / "dist/firik-agent.app/Contents/MacOS/firik-agent"
else:
    executable = root / "dist" / ("firik-agent.exe" if sys.platform == "win32" else "firik-agent")
# Run outside the checkout to detect accidental reliance on source files.
with tempfile.TemporaryDirectory() as directory:
    env = dict(os.environ, FIRIK_API_KEY="", XDG_CONFIG_HOME=directory, APPDATA=directory)
    subprocess.run([str(executable), "--smoke-test"], cwd=directory, env=env, check=True, timeout=60)
