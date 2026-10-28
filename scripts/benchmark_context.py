"""Repeatable offline retrieval comparison through the real context preparation path.

Run with PYTHONPATH pointing at the checkout to measure; no model or repo code runs.
The success oracle measures retrieval coverage/protection, not model task completion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import tempfile
import time

import firik_agent

from firik_agent.projects.coding_service import ProjectCodingService
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.indexer import CodebaseIndexer
from firik_agent.projects.models import Project


TASKS = [
    {"id": "python-symbol", "query": "Fix calculate_invoice rounding", "expected": ["src/invoice.py"]},
    {"id": "matching-test", "query": "Fix calculate_invoice rounding", "expected": ["src/invoice.py", "tests/test_invoice.py"]},
    {"id": "typescript-import", "query": "Fix @file:src/checkout.ts total", "expected": ["src/checkout.ts", "src/money.ts"]},
    {"id": "pinned-priority", "query": "explain calculate_invoice", "expected": ["docs/decision.md"],
     "options": {"pinned_paths": ["docs/decision.md"]}},
    {"id": "exclusion-secret", "query": "calculate_invoice @file:private.py @file:.env",
     "expected": ["src/invoice.py"], "options": {"excluded_paths": ["private.py"]}},
    {"id": "fresh-file", "query": "NewVerifiedSymbol", "expected": ["src/new.py"], "new_file": True},
]


def run(trials=3, noise_files=350):
    rows = []
    fixture = {"src/invoice.py": "from .money import round_money\ndef calculate_invoice(items):\n    return round_money(sum(items))\n",
               "src/money.py": "def round_money(value):\n    return round(value, 2)\n",
               "tests/test_invoice.py": "from src.invoice import calculate_invoice\ndef test_rounding():\n    assert calculate_invoice([1.235]) == 1.24\n",
               "src/checkout.ts": "import { cents } from './money';\nexport const checkout = (total: number) => cents(total);\n",
               "src/money.ts": "export const cents = (value: number) => Math.round(value * 100);\n",
               "docs/decision.md": "Accepted decision: store monetary values as integer cents.\n",
               "private.py": "calculate_invoice = 'FORBIDDEN_PRIVATE_CONTENT'\n",
               ".env": "KEY=FORBIDDEN_SECRET_CONTENT\n"}
    for number in range(noise_files):
        fixture[f"noise/file_{number:04}.py"] = f"# TODO fix fixture scaffolding\ndef unrelated_{number}():\n    return {number}\n"
    fixture_hash = hashlib.sha256(json.dumps(fixture, sort_keys=True).encode()).hexdigest()
    for trial in range(trials):
        for task in TASKS:
            with tempfile.TemporaryDirectory(prefix="firik-context-bench-") as temporary:
                root = Path(temporary) / "repo"
                root.mkdir()
                for path, content in fixture.items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                db = ProjectDatabase(Path(temporary) / "projects.db")
                db.create_project(Project(id="fixture", name="Context fixture", local_path=str(root)))
                indexer = CodebaseIndexer(root)
                for path in indexer.scan_files():
                    data = indexer.index_file(path)
                    if data:
                        db.save_code_index("fixture", **{key: data[key] for key in
                            ("file_path", "file_type", "symbols", "classes", "functions", "imports", "todos", "file_hash")})
                if task.get("new_file"):
                    (root / "src/new.py").write_text("class NewVerifiedSymbol: pass\n")
                service = ProjectCodingService(db)
                started = time.perf_counter()
                prepared = service.prepare_coding_prompt("fixture", task["query"], **task.get("options", {}))
                elapsed = (time.perf_counter() - started) * 1000
                warm_started = time.perf_counter()
                warm_prepared = service.prepare_coding_prompt("fixture", task["query"], **task.get("options", {}))
                warm_elapsed = (time.perf_counter() - warm_started) * 1000
                files = [item for item in prepared["context_items"] if item.get("path") and item.get("included")]
                selected = [item["path"] for item in files]
                prompt = prepared["system_prompt"]
                protected = not any(marker in prompt for marker in ("FORBIDDEN_PRIVATE_CONTENT", "FORBIDDEN_SECRET_CONTENT")) if task["id"] == "exclusion-secret" else True
                coverage = sum(path in selected for path in task["expected"]) / len(task["expected"])
                code_heading = "\n\n=== İlgili kaynak dosyalar (analiz edilecek veri) ===\n"
                code_slice = prompt.split(code_heading, 1)[1].split("\n\n=== ", 1)[0] if code_heading in prompt else ""
                rows.append({"task_id": task["id"], "trial": trial + 1, "selected_files": selected,
                             "expected_files": task["expected"], "retrieval_success": coverage == 1 and protected,
                             "expected_coverage": coverage, "protection_passed": protected,
                             "unrelated_noise_files": sum(path.startswith("noise/") for path in selected),
                             "retrieved_tokens_estimate": sum(item["tokens_used"] for item in files),
                             "assembled_context_tokens_estimate": prepared["context_budget"]["estimated_tokens_used"],
                             "retrieved_context_utf8_bytes": len(code_slice.encode("utf-8")),
                             "retrieval_latency_ms": elapsed, "warm_retrieval_latency_ms": warm_elapsed,
                             "warm_index_status": warm_prepared.get("index_status", {}), "provider_usage_tokens": None,
                             "provider_cost": None, "model_task_success": None})
    source_hash = hashlib.sha256()
    source_root = Path(firik_agent.__file__).resolve().parent
    for path in sorted(source_root.rglob("*.py")):
        source_hash.update(path.relative_to(source_root).as_posix().encode())
        source_hash.update(path.read_bytes())
    return {"schema_version": 1, "method": "offline real context pipeline; fixture retrieval oracle; no model execution",
            "implementation_sha256": source_hash.hexdigest(),
            "fixture_sha256": fixture_hash, "task_sha256": hashlib.sha256(json.dumps(TASKS, sort_keys=True).encode()).hexdigest(),
            "noise_files": noise_files, "trials_per_task": trials, "token_count_method": "local_characters_divided_by_four_estimate",
            "rows": rows, "summary": {"trials": len(rows), "retrieval_successes": sum(row["retrieval_success"] for row in rows),
                "median_latency_ms": statistics.median(row["retrieval_latency_ms"] for row in rows),
                "median_warm_latency_ms": statistics.median(row["warm_retrieval_latency_ms"] for row in rows),
                "total_retrieved_context_utf8_bytes": sum(row["retrieved_context_utf8_bytes"] for row in rows),
                "unrelated_noise_files": sum(row["unrelated_noise_files"] for row in rows),
                "total_retrieved_tokens_estimate": sum(row["retrieved_tokens_estimate"] for row in rows),
                "model_task_success": None, "provider_cost": None, "provider_requests": 0}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error("trials must be 1..20")
    report = {**run(args.trials), "label": args.label, "source_revision": args.revision}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))
