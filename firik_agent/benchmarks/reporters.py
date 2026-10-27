"""Readable reports rendered from validated evidence, never cached summaries."""
from __future__ import annotations

from firik_agent.benchmarks.metrics import enrich_report
from firik_agent.projects.coding_sessions import redact_sensitive


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;")


def _number(value, digits=3) -> str:
    return "unknown" if value is None else f"{value:.{digits}f}"


def markdown_report(report: dict) -> str:
    from firik_agent.benchmarks.comparison import validate_report
    validate_report(report)
    report = redact_sensitive(report)
    summary = enrich_report(report)["summary"]
    meta = report["metadata"]
    lines = ["# Firik Harness Benchmark Lab", "", _cell(report["method"]), "",
        f"Label: {_cell(report.get('label', 'candidate'))} · Commit: {_cell(meta.get('repository', {}).get('commit') or 'unknown')}",
        f"Profile: {_cell(meta.get('profile', 'unknown'))} · Model/provider: {_cell(meta.get('model'))}/{_cell(meta.get('provider'))}",
        f"Trials: {summary['fixtures_passed']}/{summary['trials']} objective fixture checks passed. Safety violations: {summary['safety_violations']}.",
        f"Completed and objectively verified tasks: {summary['completed_verified_tasks']}.", "",
        "| Metric | Value |", "| --- | ---: |",
        f"| Pass@1 (first attempt per task) | {_number(summary['pass_at_1'])} |",
        f"| Task success rate | {_number(summary['task_success_rate'])} |",
        f"| Verified completion rate (all trials) | {_number(summary['verified_completion_rate'])} |",
        f"| False completion rate (claims) | {_number(summary['false_completion_rate'])} |",
        f"| P50 / P95 elapsed ms | {_number(summary['wall_time_ms']['p50'])} / {_number(summary['wall_time_ms']['p95'])} |",
        f"| Model requests / tool calls / tool errors | {summary['request_count']} / {summary['tool_calls']} / {summary['tool_errors']} |",
        f"| Retries / repeated tool errors | {summary['retry_count']} / {summary['repeated_tool_errors']} |",
        f"| Input / output / cached tokens | {_number(summary['usage']['prompt_tokens'], 0)} / {_number(summary['usage']['completion_tokens'], 0)} / {_number(summary['usage']['cached_tokens'], 0)} |",
        f"| Cost USD / task / successful task | {_number(summary['cost'], 6)} / {_number(summary['cost_per_task'], 6)} / {_number(summary['cost_per_successful_task'], 6)} |",
        f"| Cost evidence | {_cell(', '.join(summary['cost_sources']) or 'unknown')} {'(estimate)' if summary['cost_is_estimate'] else ''} |",
        f"| Crashes / interrupted runs | {summary['crashes']} / {summary['interrupted_runs']} |", "",
        "| Task | Passed | Expected runtime status | Mean ms | Stdev ms |", "| --- | ---: | --- | ---: | ---: |"]
    for task in report["tasks"]:
        rows = [row for row in report["results"] if row["task_id"] == task["id"]]
        stats = summary["per_task"][task["id"]]
        lines.append(f"| {_cell(task['id'])} | {sum(row['fixture_passed'] for row in rows)}/{len(rows)} | {_cell(rows[0]['expected_runtime_status'])} | {_number(stats['mean'])} | {_number(stats['stdev'])} |")
    lines += ["", "## Failure breakdown", ""]
    lines += [f"- {_cell(reason)}: {count}" for reason, count in summary["failure_breakdown"].items()] or ["No unexpected fixture failures."]
    lines += ["", "## Provenance", "", f"Source SHA256: `{_cell(meta.get('source_sha256', 'unknown'))}`",
        f"Controlled settings SHA256: `{_cell(meta.get('controlled_settings_sha256', 'unknown'))}`",
        f"Evaluator: `{_cell(meta.get('evaluator_version', 'unknown'))}`",
        "", "Expected failure/cancellation fixtures count as safe harness checks; they are excluded from completed tasks.",
        "Offline token counts are synthetic usage, not tokenizer measurements. Missing usage and cost remain unknown.",
        "Fixed source/AST verification parses repository code as data and does not execute it. It is not a semantic test/build pass.",
        "Wall timings include Git/SQLite setup, native runtime, evaluation and persistence; cleanup is excluded.", ""]
    return "\n".join(lines)


def markdown_comparison(report: dict) -> str:
    report = redact_sensitive(report)
    lines = ["# Paired harness comparison", "", report["limitation"], "",
             "| Metric | Baseline | Candidate |", "| --- | ---: | ---: |"]
    for key in ("task_success_rate", "pass_at_1", "fixture_pass_rate", "request_count", "tool_calls", "cost_per_task", "cost_per_successful_task"):
        a, b = (report["profiles"][side]["summary"].get(key) for side in ("baseline", "candidate"))
        lines.append(f"| {_cell(key)} | {_number(a)} | {_number(b)} |")
    lines += ["", "| Task | Success delta | Elapsed delta ms | Regression |", "| --- | ---: | ---: | --- |"]
    for row in report["paired_tasks"]:
        lines.append(f"| {_cell(row['task_id'])} | {_number(row['success_delta'])} | {_number(row['wall_time_delta_ms'])} | {'yes' if row['regression'] else 'no'} |")
    lines += ["", "Regressions: " + (", ".join(map(_cell, report["regressions"])) or "none"),
              "", "Task-cluster bootstrap 95% intervals (2000 resamples; minimum 5 tasks):",
              f"`{report['task_cluster_bootstrap95']}`", ""]
    return "\n".join(lines)
