"""Run current voice integration and affected native UI checks in isolation."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
output = Path(__file__).resolve().parent
files = ["tests/test_ui_voice.py", "tests/test_ui_audio_engine.py", "tests/test_ui_voice_modes.py", "tests/test_ui_voice_conversation.py", "tests/test_ui_chat.py",
         "tests/test_ui_chat_workspace.py", "tests/test_ui_coding_workspace.py", "tests/test_ui_model_selection.py"]
collection = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *files],
                            cwd=root, text=True, capture_output=True)
if collection.returncode:
    print(collection.stdout + collection.stderr)
    raise SystemExit(collection.returncode)
nodes = [line for line in collection.stdout.splitlines() if line.startswith("tests/") and "::" in line]
failed = []
with (output / "desktop-tests.log").open("w") as log:
    for i, node in enumerate(nodes, 1):
        try:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", node],
                                    cwd=root, text=True, capture_output=True, timeout=60)
            line = f"{i}/{len(nodes)} {node}: {result.returncode}"
            log.write(line + "\n" + result.stdout + result.stderr + "\n")
            if result.returncode:
                failed.append(node)
        except subprocess.TimeoutExpired:
            line = f"{i}/{len(nodes)} {node}: TIMEOUT"
            log.write(line + "\n")
            failed.append(node)
        log.flush()
        print(line, flush=True)
    log.write(f"Cases: {len(nodes)}, failures: {len(failed)}\n")
print(f"Cases: {len(nodes)}, failures: {len(failed)}", flush=True)
if failed:
    print("\n".join(failed), flush=True)
raise SystemExit(bool(failed))
