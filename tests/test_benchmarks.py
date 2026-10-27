"""Offline lab regression tests use real native tools/events/SQLite, no model API."""
from copy import deepcopy
import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from firik_agent.benchmarks.cli import main
from firik_agent.benchmarks.manifests import TaskManifest, builtin_tasks
from firik_agent.benchmarks.runner import (compare_reports, export_report, run_suite, run_trial,
                                           source_fingerprint)


def task(identifier):
    return next(value for value in builtin_tasks() if value.id == identifier)


@pytest.fixture(autouse=True)
def no_providers_or_shell(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline lab accessed credentials, provider configuration, shell or HTTP.")
    monkeypatch.setattr("firik_agent.providers.registry.ProviderRegistry.from_config", forbidden)
    monkeypatch.setattr("firik_agent.credentials._backend", forbidden)
    monkeypatch.setattr("firik_agent.core.agent.Agent.initialize", forbidden)
    monkeypatch.setattr("firik_agent.core.shell.run_shell", forbidden)
    monkeypatch.setattr("firik_agent.projects.coding_runtime.run_shell", forbidden)
    monkeypatch.setattr("httpx.AsyncClient", forbidden)


@pytest.mark.asyncio
async def test_builtin_suite_real_runtime_objective_checks_and_failure_labels():
    report = await run_suite(trials=1)
    assert report["summary"]["fixtures_passed"] == 7
    assert report["summary"]["completed_verified_tasks"] == 5
    assert report["summary"]["safety_violations"] == 0
    rows = {row["task_id"]: row for row in report["results"]}
    edited = rows["guarded-edit"]
    assert edited["runtime_status"] == "completed" and edited["verified"]
    assert edited["verification"]["native_gate"]["passed"]
    assert edited["verification"]["native_gate"]["evidence_ids"]
    checks = [event["metadata"] for event in edited["trace"] if event["type"] == "verification"]
    assert any(value.get("execution_backend") == "in_process_ast" and value.get("executed") for value in checks)
    assert edited["metrics"]["request_count"] == 5
    assert edited["metrics"]["usage"]["total_tokens"] == 120
    assert rows["denied-write"]["metrics"]["tool_errors"] == 1
    assert rows["path-traversal"]["metrics"]["tool_errors"] == 1
    for identifier, status, count in (("partial-stream", "failed", "failed_requests"),
                                      ("cancellation", "cancelled", "cancelled_requests")):
        row = rows[identifier]
        assert row["runtime_status"] == status and row["fixture_passed"]
        assert not row["task_success"] and not row["verified"]
        assert row["metrics"][count] == 1
        assert row["metrics"]["finished_requests"] == 1
        assert row["metrics"]["usage"]["total_tokens"] is None
    assert rows["unknown-usage"]["metrics"]["usage"]["total_tokens"] is None
    assert all(row["persistence_verified"] for row in rows.values())
    assert all(row["metrics"]["usage"]["cost"] is None for row in rows.values())


@pytest.mark.asyncio
async def test_repeatable_manifests_fixture_revisions_and_matched_comparison():
    report = await run_suite([task("read-only")], trials=2)
    assert report["results"][0]["fixture_revision"] == report["results"][1]["fixture_revision"]
    assert report["tasks"][0]["manifest_sha256"] == task("read-only").fingerprint()
    assert report["summary"]["wall_time_ms"]["stdev"] is not None
    assert len(report["metadata"]["source_sha256"]) == 64
    assert len(report["metadata"]["controlled_settings_sha256"]) == 64
    candidate = deepcopy(report)
    candidate["metadata"]["source_sha256"] = "different-implementation"
    candidate["metadata"]["repository"] = {"commit": "different", "dirty": True}
    compared = compare_reports(report, candidate)
    assert compared["paired_wall_time_delta_ms"]["mean"] == 0
    assert compared["performance_claim"] is None
    candidate["tasks"][0]["token_budget"] += 1
    with pytest.raises(ValueError, match="matched tasks"):
        compare_reports(report, candidate)
    candidate = deepcopy(report)
    candidate["metadata"]["controlled_settings_sha256"] = "different-settings"
    with pytest.raises(ValueError, match="matched settings"):
        compare_reports(report, candidate)
    candidate = deepcopy(report)
    candidate["results"].append(candidate["results"][0])
    with pytest.raises(ValueError, match="duplicate"):
        compare_reports(report, candidate)
    truncated = deepcopy(report)
    truncated["results"].pop()
    with pytest.raises(ValueError, match="every declared"):
        compare_reports(truncated, truncated)
    candidate = deepcopy(report)
    candidate["results"][0]["manifest_sha256"] = "wrong"
    with pytest.raises(ValueError, match="fingerprint"):
        compare_reports(report, candidate)
    candidate = deepcopy(report)
    candidate["summary"]["fixtures_passed"] = 5000
    assert compare_reports(report, candidate)["fixture_pass_delta"] == 0
    candidate["results"][0]["fixture_passed"] = 1
    with pytest.raises(ValueError, match="boolean"):
        compare_reports(report, candidate)
    candidate = deepcopy(report)
    del candidate["metadata"]["tool_revision"]
    with pytest.raises(ValueError, match="missing"):
        compare_reports(candidate, candidate)


@pytest.mark.parametrize("path", ["../outside.py", "/tmp/outside.py", "C:/outside.py", "a\\b.py", ".git/config", ".env", "credentials.json", "./main.py", "CON.py", "main.py."])
def test_manifest_rejects_unsafe_source_paths(path):
    data = task("read-only").model_dump()
    data["files"] = {path: "value = 1\n"}
    with pytest.raises(ValidationError):
        TaskManifest.model_validate(data)


def test_manifest_rejects_unbounded_nonfinite_and_executable_schema_fields():
    base = task("read-only").model_dump()
    for update in ({"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
                   {"evaluator_code": "import os"}, {"token_budget": True},
                   {"turns": [{"usage": {"total_tokens": -1}}]},
                   {"turns": [{"usage": {"total_tokens": True}}]},
                   {"turns": [{"calls": [{"name": "read_file", "arguments": {"value": "x" * 40_000}}]}]}):
        with pytest.raises(ValidationError):
            TaskManifest.model_validate({**base, **update})


@pytest.mark.asyncio
async def test_mutated_models_revalidated_before_workspace_creation(tmp_path):
    manifest = task("read-only")
    manifest.files["../outside.py"] = "unsafe"
    with pytest.raises(ValidationError):
        await run_trial(manifest)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,arguments", [("run_command", {"command": "touch malicious-marker"}),
    ("run_tests", {"command": "touch malicious-marker"}),
    ("mcp_unknown_shell", {"command": "touch malicious-marker"}),
    ("add_plugin", {"name": "malicious", "python_code": "raise SystemExit"})])
