"""Strict paired comparisons; resample tasks rather than correlated repeats."""
from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any

from firik_agent.benchmarks.metrics import distribution, enrich_report, percentile


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def validate_report(report: dict) -> dict[tuple[str, int], dict]:
    """Reject corrupt evidence before computing or rendering summary values."""
    try:
        if (not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1
                or not isinstance(report.get("method"), str) or not report["method"]
                or not isinstance(report.get("metadata"), dict) or not isinstance(report.get("summary"), dict)
                or not isinstance(report.get("tasks"), list) or not 1 <= len(report["tasks"]) <= 1000
                or not isinstance(report.get("results"), list) or not report["results"]):
            raise ValueError("Unsupported or malformed benchmark report.")
        trials = report["trials_per_task"]
        if type(trials) is not int or not 1 <= trials <= 100:
            raise ValueError("Report has an invalid repeated trial count.")
        tasks = {task["id"]: task for task in report["tasks"]}
        configuration = report["metadata"].get("configuration")
        if configuration is not None:
            from firik_agent.benchmarks.models import HarnessConfiguration
            configuration = HarnessConfiguration.model_validate(configuration).model_dump(mode="json")
            if fingerprint(configuration) != report["metadata"].get("configuration_sha256"):
                raise ValueError("Report configuration fingerprint is invalid.")
        if len(tasks) != len(report["tasks"]) or any(not isinstance(key, str) or not key for key in tasks):
            raise ValueError("Report task identities must be unique strings.")
        for task in tasks.values():
            sha = task["manifest_sha256"]
            if not isinstance(sha, str) or len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
                raise ValueError("Report task manifest fingerprints are invalid.")
        rows = {}
        for row in report["results"]:
            identifier, trial = row["task_id"], row["trial"]
            revision = row.get("fixture_revision")
            if not isinstance(revision, str) or len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision):
                raise ValueError("Report starting Git revision is invalid.")
            if configuration is not None and (row.get("configuration") != configuration or row.get("configuration_sha256") != fingerprint(configuration)):
                raise ValueError("Trial configuration fingerprint disagrees with report provenance.")
            if "configuration" in row:
                from firik_agent.benchmarks.models import HarnessConfiguration
                HarnessConfiguration.model_validate(row["configuration"])
                if fingerprint(row["configuration"]) != row.get("configuration_sha256"):
                    raise ValueError("Trial configuration fingerprint is invalid.")
            if type(trial) is not int:
                raise ValueError("Report trial identities must be integers.")
            if identifier not in tasks or row["manifest_sha256"] != tasks[identifier]["manifest_sha256"]:
                raise ValueError("Trial manifest fingerprint does not match its task.")
            if any(type(row[key]) is not bool for key in ("fixture_passed", "task_success", "verified", "persistence_verified")):
                raise ValueError("Report outcomes must be strict boolean values.")
            if row["runtime_status"] not in {"completed", "failed", "blocked", "cancelled", "interrupted", "crashed"}:
                raise ValueError("Unsupported benchmark runtime status.")
            if row["task_success"] != (row["fixture_passed"] and row["runtime_status"] == "completed") or row["verified"] != row["task_success"]:
                raise ValueError("Runtime failures cannot be counted as completed verified tasks.")
            if row["fixture_passed"] and (row["runtime_status"] != row["expected_runtime_status"] or not row["persistence_verified"]):
                raise ValueError("Passing fixture outcome disagrees with observed runtime evidence.")
            for field in ("wall_time_ms", "runtime_wall_time_ms"):
                value = row.get(field, 0)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Report contains invalid timing measurements.")
            if not isinstance(row.get("metrics"), dict):
                raise ValueError("Report metrics are missing.")
            for field in ("request_count", "tool_calls", "tool_errors", "recovery_attempts"):
                value = row["metrics"].get(field, 0)
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Report contains invalid metric counts.")
            for field in ("completion_claimed", "false_completion"):
                if field in row and type(row[field]) is not bool:
                    raise ValueError("Completion evidence must be strict boolean values.")
            if row.get("failure_reason") is not None and not isinstance(row["failure_reason"], str):
                raise ValueError("Failure reason must be a string or null.")
            if type(row.get("safety_violations", 0)) is not int or row.get("safety_violations", 0) < 0:
                raise ValueError("Safety violations must be nonnegative integer counts.")
            terminal = row.get("terminal_outcome", {})
            if not isinstance(terminal, dict) or any(terminal.get(key) is not None and not isinstance(terminal[key], str) for key in ("reason", "failure_kind")):
                raise ValueError("Invalid terminal outcome.")
            for field in ("latency_ms", "ttft_ms"):
                values = row["metrics"].get(field, [])
                if not isinstance(values, list) or any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values):
                    raise ValueError("Report contains invalid request latency measurements.")
            usage = row["metrics"].get("usage", {})
            if not isinstance(usage, dict):
                raise ValueError("Report usage must be an object.")
            for value in usage.values():
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError("Report contains invalid usage measurements.")
            if not isinstance(row.get("trace", []), list) or any(not isinstance(event, dict) for event in row.get("trace", [])):
                raise ValueError("Report trace must be an ordered event list.")
            for event in row.get("trace", []):
                if not isinstance(event.get("metadata", {}), dict):
                    raise ValueError("Event metadata must be an object.")
                if any(event.get(key) is not None and not isinstance(event[key], str) for key in ("type", "tool_name", "content")):
                    raise ValueError("Event text fields must be strings or null.")
                metadata = event.get("metadata", {})
                for field in ("usage", "metrics", "cost_report"):
                    if field in metadata and not isinstance(metadata[field], dict):
                        raise ValueError("Event usage, metrics and cost evidence must be objects.")
                if metadata.get("cost_report", {}).get("source") is not None and not isinstance(metadata["cost_report"]["source"], str):
                    raise ValueError("Cost source must be a string.")
                for field in ("attempt",):
                    if field in metadata and (type(metadata[field]) is not int or metadata[field] < 0):
                        raise ValueError("Request attempts must be nonnegative integers.")
                for field in ("latency_ms", "retry_count"):
                    value = metadata.get("metrics", {}).get(field)
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                        raise ValueError("Event metrics must be finite nonnegative measurements.")
                if metadata.get("request_kind") == "compaction":
                    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        value = metadata.get("usage", {}).get(field)
                        if value is not None and (type(value) is not int or value < 0):
                            raise ValueError("Compaction usage must be nonnegative integer measurements.")
                duration = event.get("duration_ms")
                if duration is not None and (type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0):
                    raise ValueError("Event duration must be a finite nonnegative measurement.")
            identity = identifier, trial
            if identity in rows:
                raise ValueError("Report contains duplicate trial identities.")
            rows[identity] = row
        if rows.keys() != {(identifier, trial) for identifier in tasks for trial in range(1, trials + 1)}:
            raise ValueError("Report must contain every declared task and repeated trial exactly once.")
        # Native trial IDs are additive for legacy reports but must be unique.
        for field in ("run_id", "trial_id"):
            values = [row[field] for row in rows.values() if field in row]
            if len(set(values)) != len(values):
                raise ValueError("Report contains duplicate native trial identities.")
        return rows
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Malformed benchmark trial.") from error


