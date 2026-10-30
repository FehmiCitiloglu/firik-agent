"""Conservative, advisory configuration recommendations from actual trial rows.

No provider requests, catalog access, policy edits or runtime switching occurs.
Small/synthetic/unmatched evidence yields an explicit conservative fallback.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field

from firik_agent.benchmarks.manifests import StrictModel
from .comparison import validate_comparison_report

Objective = Literal["maximize_success", "minimize_cost", "minimize_latency", "balanced", "local_only"]
OBJECTIVES = {"maximize_success", "minimize_cost", "minimize_latency", "balanced", "local_only"}


class RecommendationConstraints(StrictModel):
    minimum_tasks: int = Field(default=5, ge=1, le=1000)
    minimum_repetitions: int = Field(default=3, ge=1, le=100)
    minimum_success_rate: float = Field(default=0.8, ge=0, le=1)
    allowed_backends: list[str] = Field(default_factory=lambda: ["native", "pi", "codex"], min_length=1, max_length=3)
    maximum_mean_cost_usd: float | None = Field(default=None, ge=0)
    maximum_mean_duration_ms: float | None = Field(default=None, gt=0)


def _wilson(successes: int, count: int) -> list[float] | None:
    if count == 0:
        return None
    z = 1.96
    p = successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    delta = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [max(0, center - delta), min(1, center + delta)]


def recommend(report: dict, objective: Objective = "balanced", constraints: RecommendationConstraints | dict | None = None) -> dict:
    """Suggest among tested configurations without changing any active session.

    Repeats are correlated, so uncertainty uses distinct tasks which succeeded
    in every repetition. This descriptive interval is not an external validity
    or performance guarantee. Unknown costs cannot win a cost objective.
    """
    if objective not in OBJECTIVES:
        raise ValueError("Unsupported performance recommendation objective.")
    constraints = RecommendationConstraints.model_validate(
        constraints.model_dump() if hasattr(constraints, "model_dump") else constraints or {})
    report = validate_comparison_report(report)
    from firik_agent.benchmarks.runner import source_fingerprint
    source_matches = report.get("source_sha256") == source_fingerprint()
    configurations = {configuration["id"]: configuration for configuration in report["plan"]["configurations"]}
    summaries = report["summary"]
    eligible, rejected = [], []
    for summary in summaries:
        identifier = summary["configuration_id"]
        configuration = configurations[identifier]
        rows = [row for row in report["rows"] if row["configuration_id"] == identifier]
        reasons = []
        if not source_matches:
            reasons.append("Installed runtime sources differ from the measured evidence; rerun before selecting a configuration.")
        if not summary["comparable"]:
            reasons.append("Execution identity, cleanup or source controls are not comparable.")
        if not summary["revision_verified"]:
            reasons.append("The backend did not verify the actual model revision.")
        if configuration["provider"] == "replay" or any(row["usage"].get("source") == "synthetic" for row in rows):
            reasons.append("Synthetic replay establishes regression behavior, not production model performance.")
        if summary["tasks"] < constraints.minimum_tasks or report["plan"]["repetitions"] < constraints.minimum_repetitions:
            reasons.append("Insufficient task diversity or repeated trials.")
        if summary["false_completions"]:
            reasons.append("False completion was observed; safety/verification cannot be weakened to optimize.")
        if summary["success_rate"] is None or summary["success_rate"] < constraints.minimum_success_rate:
            reasons.append("Verified success is below the required operational threshold.")
        if summary["backend"] not in constraints.allowed_backends:
            reasons.append("Backend is excluded by the operational constraints.")
        if constraints.maximum_mean_duration_ms is not None and (summary["average_duration_ms"] is None or summary["average_duration_ms"] > constraints.maximum_mean_duration_ms):
            reasons.append("Measured duration exceeds the requested limit.")
        complete_cost = summary["usage"]["cost_usd"]["complete"] and all(
            row["usage"].get("source") in {"provider", "provider_reported", "verified_price_estimate"} for row in rows)
        mean_cost = summary["usage"]["cost_usd"]["total"] / summary["trials"] if complete_cost else None
        if objective == "minimize_cost" and mean_cost is None:
            reasons.append("Cost coverage and provenance are insufficient for cost optimization.")
        if constraints.maximum_mean_cost_usd is not None and (mean_cost is None or mean_cost > constraints.maximum_mean_cost_usd):
            reasons.append("Known cost does not establish the requested spending constraint.")
        # A manifest's local_only label is not locality evidence. Adapters must
        # supply verified operational locality; current external modes do not.
        if objective == "local_only" and not all(row.get("local_execution_verified") is True for row in rows):
            reasons.append("The backend has not established local-only model execution.")
        tasks = {row["task_id"] for row in rows}
        stable = sum(all(row["success"] for row in rows if row["task_id"] == task) for task in tasks)
        candidate = {**summary, "mean_cost_usd": mean_cost,
                     "all_repetitions_successful_tasks": stable,
                     "task_success_interval_95": _wilson(stable, len(tasks))}
        if reasons:
            rejected.append({"configuration_id": identifier, "reasons": reasons})
        else:
            eligible.append(candidate)
    result = {"schema_version": 1, "objective": objective, "automatic_selection": False,
              "applied": False, "recommendation": None, "evidence_report_id": report.get("id"),
              "fallback": {"backend": "native", "action": "keep_current_configuration",
                           "reason": "Evidence is insufficient; retain the current explicitly selected backend and permissions."},
              "constraints": constraints.model_dump(mode="json"), "rejected": rejected,
              "uncertainty": "Descriptive evidence for this curated task set; correlated repeats are not independent tasks."}
    if not eligible:
        return result
    if objective in {"maximize_success", "local_only"}:
        key = lambda item: (-item["success_rate"], -item["task_success_interval_95"][0], item["average_duration_ms"], item["configuration_id"])
    elif objective == "minimize_cost":
        key = lambda item: (item["mean_cost_usd"], -item["success_rate"], item["average_duration_ms"], item["configuration_id"])
    elif objective == "minimize_latency":
        key = lambda item: (item["average_duration_ms"], -item["success_rate"], item["configuration_id"])
    else:
        # Prioritize verified completion and uncertainty. Use measured latency
        # as a tie breaker; missing costs never become invented zero costs.
        key = lambda item: (-item["success_rate"], -item["task_success_interval_95"][0], item["average_duration_ms"], item["configuration_id"])
    chosen = min(eligible, key=key)
    configuration = configurations[chosen["configuration_id"]]
    result["recommendation"] = {"configuration": configuration, "evidence": chosen,
        "reason": "Selected among measured configurations that meet the fixed verification and operational constraints."}
    result["fallback"] = None
    return result
