"""Exercise the frozen GUI and PortAudio discovery without streams or an API key."""
import argparse
from pathlib import Path
import os
import subprocess
import sys
import tempfile

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distpath", type=Path, default=Path(__file__).resolve().parents[1] / "dist")
    parser.add_argument("--text-only", action="store_true", help="Skip PortAudio for an intentionally audio-free build")
    parser.add_argument("--browser", action="store_true", help="Also launch explicitly installed Chromium through the frozen driver")
    args = parser.parse_args()
    if sys.platform == "darwin":
        relative = "firik-agent.app/Contents/MacOS/firik-agent"
    else:
        relative = "firik-agent.exe" if sys.platform == "win32" else "firik-agent"
    command = [str(args.distpath.resolve() / relative), "--smoke-test"]
    if not args.text_only:
        command.append("--check-voice-backend")
    # Run outside the checkout to detect accidental reliance on source files.
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ, FIRIK_API_KEY="", XDG_CONFIG_HOME=directory, APPDATA=directory)
        env.pop("FIRIK_SMOKE_UNUSED_KEY", None)
        subprocess.run(command, cwd=directory, env=env, check=True, timeout=60)
        if args.browser:
            subprocess.run([command[0], "--smoke-test", "--check-browser-backend"],
                           cwd=directory, env=env, check=True, timeout=60)
    print("Frozen desktop smoke passed" + ("." if args.text_only else " with PortAudio device discovery."))


if __name__ == "__main__":
    main()