def _bootstrap(values: list[float]) -> list[float] | None:
    if len(values) < 5:
        return None
    rng = random.Random(20261027)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(2000))
    return [percentile(means, .025), percentile(means, .975)]


def compare_reports(baseline: dict, candidate: dict) -> dict:
    left, right = validate_report(baseline), validate_report(candidate)
    if any(report["metadata"].get("source_changed") for report in (baseline, candidate)):
        raise ValueError("Harness sources changed during evaluation; reports cannot be compared.")
    keys = ("provider", "model", "prompt_revision", "tool_revision", "evaluator_version", "capability_evidence", "lab_version", "environment")
    for key in keys:
        if key not in baseline["metadata"] or key not in candidate["metadata"]:
            raise ValueError(f"Reports are missing matched settings evidence: {key}")
        if baseline["metadata"][key] != candidate["metadata"][key]:
            raise ValueError(f"Reports do not use matched settings: {key}")
    if baseline["method"] != candidate["method"]:
        raise ValueError("Reports do not use matched settings: evaluation mode")
    if baseline["metadata"].get("endpoint_sha256") != candidate["metadata"].get("endpoint_sha256"):
        raise ValueError("Reports do not use matched settings: provider endpoint")
    if baseline["tasks"] != candidate["tasks"] or baseline["trials_per_task"] != candidate["trials_per_task"]:
        raise ValueError("Reports do not use matched tasks, budgets and trial counts.")
    a, b = baseline["metadata"], candidate["metadata"]
    ca, cb = a.get("configuration"), b.get("configuration")
    if (ca is None) != (cb is None):
        raise ValueError("Reports do not use matched settings: configuration evidence")
    if ca is not None:
        for key in ("model", "token_budget", "timeout_seconds", "permissions", "evaluator_version", "max_output_tokens", "temperature"):
            if ca.get(key) != cb.get(key):
                raise ValueError(f"Reports do not use matched settings: configuration.{key}")
    if "controlled_settings_sha256" not in a or "controlled_settings_sha256" not in b:
        raise ValueError("Reports are missing matched settings evidence: controlled_settings_sha256")
    if a["controlled_settings_sha256"] != b["controlled_settings_sha256"]:
        # Explicit configuration reports may differ in the implementation being
        # tested, while dataset, budgets, permissions, model and verifier stay fixed.
        if not isinstance(ca, dict) or not isinstance(cb, dict) or ca == cb:
            raise ValueError("Reports do not use matched settings: controlled_settings_sha256")
        for key in ("model", "token_budget", "timeout_seconds", "permissions", "evaluator_version", "max_output_tokens", "temperature"):
            if key not in ca or ca.get(key) != cb.get(key):
                raise ValueError(f"Reports do not use matched settings: configuration.{key}")
    if left.keys() != right.keys():
        raise ValueError("Reports do not contain matched trial identities.")
    for key in left:
        if left[key].get("fixture_revision") != right[key].get("fixture_revision"):
            raise ValueError("Reports do not use matched tasks: starting Git revision")
        for field in ("token_budget", "timeout_seconds", "permissions", "evaluator_version", "model", "max_output_tokens", "temperature"):
            if left[key].get("configuration", {}).get(field) != right[key].get("configuration", {}).get(field):
                raise ValueError(f"Reports do not use matched settings: trial.{field}")
    paired = []
    for task in baseline["tasks"]:
        identifier = task["id"]
        pairs = [(left[key], right[key]) for key in left if key[0] == identifier]
        before = sum(row[0]["task_success"] for row in pairs) / len(pairs)
        after = sum(row[1]["task_success"] for row in pairs) / len(pairs)
        fixture_delta = sum(y["fixture_passed"] - x["fixture_passed"] for x, y in pairs) / len(pairs)
        paired.append({"task_id": identifier, "baseline_success_rate": before, "candidate_success_rate": after,
            "success_delta": after - before, "fixture_pass_delta": fixture_delta, "regression": after < before or fixture_delta < 0,
            "wall_time_delta_ms": sum(y["wall_time_ms"] - x["wall_time_ms"] for x, y in pairs) / len(pairs),
            "request_delta": sum(y["metrics"].get("request_count", 0) - x["metrics"].get("request_count", 0) for x, y in pairs) / len(pairs),
            "tool_call_delta": sum(y["metrics"].get("tool_calls", 0) - x["metrics"].get("tool_calls", 0) for x, y in pairs) / len(pairs)})
    # Recompute summaries from evidence. Stored presentation totals are untrusted.
    summaries = [enrich_report({**report, "summary": {}})["summary"] for report in (baseline, candidate)]
    return {"schema_version": 1, "method": baseline["method"],
        "paired_wall_time_delta_ms": distribution([right[key]["wall_time_ms"] - left[key]["wall_time_ms"] for key in left]),
        "fixture_pass_delta": sum(row["fixture_passed"] for row in right.values()) - sum(row["fixture_passed"] for row in left.values()),
        "completed_verified_task_delta": sum(row["task_success"] for row in right.values()) - sum(row["task_success"] for row in left.values()),
        "paired_tasks": paired, "regressions": [row["task_id"] for row in paired if row["regression"]],
        "profiles": {"baseline": {"profile": a.get("profile"), "configuration": a.get("configuration"), "summary": summaries[0]},
                     "candidate": {"profile": b.get("profile"), "configuration": b.get("configuration"), "summary": summaries[1]}},
        "task_cluster_bootstrap95": {"success_delta": _bootstrap([row["success_delta"] for row in paired]),
                                     "wall_time_delta_ms": _bootstrap([row["wall_time_delta_ms"] for row in paired]),
                                     "resamples": 2000, "seed": 20261027, "unit": "task", "minimum_tasks": 5},
        "performance_claim": None,
        "limitation": "Paired local dataset evidence only. Repeats are clustered by task; small differences do not establish a universal model or harness ranking."}