async def test_fixture_shell_mcp_plugins_are_denied_before_execution(name, arguments):
    data = task("denied-write").model_dump()
    data["turns"] = [{"calls": [{"name": name, "arguments": arguments}]}, {"text": "Denied."}]
    manifest = TaskManifest.model_validate(data)
    result = await run_trial(manifest)
    assert result["fixture_passed"] and result["metrics"]["tool_errors"] == 1
    assert result["safety_violations"] == 0


@pytest.mark.asyncio
async def test_fixture_python_is_parsed_as_data_never_imported_or_executed(tmp_path):
    marker = tmp_path / "must-not-exist"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
    data = task("unknown-usage").model_dump()
    data.update(files={"main.py": source}, expected_files={"main.py": source})
    result = await run_trial(TaskManifest.model_validate(data))
    assert result["fixture_passed"] and not marker.exists()


@pytest.mark.asyncio
async def test_replay_token_budget_stops_without_implicit_retry():
    data = task("read-only").model_dump()
    data.update(token_budget=24, expected_status="failed", expected_text="")
    result = await run_trial(TaskManifest.model_validate(data))
    assert result["fixture_passed"]
    assert result["metrics"]["request_count"] == 2
    assert result["metrics"]["failed_requests"] == 1
    assert result["metrics"]["recovery_attempts"] == 0
    assert not result["metrics"]["usage_complete"]
    assert result["metrics"]["reported_usage"]["total_tokens"] == 24


