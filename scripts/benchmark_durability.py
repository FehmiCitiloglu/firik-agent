#!/usr/bin/env python3
"""Repeatable offline SQLite and workspace-capture durability microbenchmark.

Creates temporary synthetic runs and Git workspaces; never contacts providers,
executes user commands, or writes to the user's actual project database.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal
from firik_agent.projects.models import Project
from firik_agent.projects.resume import capture_workspace


def summary(samples):
    ordered = sorted(samples)
    return {"samples": len(samples), "median_ms": round(statistics.median(samples), 4),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 4),
            "min_ms": round(min(samples), 4), "max_ms": round(max(samples), 4),
            "total_ms": round(sum(samples), 4), "raw_ms": [round(value, 4) for value in samples]}


def measure(operation):
    started = time.perf_counter()
    result = operation()
    return (time.perf_counter() - started) * 1000, result


def benchmark(samples=100, workspace_samples=10, sizes=(10, 100, 1000)):
    with TemporaryDirectory(prefix="firik-durability-benchmark-") as directory:
        root = Path(directory)
        db = ProjectDatabase(root / "projects.db")
        db.create_project(Project(id="benchmark", name="Offline durability benchmark"))
        store = CodingSessionStore(db)
        run = store.create_run("benchmark")
        journal = ExecutionJournal(db)
        baseline, treatment = [], []
        for index in range(samples):
            def events():
                store.append_event(run.id, {"type": "tool_call_started", "tool": "read_file"})
                store.append_event(run.id, {"type": "tool_call_finished", "tool": "read_file"})
            baseline.append(measure(events)[0])
            def durable():
                step = journal.begin(run.id, "tool:read_file", input_data={"path": "app.py"}, side_effect="pure_read")
                for state in ("dispatching", "running"):
                    journal.transition(run.id, step["attempt_id"], state)
                journal.transition(run.id, step["attempt_id"], "completed",
                                   result={"content": "short synthetic output", "is_error": False})
            treatment.append(measure(durable)[0])
        database_bytes = sum(path.stat().st_size for path in root.glob("projects.db*"))
        captures = []
        for count in sizes:
            workspace = root / f"workspace-{count}"
            workspace.mkdir()
            for index in range(count):
                folder = workspace / f"module_{index // 100:02d}"
                folder.mkdir(exist_ok=True)
                (folder / f"source_{index:04d}.py").write_text(
                    f"# Synthetic fixture {index}\n" + "value = 'safe benchmark content'\n" * 32, encoding="utf-8")
            subprocess.run(["git", "init", "--quiet", str(workspace)], check=True, capture_output=True, timeout=10)
            capture_times = []
            manifest = None
            for _ in range(workspace_samples):
                elapsed, manifest = measure(lambda: capture_workspace(workspace))
                capture_times.append(elapsed)
            assert manifest and manifest["complete"] and len(manifest["files"]) == count
            captures.append({"source_files": count, "source_bytes": sum(path.stat().st_size for path in workspace.rglob("*.py")),
                             "manifest_json_bytes": len(json.dumps(manifest).encode("utf-8")),
                             "git_state": "initialized, unborn HEAD, untracked synthetic files",
                             "capture": summary(capture_times)})
        return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
                "environment": {"platform": platform.platform(), "python": platform.python_version(),
                                "sqlite_synchronous": "FULL", "sqlite_journal_mode": "WAL"},
                "scope": "Offline synthetic microbenchmark; no providers, paid requests, desktop/device performance or power-loss simulation.",
                "method": {"baseline": "two existing CodingSessionStore.append_event transactions",
                           "additional_journal": "begin ready + dispatching + running + completed; four transactions, each appending its event atomically",
                           "workspace_capture": "file SHA-256/mode/size and read-only Git probes; separate from SQLite timings",
                           "combined_ms": "Baseline and journal cases are interleaved; treatment numbers are additional overhead, not end-to-end task latency."},
                "baseline_existing_events": summary(baseline), "additional_four_transition_journal": summary(treatment),
                "database_bytes_after_sqlite_cases": database_bytes, "workspace_cases": captures,
                "limits": ["Small synthetic outputs; no large result or checkpoint bodies.",
                           "One local machine/filesystem; timing noise and cache effects apply.",
                           "Workspace capture cost scales with scanned bytes and Git probes; fixture runs are cache-warm after the first sample.",
                           "Representative capture fixture sizes are within the default 2000-file/20-MiB scan bound."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--workspace-samples", type=int, default=10)
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 1 or args.workspace_samples < 1 or any(size < 1 or size > 2000 for size in args.sizes):
        parser.error("Positive sample counts and workspace sizes from 1 to 2000 are required.")
    result = benchmark(args.samples, args.workspace_samples, tuple(args.sizes))
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps({"baseline_median_ms": result["baseline_existing_events"]["median_ms"],
                      "additional_journal_median_ms": result["additional_four_transition_journal"]["median_ms"],
                      "workspace_capture_median_ms": {str(case["source_files"]): case["capture"]["median_ms"]
                                                      for case in result["workspace_cases"]},
                      "output": str(args.output.resolve()) if args.output else None}, indent=2))
    if not args.output:
        print(serialized)


if __name__ == "__main__":
    main()
