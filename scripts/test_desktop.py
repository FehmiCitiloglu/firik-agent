"""Run desktop checks with fresh native Tcl/Tk state for each GUI test."""
from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    pytest = [sys.executable, "-m", "pytest"]
    if sys.platform not in {"win32", "darwin"}:
        return subprocess.call(pytest, cwd=root)

    gui_files = sorted((root / "tests").glob("test_ui*.py"))
    relative = [str(path.relative_to(root)) for path in gui_files]
    result = subprocess.call(pytest + [f"--ignore={path}" for path in relative], cwd=root)
    # Native Tcl/Tk keeps process-wide initialization state across interpreters.
    # Test each window lifecycle in a fresh process, as a desktop launch does.
    # Collect parametrized cases as well and preserve every test's exit status.
    collection = subprocess.run(pytest + ["--collect-only", "-q", *relative],
                                cwd=root, text=True, capture_output=True)
    if collection.returncode:
        print(collection.stdout, flush=True)
        print(collection.stderr, file=sys.stderr, flush=True)
        return collection.returncode
    nodes = [line.strip() for line in collection.stdout.splitlines()
             if line.startswith("tests/") and "::" in line]
    if not nodes:
        print("No desktop GUI tests were collected", file=sys.stderr)
        return 1
    print(f"Running {len(nodes)} desktop cases in isolated processes", flush=True)
    for node in nodes:
        status = subprocess.call(pytest + ["-q", node], cwd=root)
        result = result or status
    return result


if __name__ == "__main__":
    raise SystemExit(main())
