"""Run affected native voice tests in separate Tk processes."""
from pathlib import Path
import json
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[4]
output = Path(__file__).resolve().parent
files = ["tests/test_ui_voice.py", "tests/test_ui_voice_modes.py",
         "tests/test_ui_voice_conversation.py", "tests/test_ui_audio_engine.py",
         "tests/test_ui_voice_provider_settings.py"]
collection = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *files],
                            cwd=root, text=True, capture_output=True)
if collection.returncode:
    print(collection.stdout + collection.stderr)
    raise SystemExit(collection.returncode)
nodes = [line for line in collection.stdout.splitlines() if line.startswith("tests/") and "::" in line]
rerun_failed = "--failed" in sys.argv[1:]
previous = (json.loads((output / "provider-desktop-results.json").read_text())["results"]
            if rerun_failed else [])
if rerun_failed:
    failed_nodes = {result["node"] for result in previous if result["status"] != 0}
    nodes = [node for node in nodes if node in failed_nodes]
results = list(previous)
log_name = "provider-desktop-reruns.log" if rerun_failed else "provider-desktop-tests.log"
with (output / log_name).open("a" if rerun_failed else "w") as log:
    for index, node in enumerate(nodes, 1):
        started = time.monotonic()
        try:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", node],
                                    cwd=root, text=True, capture_output=True, timeout=60)
            status = result.returncode
            detail = result.stdout + result.stderr
        except subprocess.TimeoutExpired:
            status, detail = "timeout", "Test exceeded 60 seconds."
        record = {"node": node, "status": status, "seconds": round(time.monotonic() - started, 3)}
        results = [item for item in results if item["node"] != node]
        results.append(record)
        line = f"{index}/{len(nodes)} {node}: {status}"
        log.write(line + "\n" + detail + "\n")
        log.flush()
        print(line, flush=True)
failures = [result for result in results if result["status"] != 0]
summary = {"cases": len(results), "failures": len(failures), "results": results}
(output / "provider-desktop-results.json").write_text(json.dumps(summary, indent=2) + "\n")
print(f"Cases: {len(results)}, failures: {len(failures)}", flush=True)
raise SystemExit(bool(failures))
