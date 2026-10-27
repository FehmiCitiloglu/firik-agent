"""Benchmark analytics over the native RunMetrics snapshot and event journal.

Provider snapshots stay in core.metrics; this module never re-adds usage frames.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def distribution(values: list[float]) -> dict[str, Any]:
    import statistics
    return {"count": len(values), "mean": statistics.mean(values) if values else None,
            "stdev": statistics.stdev(values) if len(values) > 1 else None,
            "min": min(values) if values else None, "max": max(values) if values else None,
            "p50": percentile(values, .5), "p95": percentile(values, .95)}


def enrich_result(row: dict) -> dict:
    """Add evidence-derived metrics without interpreting final prose as a test."""
    trace = row.get("trace", [])
    errors = Counter()
    compactions: dict[tuple[str, str], dict] = {}
    cost_sources = set()
    for event in trace:
        kind, metadata = event.get("type"), event.get("metadata") or {}
        if kind == "tool_call_error":
            errors[(event.get("tool_name"), event.get("content"))] += 1
        if kind == "model_request_finished":
            cost_report = metadata.get("cost_report") or {}
            if cost_report.get("cost") is not None:
                cost_sources.add(cost_report.get("source", "unknown"))
            if metadata.get("request_kind") == "compaction":
                compactions[(metadata.get("agent_id", "orchestrator"), metadata.get("request_id", "unknown"))] = metadata
    claimed = row.get("completion_claimed")
    if claimed is None:
        claimed = any(event.get("type") == "done" for event in trace) and row["runtime_status"] == "completed"
    row["completion_claimed"] = bool(claimed)
    row["false_completion"] = bool(claimed and not row["verified"])
    metrics = row["metrics"]
    metrics["repeated_tool_errors"] = sum(max(0, value - 1) for value in errors.values())
    metrics["compaction_overhead_ms"] = sum((item.get("metrics") or {}).get("latency_ms", 0) or 0 for item in compactions.values())
    metrics["compaction_usage"] = {
        key: (sum(item["usage"][key] for item in compactions.values())
              if compactions and all(isinstance(item.get("usage", {}).get(key), (int, float)) for item in compactions.values()) else None)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    mode = row.get("mode", "offline")
    has_tokens = any(value is not None for key, value in metrics.get("reported_usage", metrics.get("usage", {})).items() if key != "cost")
    metrics["provenance"] = {"counts_and_latency": "exact_local", "tokens": ("synthetic" if mode == "offline" else "provider_reported") if has_tokens else "unknown",
                              "cost": sorted(cost_sources) if cost_sources else "unknown"}
    row.setdefault("failure_reason", None if row["fixture_passed"] else
                   (row.get("terminal_outcome", {}).get("reason") or
                    ("verification_failed" if row["runtime_status"] == "completed" else row["runtime_status"])))
    if all(key in row for key in ("run_id", "trial_id", "configuration_sha256", "configuration", "fixture_revision")):
        from firik_agent.benchmarks.models import (Cost, IndividualTrial, ModelProviderIdentity, RecoveryEvent,
            TerminalOutcome, TokenUsage, ToolExecutionTrace, VerificationResult)
        tools, recoveries = [], []
        failed_tools = set()
        for sequence, event in enumerate(trace, 1):
            metadata = event.get("metadata") or {}
            if event.get("type") in {"tool_call_result", "tool_call_error"}:
                tools.append(ToolExecutionTrace(tool_call_id=str(metadata.get("tool_call_id") or f"event-{sequence}"),
                    name=event.get("tool_name") or "unknown", status="failed" if event["type"] == "tool_call_error" else "completed",
                    duration_ms=event.get("duration_ms"), event_sequence=sequence))
                name = event.get("tool_name") or "unknown"
                if event["type"] == "tool_call_error":
                    failed_tools.add(name)
                elif name in failed_tools:
                    recoveries.append(RecoveryEvent(kind="tool-correction", event_sequence=sequence))
                    failed_tools.remove(name)
            if event.get("type") == "model_request_finished" and metadata.get("attempt", 0):
                recoveries.append(RecoveryEvent(kind="retry", request_id=metadata.get("request_id"), event_sequence=sequence))
            if event.get("type") == "context_compacted":
                recoveries.append(RecoveryEvent(kind="compaction", event_sequence=sequence))
        usage = metrics.get("usage", {})
        token_source = "synthetic" if mode == "offline" else "provider"
        if not has_tokens:
            token_source = "unknown"
        cost_source = "unknown" if not cost_sources else "provider" if cost_sources == {"provider"} else "estimate"
        row["record"] = IndividualTrial(trial_id=row["trial_id"], run_id=row["run_id"], suite_id=row.get("suite_id", "offline-smoke" if mode == "offline" else "coding"),
            task_id=row["task_id"], repetition=row["trial"], configuration_sha256=row["configuration_sha256"],
            task_sha256=row["manifest_sha256"], starting_revision=row["fixture_revision"],
            model=ModelProviderIdentity.model_validate(row["configuration"]["model"]),
            outcome=TerminalOutcome(status=row["runtime_status"], completion_claimed=bool(claimed), verified=row["verified"],
                                    failure_kind=row.get("terminal_outcome", {}).get("reason")),
            verification=VerificationResult(passed=row["verified"], source_match=row["verification"]["source_match"], detail=row["verification"]["detail"]),
            tokens=TokenUsage(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                              cached_tokens=usage.get("cached_tokens"), source=token_source, complete=metrics.get("usage_complete", False)),
            cost=Cost(amount=usage.get("cost"), source=cost_source, complete=metrics.get("cost_complete", False)),
            tools=tools, recoveries=recoveries).model_dump(mode="json")
        metrics["tool_recoveries"] = sum(event.kind == "tool-correction" for event in recoveries)
    return row


def enrich_report(report: dict) -> dict:
    for row in report["results"]:
        row.setdefault("suite_id", report.get("suite_id", "offline-smoke" if row.get("mode", "offline") == "offline" else "coding"))
    rows = [enrich_result(row) for row in report["results"]]
    summary = report.setdefault("summary", {})
    count = len(rows)
    # pass@1 uses the first observed attempt of each task, never the best repeat.
    first = {}
    for row in sorted(rows, key=lambda value: value["trial"]):
        first.setdefault(row["task_id"], row)
    successes = sum(row["task_success"] for row in rows)
    claims = sum(row["completion_claimed"] for row in rows)
    costs = [row["metrics"].get("usage", {}).get("cost") for row in rows]
    cost = sum(costs) if costs and all(value is not None for value in costs) else None
    failures = Counter(row["failure_reason"] for row in rows if not row["fixture_passed"])
    statuses = Counter(row["runtime_status"] for row in rows)
    summary.update({"trials": count, "fixtures_passed": sum(row["fixture_passed"] for row in rows),
        "fixture_pass_rate": sum(row["fixture_passed"] for row in rows) / count if count else None,
        "completed_verified_tasks": successes,
        "pass_at_1": sum(row["task_success"] for row in first.values()) / len(first) if first else None,
        "task_success_rate": successes / count if count else None,
        "verified_completion_rate": sum(row["verified"] for row in rows) / count if count else None,
        "false_completion_rate": sum(row["false_completion"] for row in rows) / claims if claims else None,
        "false_completions": sum(row["false_completion"] for row in rows), "completion_claims": claims,
        "wall_time_ms": distribution([row["wall_time_ms"] for row in rows]),
        "time_to_completion_ms": distribution([row.get("runtime_wall_time_ms", row["wall_time_ms"]) for row in rows if row["task_success"]]),
        "request_latency_ms": distribution([value for row in rows for value in row["metrics"].get("latency_ms", [])]),
        "per_task": {identifier: distribution([row["wall_time_ms"] for row in rows if row["task_id"] == identifier]) for identifier in first},
        "cost": cost, "cost_per_task": cost / count if cost is not None and count else None,
        "cost_per_successful_task": cost / successes if cost is not None and successes else None,
        "successful_tasks_per_dollar": successes / cost if cost else None,
        "failure_breakdown": dict(sorted(failures.items())), "terminal_outcomes": dict(sorted(statuses.items())),
        "safety_violations": sum(row.get("safety_violations", 0) for row in rows),
        "crashes": sum(row["runtime_status"] == "crashed" or row.get("terminal_outcome", {}).get("reason") == "crash" for row in rows),
        "interrupted_runs": statuses["interrupted"],
        "timeouts": sum((row.get("terminal_outcome", {}).get("reason") or row.get("terminal_outcome", {}).get("failure_kind")) == "timeout" or row.get("failure_reason") == "timeout" for row in rows),
        "usage": {key: sum(values) if values and all(value is not None for value in values) else None
                  for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens")
                  for values in [[row["metrics"].get("usage", {}).get(key) for row in rows]]}})
    for key in ("request_count", "tool_calls", "tool_errors", "recovery_attempts", "repeated_tool_errors", "compaction_requests", "compaction_overhead_ms"):
        summary[key] = sum(row["metrics"].get(key, 0) for row in rows)
    summary["retry_count"] = summary["recovery_attempts"]
    summary["tool_recoveries"] = sum(row["metrics"].get("tool_recoveries", 0) for row in rows)
    summary["recovery_count"] = summary["retry_count"] + summary["tool_recoveries"]
    summary["cost_sources"] = sorted({source for row in rows for source in row["metrics"]["provenance"]["cost"]
                                      if isinstance(row["metrics"]["provenance"]["cost"], list)})
    summary["cost_is_estimate"] = any(source not in {"provider", "provider_reported"} for source in summary["cost_sources"])
    return report
