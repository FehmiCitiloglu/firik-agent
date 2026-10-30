#!/usr/bin/env python3
"""Offline full-suite adapter evidence plus matched native-loop overhead samples.

No credentials, network, external agent executable or paid provider is used.
Historical recovery/context ablations are intentionally not manufactured.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firik_agent.benchmarks.coding import coding_suite
from firik_agent.benchmarks.evaluator import evaluate_sources
from firik_agent.benchmarks.runner import source_fingerprint
from firik_agent.benchmarks.storage import write_outputs
from firik_agent.harness.comparison import (ComparativeConfiguration, ComparisonPlan, _fixture,
                                            offline_native_factory, run_comparison, validate_comparison_report)
from firik_agent.harness.models import HarnessExecutionPolicy, HarnessTask


async def _sample(task, configuration, plan, variant):
    with tempfile.TemporaryDirectory(prefix="firik-adapter-overhead-") as directory:
        root = Path(directory) / "repository"
        _fixture(root, task)
        backend = offline_native_factory(configuration, root, task, plan)
        started, count, session, result = time.monotonic(), 0, None, None
        status = "failed"
        try:
            if variant == "original-native-loop":
                runner = getattr(backend.agent, "_run_native_stream", None)
                if runner is None:
                    raise RuntimeError("Original native loop is not available for a matched overhead sample.")
                async for event in runner(task.prompt, session=backend.chat_session, **backend.runtime_options):
                    count += 1
                    if event.type.value == "done":
                        status = "completed"
                    elif event.type.value == "error":
                        status = "failed"
            else:
                policy = HarnessExecutionPolicy(workspace=str(root), project_id="harness-comparison", tool_mode="native",
                    permissions=dict(plan.permissions), timeout_seconds=plan.timeout_seconds)
                session = await backend.initialize_session(policy=policy, provider={"name": "replay", "model": "scripted-v1"})
                run_id = await backend.execute_task(session, HarnessTask(task.prompt, model="scripted-v1", profile=configuration.profile))
                async for event in backend.stream_events(session, run_id):
                    count += 1
                result = await backend.inspect_run(session, run_id)
                status = result.status
        finally:
            if session is not None:
                await backend.close_session(session)
        duration = (time.monotonic() - started) * 1000
        passed, detail = evaluate_sources(root, task.evaluation.expected_files)
        return {"task_id": task.id, "variant": variant, "duration_ms": duration,
                "events": count, "status": status, "source_verified": passed,
                "success": status == "completed" and passed, "verification_detail": detail}


def _percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = int(index)
    high = min(len(ordered) - 1, low + 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


async def benchmark(*, task_count=40, repetitions=3, overhead_tasks=5, overhead_repetitions=5):
    suite = coding_suite()
    suite = suite.model_copy(update={"tasks": suite.tasks[:task_count]})
    configuration = ComparativeConfiguration(id="current-native-full", provider="replay", model="scripted-v1",
                                             model_revision="scripted-v1")
    plan = ComparisonPlan(suite=suite, configurations=[configuration], repetitions=repetitions)
    initial_source = source_fingerprint()
    comparison = validate_comparison_report(await run_comparison(plan))
    rows = []
    # Alternate the first variant in every pair to reduce a systematic warm-up
    # order effect. These short local samples do not imply a production speedup.
    for repetition in range(1, overhead_repetitions + 1):
        for index, task in enumerate(suite.tasks[:overhead_tasks]):
            variants = ["original-native-loop", "native-harness-adapter"]
            if (repetition + index) % 2:
                variants.reverse()
            for variant in variants:
                rows.append({**await _sample(task, configuration, plan, variant), "repetition": repetition})
    summaries = {}
    for variant in ("original-native-loop", "native-harness-adapter"):
        samples = [row for row in rows if row["variant"] == variant]
        timings = [row["duration_ms"] for row in samples]
        summaries[variant] = {"samples": len(samples), "successes": sum(row["success"] for row in samples),
                              "median_ms": statistics.median(timings), "p95_ms": _percentile(timings, .95)}
    deltas = []
    for repetition in range(1, overhead_repetitions + 1):
        for task in suite.tasks[:overhead_tasks]:
            pair = {row["variant"]: row["duration_ms"] for row in rows if row["task_id"] == task.id and row["repetition"] == repetition}
            deltas.append(pair["native-harness-adapter"] - pair["original-native-loop"])
    overhead = {"schema_version": 1, "kind": "native-adapter-overhead", "source_sha256": initial_source,
        "source_changed": initial_source != source_fingerprint(), "model": "scripted-v1", "provider": "replay",
        "usage_provenance": "synthetic", "model_requests_paid": 0,
        "measurement": "Native execution/stream/session-close wall time; fixture construction and final independent evaluator excluded equally.",
        "summary": summaries, "paired_median_delta_ms": statistics.median(deltas),
        "paired_p95_delta_ms": _percentile(deltas, .95), "rows": rows,
        "historical_phase_ablations": {name: "not executed: no immutable matched source/environment snapshot supplied; safety/recovery was not disabled to fabricate an ablation"
            for name in ("pre-recovery-native", "recovery-only", "improved-context-only")},
        "external_backend_results": {name: "not executed: no supported installed and authorized isolated runtime supplied" for name in ("pi", "codex")},
        "limitations": ["Both samples use the current complete native runtime; this measures wrapper overhead, not earlier phases.",
            "Synthetic replay is deterministic runtime validation, not model quality, paid-provider latency or billing evidence.",
            "Short local filesystem timings are noisy; no universal speedup is claimed.",
            "Performance figures are invalidated when sources change during measurement."]}
    return comparison, overhead


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tasks", type=int, default=40)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--overhead-tasks", type=int, default=5)
    parser.add_argument("--overhead-repeat", type=int, default=5)
    args = parser.parse_args(argv)
    if not 1 <= args.tasks <= 40 or not 1 <= args.repeat <= 100 or not 1 <= args.overhead_tasks <= args.tasks or not 1 <= args.overhead_repeat <= 100:
        parser.error("Task/repeat bounds exceeded.")
    if any((args.output / name).exists() or (args.output / name).is_symlink() for name in ("comparison.json", "comparison.md", "overhead.json", "overhead.md")):
        parser.error("Evidence already exists; choose a new output directory.")
    comparison, overhead = asyncio.run(benchmark(task_count=args.tasks, repetitions=args.repeat,
        overhead_tasks=args.overhead_tasks, overhead_repetitions=args.overhead_repeat))
    summary = comparison["summary"][0]
    description = f"# Current native harness comparison\n\n{summary['verified_successes']}/{summary['trials']} exact-source trials passed. Synthetic offline replay only; no paid model calls.\n"
    write_outputs(args.output, comparison, description, stem="comparison")
    description = "# Native adapter overhead\n\nCurrent original loop and wrapper use the same complete runtime and synthetic replay.\n\n"
    for variant, metrics in overhead["summary"].items():
        description += f"- {variant}: {metrics['successes']}/{metrics['samples']} passed, median {metrics['median_ms']:.3f} ms, p95 {metrics['p95_ms']:.3f} ms.\n"
    description += f"\nPaired median wrapper difference: {overhead['paired_median_delta_ms']:.3f} ms. Local timing only; no production speedup claim.\n"
    write_outputs(args.output, overhead, description, stem="overhead")
    print(json.dumps({"comparison": summary, "overhead": overhead["summary"],
                      "paired_median_delta_ms": overhead["paired_median_delta_ms"],
                      "source_changed": comparison["source_changed"] or overhead["source_changed"]}, indent=2))
    return 0 if summary["verified_successes"] == summary["trials"] and all(row["success"] for row in overhead["rows"]) and not comparison["source_changed"] and not overhead["source_changed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