@pytest.mark.asyncio
async def test_sparse_usage_components_enforce_budget_and_unknown_is_explicit():
    data = task("read-only").model_dump()
    data.update(token_budget=24, expected_status="failed", expected_text="")
    for turn in data["turns"]:
        turn["usage"].pop("total_tokens")
    result = await run_trial(TaskManifest.model_validate(data))
    assert result["fixture_passed"] and result["metrics"]["failed_requests"] == 1
    assert result["token_budget"]["reported_tokens"] == 24
    result = await run_trial(task("unknown-usage"))
    assert result["token_budget"]["reported_tokens"] is None
    assert result["token_budget"]["unreported_requests"] == 1


@pytest.mark.asyncio
async def test_bounded_adversarial_ast_input_is_evaluator_failure_not_suite_crash():
    data = task("unknown-usage").model_dump()
    content = "+" * 10_000 + "1\n"
    data.update(files={"main.py": content}, expected_files={"main.py": content})
    result = await run_trial(TaskManifest.model_validate(data))
    assert not result["fixture_passed"] and not result["verification"]["source_match"]
    assert result["persistence_verified"]


@pytest.mark.asyncio
async def test_deadline_failure_is_not_mistaken_for_completed_task():
    data = task("unknown-usage").model_dump()
    data.update(timeout_seconds=1e-9, expected_status="failed", expected_text="")
    result = await run_trial(TaskManifest.model_validate(data))
    assert result["runtime_status"] == "failed"
    assert result["fixture_passed"] and not result["task_success"]


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_after_native_cleanup(monkeypatch):
    entered = asyncio.Event()
    async def waiting(self, messages, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        yield
    monkeypatch.setattr("firik_agent.benchmarks.runner.ReplayProvider.chat_stream", waiting)
    running = asyncio.create_task(run_trial(task("read-only")))
    await asyncio.wait_for(entered.wait(), 3)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.asyncio
async def test_redacted_reports_preserve_existing_files_and_symlink_targets(tmp_path):
    report = await run_suite([task("unknown-usage")], trials=1, label="api_key=supersecretfixture")
    output = tmp_path / "report"
    paths = export_report(report, output)
    assert "supersecretfixture" not in paths[0].read_text()
    assert "supersecretfixture" not in paths[1].read_text()
    parsed = json.loads(paths[0].read_text())
    assert parsed["results"][0]["metrics"]["usage"]["cost"] is None
    assert "synthetic usage" in paths[1].read_text()
    with pytest.raises(FileExistsError):
        export_report(report, output)
    original = tmp_path / "original"
    original.write_text("user evidence")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "report.md").symlink_to(original)
    with pytest.raises(FileExistsError):
        export_report(report, linked)
    assert original.read_text() == "user evidence"
    assert not (linked / "report.json").exists()


def test_cli_list_help_run_manifest_error_and_comparison(tmp_path, capsys):
    assert main(["list", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 7
    assert main(["run", "--task", "unknown", "--output", str(tmp_path / "unknown")]) == 2
    assert not (tmp_path / "unknown").exists()
    assert main(["run", "--task", "unknown-usage", "--trials", "1", "--output", str(tmp_path / "ok")]) == 0
    path = tmp_path / "ok" / "report.json"
    assert main(["compare", str(path), str(path), "--output", str(tmp_path / "comparison.json")]) == 0
    assert json.loads((tmp_path / "comparison.json").read_text())["performance_claim"] is None
    assert main(["run", "--task", "unknown-usage", "--trials", "1", "--output", str(tmp_path / "ok")]) == 2
    manifest = tmp_path / "task.json"
    manifest.write_text(task("unknown-usage").model_dump_json())
    assert main(["run", "--manifest", str(manifest), "--trials", "1", "--output", str(tmp_path / "custom")]) == 0


def test_rejected_suite_and_unsupported_comparison_shapes():
    for trials in (0, True, 101):
        with pytest.raises(ValueError):
            asyncio.run(run_suite(trials=trials))
    with pytest.raises(ValueError, match="Unsupported"):
        compare_reports({}, {})
    assert len(source_fingerprint()) == 64
