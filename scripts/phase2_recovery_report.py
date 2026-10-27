#!/usr/bin/env python3
"""Summarize Phase 2 native recovery traces without changing benchmark oracles.

Usage: python scripts/phase2_recovery_report.py BASELINE CANDIDATE OUTPUT_DIR
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Permit direct invocation from a source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firik_agent.benchmarks.comparison import compare_reports, validate_report

REPLAY_ACTIONS = {"retry", "backoff", "compact_context", "reduce_tool_output", "same_provider_nonstream"}
DENIALS = {"authentication_failed", "permission_denied", "policy_refusal", "cancelled", "budget_exceeded"}
EFFECTS = {"conditional_write", "non_idempotent_mutation", "external_side_effect", "unknown"}


def trace_metrics(report: dict) -> dict:
    """Count only observed dispatches/results; decisions alone are not retries."""
    validate_report(report)
    counts = {"recovery_decisions": 0, "retry_requests": 0, "successful_retry_requests": 0,
              "false_retry_count": 0, "unclassified_retry_requests": 0, "interface_fallback_requests": 0,
              "reconciliation_required": 0, "uncertain_tool_reconciliation_decisions": 0,
              "uncertain_tool_operations_observed": 0, "uncertain_tool_operations_not_replayed": 0,
              "repeated_mutation_dispatches_after_success": 0,
              "repeated_mutation_dispatches_after_unknown": 0,
              "retry_delay_seconds": 0.0}
    for row in report["results"]:
        last_decision = None
        retry_ids = set()
        known_tool_outcomes = {}
        uncertain_tools, replayed_uncertain_tools = set(), set()
        for event in row.get("trace", []):
            metadata = event.get("metadata", {})
            kind = event.get("type")
            if kind == "recovery":
                counts["recovery_decisions"] += 1
                last_decision = metadata
                action = metadata.get("recovery_action", metadata.get("action"))
                if action == "reconcile" or metadata.get("reconciliation_status") == "required":
                    counts["reconciliation_required"] += 1
                    if metadata.get("request_phase") == "tool_dispatched":
                        counts["uncertain_tool_reconciliation_decisions"] += 1
                delay = metadata.get("delay")
                if action in REPLAY_ACTIONS and type(delay) in {float, int}:
                    counts["retry_delay_seconds"] += delay
            elif kind == "model_request_started":
                if metadata.get("request_kind") == "compaction":
                    continue
                if metadata.get("attempt", 0) > 0:
                    counts["retry_requests"] += 1
                    retry_ids.add(metadata.get("request_id"))
                    # A denial or partial response must never be replayed even
                    # if a malformed policy incorrectly labels it repeatable.
                    if last_decision is not None and last_decision.get("recovery_action", last_decision.get("action")) == "same_provider_nonstream":
                        counts["interface_fallback_requests"] += 1
                    if last_decision is None:
                        counts["unclassified_retry_requests"] += 1
                    elif (last_decision.get("failure_kind") in DENIALS
                          or last_decision.get("request_phase") == "partial_response"
                          or last_decision.get("safe_to_repeat") is not True
                          or (last_decision.get("recovery_action", last_decision.get("action")) == "same_provider_nonstream"
                              and (last_decision.get("request_phase") != "not_submitted"
                                   or last_decision.get("failure_kind") != "unsupported_capability"))
                          or last_decision.get("recovery_action", last_decision.get("action")) not in REPLAY_ACTIONS):
                        counts["false_retry_count"] += 1
                last_decision = None
            elif kind == "model_request_finished":
                if metadata.get("request_id") in retry_ids and metadata.get("status") == "completed":
                    counts["successful_retry_requests"] += 1
            elif kind == "tool_call_started":
                fingerprint = metadata.get("fingerprint")
                risk = metadata.get("side_effect_risk")
                if fingerprint and risk in EFFECTS:
                    previous = known_tool_outcomes.get(fingerprint)
                    if previous in {"success", "unknown"}:
                        counts["repeated_mutation_dispatches_after_" + previous] += 1
                    if previous == "unknown":
                        replayed_uncertain_tools.add(fingerprint)
                    known_tool_outcomes[fingerprint] = "unknown"
            elif kind in {"tool_call_result", "tool_call_error"}:
                fingerprint = metadata.get("fingerprint")
                if fingerprint:
                    if kind == "tool_call_result":
                        known_tool_outcomes[fingerprint] = "success"
                    elif metadata.get("reconciliation_status") == "required" or metadata.get("failure_kind") == "tool_outcome_unknown":
                        known_tool_outcomes[fingerprint] = "unknown"
                        uncertain_tools.add(fingerprint)
                    else:
                        known_tool_outcomes[fingerprint] = "failed"
        counts["uncertain_tool_operations_observed"] += len(uncertain_tools)
        counts["uncertain_tool_operations_not_replayed"] += len(uncertain_tools - replayed_uncertain_tools)
    denominator = counts["retry_requests"]
    counts["successful_retry_rate"] = counts["successful_retry_requests"] / denominator if denominator else None
    counts["retry_rate_denominator"] = "Observed retry request dispatches; not all errors or policy decisions."
    counts["recovery_opportunity_success_rate"] = None
    counts["recovery_opportunity_limitation"] = (
        "The frozen Phase 1 suite has stop-on-error scripts, no authorized fault-then-success turns. "
        "Eligible recovery opportunities require the separate scripted integration matrix.")
    return counts


def build_report(baseline: dict, candidate: dict) -> dict:
    comparison = compare_reports(baseline, candidate)
    before = comparison["profiles"]["baseline"]["summary"]
    after = comparison["profiles"]["candidate"]["summary"]
    delta_fields = ("fixtures_passed", "completed_verified_tasks", "false_completions", "completion_claims",
                    "request_count", "tool_calls", "tool_errors", "safety_violations")
    return {"schema_version": 1, "comparison": comparison,
            "baseline_recovery": trace_metrics(baseline), "candidate_recovery": trace_metrics(candidate),
            "deltas": {field: after.get(field, 0) - before.get(field, 0) for field in delta_fields},
            "latency": {"baseline_mean_wall_ms": before["wall_time_ms"]["mean"],
                        "candidate_mean_wall_ms": after["wall_time_ms"]["mean"],
                        "added_mean_wall_ms": after["wall_time_ms"]["mean"] - before["wall_time_ms"]["mean"]},
            "cost_change": None,
            "limitations": [
                "Deterministic offline replay; no live model requests or provider billing measurement.",
                "False retries are actual retry dispatches after denial/partial output or without safe policy authorization; unclassified legacy attempts are separate.",
                "Repeat-mutation counts are diagnostic observations, not a universal proof: intentional user-authorized identical operations can be valid.",
                "A reconcile decision prevents automatic replay; it does not establish the remote operation outcome.",
                "The original Phase 1 task/evaluator/control fingerprints are preserved; native ask-mode prose can claim unknown work that only the independent oracle can disprove.",
                "Added wall time includes local fixture/persistence/host scheduling noise and is not an isolated engine microbenchmark."]}


def markdown(result: dict) -> str:
    comparison = result["comparison"]
    before = comparison["profiles"]["baseline"]["summary"]
    after = comparison["profiles"]["candidate"]["summary"]
    lines = ["# Phase 2 matched offline validation", "", "| Measure | Baseline | Candidate |", "| --- | ---: | ---: |"]
    for label, key in (("Fixture checks passed", "fixtures_passed"), ("Verified tasks", "completed_verified_tasks"),
                       ("Native completion claims", "completion_claims"), ("False completion claims", "false_completions"),
                       ("Model requests", "request_count"), ("Native tool starts", "tool_calls"), ("Safety violations", "safety_violations")):
        lines.append(f"| {label} | {before[key]} | {after[key]} |")
    for label, key in (("Actual retry requests", "retry_requests"), ("False retries", "false_retry_count"),
                       ("Required reconciliations", "reconciliation_required"),
                       ("Observed uncertain tools with no later replay", "uncertain_tool_operations_not_replayed"),
                       ("Repeated mutation dispatch after unknown result", "repeated_mutation_dispatches_after_unknown")):
        lines.append(f"| {label} | {result['baseline_recovery'][key]} | {result['candidate_recovery'][key]} |")
    lines += ["", f"Added mean wall time: {result['latency']['added_mean_wall_ms']:.2f} ms/trial.",
              "Recovery-opportunity success and cost changes: **unmeasured** in this frozen suite.", "", "## Limits", ""]
    lines += [f"- {item}" for item in result["limitations"]]
    lines += ["", "See raw JSON for per-task outcomes, request evidence, descriptive task-cluster intervals and diagnostic counts.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    baseline, candidate = (json.loads(path.read_text()) for path in (args.baseline, args.candidate))
    result = build_report(baseline, candidate)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    (args.output / "report.md").write_text(markdown(result))
    print(args.output.resolve() / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
