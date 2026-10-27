import asyncio

import pytest

from firik_agent.benchmarks.live import BudgetedProvider, LiveBudgetExceeded, run_live_suite
from firik_agent.benchmarks.models import HarnessConfiguration, ModelProviderIdentity
from firik_agent.core.types import StreamChunk
from firik_agent.providers.base import BaseProvider


class FakeLiveProvider(BaseProvider):
    def __init__(self, *, usage=True):
        super().__init__("fake-live", "https://model.invalid/v1", "known-secret-value", "test-model")
        self.calls = []
        self.usage = usage

    async def chat_stream(self, messages, **kwargs):
        self.calls.append(kwargs)
        for _ in range(2):
            yield StreamChunk(delta_content="done", usage={"prompt_tokens": 10, "completion_tokens": 2} if self.usage else None)

    async def chat(self, *args, **kwargs):
        raise AssertionError("Nonstream fallback forbidden")

    async def list_models(self):
        return []


def config(**kwargs):
    return HarnessConfiguration(model=ModelProviderIdentity(provider="fake-live", model="test-model"), **kwargs)


@pytest.mark.asyncio
async def test_live_requires_explicit_opt_in_before_any_configuration_or_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Live provider touched before authorization")
    monkeypatch.setattr("firik_agent.benchmarks.live.configured_provider", forbidden)
    with pytest.raises(PermissionError, match="allow-live"):
        await run_live_suite(configuration=config())


@pytest.mark.asyncio
async def test_live_wrapper_deduplicates_usage_and_bounds_requests_and_outputs():
    inner = FakeLiveProvider()
    bounded = BudgetedProvider(inner, config(request_budget=1, max_output_tokens=5))
    assert len([chunk async for chunk in bounded.chat_stream([])]) == 2
    assert bounded.tokens == 12
    assert bounded.unknown_usage_requests == 0
    assert inner.calls[0]["max_tokens"] == 5
    with pytest.raises(LiveBudgetExceeded, match="request"):
        _ = [chunk async for chunk in bounded.chat_stream([])]
    assert len(inner.calls) == 1


@pytest.mark.asyncio
async def test_live_wrapper_preserves_unknown_usage_and_enforces_reported_tokens():
    bounded = BudgetedProvider(FakeLiveProvider(usage=False), config(request_budget=1))
    _ = [chunk async for chunk in bounded.chat_stream([])]
    assert bounded.unknown_usage_requests == 1
    assert bounded.recorder.snapshot()["usage"]["total_tokens"] is None
    bounded = BudgetedProvider(FakeLiveProvider(), config(token_budget=11))
    with pytest.raises(LiveBudgetExceeded, match="during streaming"):
        _ = [chunk async for chunk in bounded.chat_stream([])]
    assert bounded.tokens == 12


@pytest.mark.asyncio
async def test_live_suite_rejects_fixture_revision_tampering_before_provider(monkeypatch):
    from firik_agent.benchmarks.coding import coding_suite

    suite = coding_suite()
    suite.tasks[0].repository.files["main.py"] = "tampered = True\n"
    def forbidden(*args, **kwargs):
        pytest.fail("Provider constructed for tampered fixture")
    monkeypatch.setattr("firik_agent.benchmarks.live.configured_provider", forbidden)
    with pytest.raises(ValueError, match="starting revision"):
        await run_live_suite(suite, configuration=config(), allow_live=True)


@pytest.mark.asyncio
async def test_live_suite_runs_injected_provider_and_keeps_cost_unknown():
    from firik_agent.benchmarks.coding import coding_suite

    suite = coding_suite()
    suite.tasks = suite.tasks[:1]
    report = await run_live_suite(suite, configuration=config(request_budget=2), allow_live=True,
                                  provider=FakeLiveProvider())
    assert len(report["results"]) == 1
    assert report["metadata"]["mode"] == "live"
    assert not report["results"][0]["verified"]
    assert report["results"][0]["metrics"]["usage"]["cost"] is None
    assert "known-secret-value" not in str(report)
    assert report["results"][0]["fixture_revision"] == suite.tasks[0].repository.starting_revision


@pytest.mark.asyncio
async def test_live_suite_can_complete_real_guarded_edit_with_native_evidence():
    from firik_agent.benchmarks.coding import coding_suite, task_manifest
    from firik_agent.benchmarks.manifests import ReplayTurn, TaskManifest
    from firik_agent.benchmarks.runner import ReplayProvider

    suite = coding_suite()
    suite.tasks = suite.tasks[:1]
    task = suite.tasks[0]
    manifest = task_manifest(task)
    step = {"id": "change", "title": "Fix inclusive bound", "acceptance_criteria": ["includes the upper bound"],
            "verification": "builtin:verify", "status": "in_progress"}
    turns = [
        {"calls": [{"name": "read_file", "arguments": {"path": "main.py"}},
                   {"name": "update_plan", "arguments": {"steps": [step]}}]},
        {"calls": [{"name": "edit_file", "arguments": {"path": "main.py", "expected_hash": "$hash:main.py",
            "old_text": task.repository.files["main.py"], "new_text": task.evaluation.expected_files["main.py"], "step_id": "change"}}]},
        {"calls": [{"name": "run_tests", "arguments": {"command": "builtin:verify"}}]},
        {"calls": [{"name": "update_plan", "arguments": {"steps": [{**step, "status": "completed", "evidence_ids": ["$evidence"]}]}}]},
        {"text": "Change verified."},
    ]
    script = TaskManifest.model_validate({**manifest.model_dump(), "turns": turns})
    provider = ReplayProvider(script)
    provider.name, provider.current_model = "fake-live", "test-model"
    report = await run_live_suite(suite, configuration=config(request_budget=8), allow_live=True, provider=provider)
    row = report["results"][0]
    assert row["runtime_status"] == "completed"
    assert row["verified"] and row["task_success"]
    assert row["verification"]["native_gate"]["evidence_ids"]
    assert any(event["type"] == "verification" and event["metadata"].get("executed") for event in row["trace"])


@pytest.mark.asyncio
async def test_live_mixed_sparse_usage_across_requests_enforces_lower_bound():
    class SparseProvider(FakeLiveProvider):
        async def chat_stream(self, messages, **kwargs):
            self.calls.append(kwargs)
            usage = {"total_tokens": 60} if len(self.calls) == 1 else {"prompt_tokens": 60}
            yield StreamChunk(delta_content="partial", usage=usage)
    inner = SparseProvider()
    bounded = BudgetedProvider(inner, config(token_budget=100, request_budget=3))
    _ = [chunk async for chunk in bounded.chat_stream([])]
    with pytest.raises(LiveBudgetExceeded, match="during streaming"):
        _ = [chunk async for chunk in bounded.chat_stream([])]
    assert bounded.tokens == 120
    with pytest.raises(LiveBudgetExceeded, match="token budget exhausted"):
        _ = [chunk async for chunk in bounded.chat_stream([])]
    assert len(inner.calls) == 2
