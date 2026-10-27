from copy import deepcopy
import asyncio
import json
from pathlib import Path

import pytest

from firik_agent.benchmarks.comparison import compare_reports, validate_report
from firik_agent.benchmarks.manifests import builtin_tasks
from firik_agent.benchmarks.metrics import enrich_report, percentile
from firik_agent.benchmarks.reporters import markdown_report, markdown_comparison
from firik_agent.benchmarks.runner import run_suite
from firik_agent.benchmarks.storage import export_report, load_report, read_json


@pytest.fixture(scope="module")
def evidence():
    return asyncio.run(run_suite([builtin_tasks()[1], builtin_tasks()[-1]], trials=2))


def test_percentiles_first_attempt_and_unknown_cost(evidence):
    report = deepcopy(evidence)
    failed = report["results"][0]
    failed.update(fixture_passed=False, task_success=False, verified=False, completion_claimed=True)
    result = enrich_report(report)["summary"]
    assert result["pass_at_1"] == .5  # Later successful repeats do not repair pass@1.
    assert result["task_success_rate"] == .75
    assert result["false_completion_rate"] == .25
    assert result["cost"] is result["cost_per_successful_task"] is None
    assert result["usage"]["total_tokens"] is None
    assert percentile([1, 2, 3, 4, 5], .95) == pytest.approx(4.8)
    assert result["wall_time_ms"]["p50"] is not None


def test_paired_regression_detected_and_summary_tampering_ignored(evidence):
    candidate = deepcopy(evidence)
    candidate["summary"]["completed_verified_tasks"] = 1000000
    row = candidate["results"][0]
    row.update(fixture_passed=False, task_success=False, verified=False)
    result = compare_reports(evidence, candidate)
    assert result["completed_verified_task_delta"] == -1
    assert result["regressions"] == [row["task_id"]]
    assert result["task_cluster_bootstrap95"]["success_delta"] is None
    assert "Regressions:" in markdown_comparison(result)
    assert result["performance_claim"] is None


@pytest.mark.parametrize("mutation", [
    lambda r: r["results"].append(deepcopy(r["results"][0])),
    lambda r: r["results"].pop(),
    lambda r: r["results"][0].update(fixture_revision="unknown"),
    lambda r: r["results"][0]["metrics"].update(request_count=-1),
    lambda r: r["results"][0]["metrics"]["usage"].update(cost=float("nan")),
    lambda r: r["results"][0].update(verified=1),
])
def test_corrupt_evidence_rejected(evidence, mutation):
    value = deepcopy(evidence)
    mutation(value)
    with pytest.raises(ValueError):
        validate_report(value)


def test_task_pairing_rejects_different_starting_revision(evidence):
    candidate = deepcopy(evidence)
    candidate["results"][0]["fixture_revision"] = "0" * 40
    with pytest.raises(ValueError, match="starting Git revision"):
        compare_reports(evidence, candidate)


def test_source_controlled_ab_configuration(evidence):
    from firik_agent.benchmarks.models import HarnessConfiguration
    from firik_agent.benchmarks.comparison import fingerprint
    left, right = deepcopy(evidence), deepcopy(evidence)
    for report, identity, requests in ((left, "baseline", 16), (right, "candidate", 8)):
        config = HarnessConfiguration(id=identity, request_budget=requests).model_dump(mode="json")
        report["metadata"].update(configuration=config, configuration_sha256=fingerprint(config),
                                  controlled_settings_sha256=fingerprint(config), profile=identity)
        for row in report["results"]:
            row.update(configuration=deepcopy(config), configuration_sha256=fingerprint(config))
    assert compare_reports(left, right)["completed_verified_task_delta"] == 0
    right["metadata"]["configuration"]["token_budget"] += 1
    with pytest.raises(ValueError, match="fingerprint"):
        compare_reports(left, right)
    right["metadata"]["configuration_sha256"] = fingerprint(right["metadata"]["configuration"])
    for row in right["results"]:
        row.update(configuration=deepcopy(right["metadata"]["configuration"]), configuration_sha256=right["metadata"]["configuration_sha256"])
    with pytest.raises(ValueError, match="token_budget"):
        compare_reports(left, right)


def test_storage_json_markdown_redaction_and_strict_reading(evidence, tmp_path):
    value = deepcopy(evidence)
    value["label"] = "authorization: Bearer secret-token-123"
    paths = export_report(value, tmp_path / "result")
    restored = load_report(paths[0])
    assert restored["results"][0]["metrics"]["usage"]["cost"] is None
    assert "secret-token-123" not in paths[0].read_text() + paths[1].read_text()
    assert "Pass@1" in paths[1].read_text() and "Provenance" in paths[1].read_text()
    assert "unknown" in markdown_report(restored)
    for contents in ('{"schema_version": 1, "schema_version": 1}', '{"a": NaN}', '{bad'):
        malformed = tmp_path / "broken.json"
        malformed.write_text(contents)
        with pytest.raises(ValueError):
            read_json(malformed)
    with pytest.raises(ValueError, match="size"):
        read_json(paths[0], limit=10)


def test_cockpit_summary_recomputes_native_evidence_without_running_tools(evidence):
    from firik_agent.projects.cockpit import benchmark_report_summary
    value = deepcopy(evidence)
    value["summary"]["fixtures_passed"] = 999
    result = benchmark_report_summary(value)
    assert result["fixtures_passed"] == 4
    assert result["verified_tasks"] == 4
    assert result["cost"] is None
    assert value["summary"]["fixtures_passed"] == 999


@pytest.mark.parametrize("mutation", [
    lambda r: r["results"][0]["metrics"].update(latency_ms=["oops"]),
    lambda r: r["results"][0].update(completion_claimed="false"),
    lambda r: r["results"][0].update(failure_reason={}),
    lambda r: r["results"][0].update(safety_violations=-1),
    lambda r: r["results"][0]["trace"][0].update(metadata="invalid"),
])
def test_corrupt_analytics_fields_rejected(evidence, mutation):
    value = deepcopy(evidence)
    mutation(value)
    with pytest.raises(ValueError):
        validate_report(value)


def test_desktop_summary_import_is_passive(evidence, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from firik_agent.ui import coding_evidence
    from firik_agent.benchmarks.storage import export_report
    path, _ = export_report(evidence, tmp_path / "report")
    shown = []
    monkeypatch.setattr(coding_evidence.filedialog, "askopenfilename", lambda **_: str(path))
    monkeypatch.setattr(coding_evidence.messagebox, "showinfo", lambda *args: shown.append(args))
    monkeypatch.setattr(coding_evidence.messagebox, "showerror", lambda *args: pytest.fail(str(args)))
    coding_evidence.CodingEvidenceMixin._open_benchmark_summary(SimpleNamespace())
    assert shown and "4/4" in shown[0][1]
    assert "Bilinmiyor" in shown[0][1]
