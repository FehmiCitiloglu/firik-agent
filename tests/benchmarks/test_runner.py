"""Native offline execution evidence; fixture programs never run."""
import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from firik_agent.benchmarks.fixtures import fixture_revision, offline_tasks
from firik_agent.benchmarks.manifests import TaskManifest
from firik_agent.benchmarks.models import HarnessConfiguration
from firik_agent.benchmarks.runner import run_suite, run_trial


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("Offline replay reached network, credentials, live initialization or shell")
    monkeypatch.setattr("httpx.AsyncClient", forbidden)
    monkeypatch.setattr("firik_agent.providers.registry.ProviderRegistry.from_config", forbidden)
    monkeypatch.setattr("firik_agent.credentials._backend", forbidden)
    monkeypatch.setattr("firik_agent.core.agent.Agent.initialize", forbidden)
    monkeypatch.setattr("firik_agent.projects.coding_runtime.run_shell", forbidden)


@pytest.mark.asyncio
async def test_expanded_offline_faults_have_objective_native_evaluators():
    report = await run_suite(offline_tasks(), trials=1)
    assert len(report["results"]) >= 12
    assert report["summary"]["fixtures_passed"] == len(report["results"])
    rows = {row["task_id"]: row for row in report["results"]}
    for name in ("incomplete-tool-stream", "rate-limit", "context-exhaustion", "timeout", "repeated-tool-loop"):
        assert rows[name]["runtime_status"] == "failed"
        assert not rows[name]["verified"]
    assert rows["interrupted-execution"]["runtime_status"] == "interrupted"
    assert rows["false-completion"]["completion_claimed"]
    assert not rows["false-completion"]["verification"]["source_match"]
    assert not rows["false-completion"]["task_success"]
    assert rows["streaming-usage-dedup"]["metrics"]["usage"]["total_tokens"] == 24
    assert rows["repeated-tool-loop"]["metrics"]["request_count"] == 3
    assert all(row["persistence_verified"] for row in rows.values())
    assert len({row["run_id"] for row in rows.values()}) == len(rows)
    assert all(row["metrics"]["usage"]["cost"] is None for row in rows.values())


@pytest.mark.asyncio
async def test_replay_and_git_revisions_stable_across_concurrent_trials():
    manifest = next(task for task in offline_tasks() if task.id == "guarded-edit")
    a, b = await asyncio.gather(run_trial(manifest, 1), run_trial(manifest, 2))
    assert a["fixture_passed"] and b["fixture_passed"]
    assert a["run_id"] != b["run_id"]
    assert a["provenance"]["workspace_id"] != b["provenance"]["workspace_id"]
    assert a["fixture_revision"] == b["fixture_revision"] == fixture_revision(manifest.files)
    assert a["metrics"]["usage"] == b["metrics"]["usage"]
    assert a["configuration_sha256"] == b["configuration_sha256"]
    assert a["verification"]["expected_sha256"] == b["verification"]["expected_sha256"]


@pytest.mark.asyncio
async def test_request_budget_is_host_control_and_profile_is_auditable():
    task = next(task for task in offline_tasks() if task.id == "read-only")
    configuration = HarnessConfiguration(id="bounded-candidate", request_budget=1)
    row = await run_trial(task, configuration=configuration)
    assert row["runtime_status"] == "failed"
    assert row["terminal_outcome"]["reason"] == "request_budget_exhausted"
    assert row["metrics"]["request_count"] == 1
    assert row["configuration"]["id"] == "bounded-candidate"
    assert row["configuration_sha256"] == configuration.fingerprint()


@pytest.mark.parametrize("files", [{"a.py": "", "a.py/b.py": ""}, {"A.py": "", "a.py/b.py": ""}])
def test_invalid_file_directory_collisions_rejected_before_run(files):
    data = offline_tasks()[0].model_dump()
    with pytest.raises(ValidationError, match="conflict"):
        TaskManifest.model_validate({**data, "files": files})
