"""Offline contract/lifecycle experiments; no provider is contacted."""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil

import pytest

from firik_agent.benchmarks.coding import coding_suite
from firik_agent.harness.comparison import (ComparativeConfiguration, ComparisonPlan, run_comparison,
                                            validate_comparison_report)
from firik_agent.harness.models import (HarnessCapability, HarnessCompletionEvidence, HarnessError, HarnessMetadata,
                                       HarnessEvent, HarnessResult, HarnessSession, HarnessUsage)
from firik_agent.harness.optimizer import recommend


@pytest.fixture(autouse=True)
def stable_test_source_identity(monkeypatch):
    # Parallel implementation work may edit unrelated installed sources while
    # these offline fixtures execute. Source-change rejection is tested below;
    # pin its host dependency here to isolate recommendation semantics.
    import firik_agent.benchmarks.runner as runner
    monkeypatch.setattr(runner, "source_fingerprint", lambda: "0" * 64)


def make_plan(tasks=1, repetitions=1, configurations=2, **kwargs):
    suite = coding_suite()
    suite = suite.model_copy(update={"tasks": suite.tasks[:tasks]})
    configs = [ComparativeConfiguration(id=f"config-{index}", provider="fixture", model="fixture-model",
                                        model_revision="immutable-v1") for index in range(configurations)]
    return ComparisonPlan(suite=suite, configurations=configs, repetitions=repetitions, **kwargs)


class FakeBackend:
    def __init__(self, configuration, root, task, plan, *, behavior="success", usage=None, model=None):
        self.configuration, self.root, self.task, self.plan = configuration, root, task, plan
        self.behavior, self.usage, self.model = behavior, usage or HarnessUsage(), model
        self.closed = self.cancelled = False
        self.policy = None

    def get_metadata(self):
        return HarnessMetadata(self.configuration.backend, "fixture-v1")

    def get_capabilities(self):
        return {name: HarnessCapability(name, name != "hard_token_budget", "Offline fake contract")
                for name in ("streaming", "cancellation", "sandboxing", "hard_token_budget")}

    def validate_configuration(self, policy, provider=None):
        self.policy = policy

    async def initialize_session(self, *, policy, provider=None):
        return HarnessSession(self.root.parent.name, self.configuration.backend, project_id=policy.project_id, workspace=str(self.root))

    async def execute_task(self, session, task):
        self.session, self.run_id = session, "run-" + session.id
        return self.run_id

    async def stream_events(self, session, run_id):
        event = HarnessEvent("tool_call_started", session.id, run_id, sequence=1, id="deduplicated", backend=self.configuration.backend)
        yield event
        yield event
        if self.behavior == "hang":
            await asyncio.Future()
        if self.behavior == "crash":
            raise ConnectionError("RPC disconnected after a partial frame")
        if self.behavior not in {"false-completion", "failed"}:
            for name, value in self.task.evaluation.expected_files.items():
                (self.root / name).write_text(value)
        yield HarnessEvent("done", session.id, run_id, sequence=2, backend=self.configuration.backend)

    async def inspect_run(self, session, run_id):
        status = "failed" if self.behavior in {"failed", "crash"} else "completed"
        errors = (HarnessError("rpc_disconnected", "A partial JSON frame was lost.", backend="native"),) if self.behavior == "crash" else ()
        return HarnessResult(run_id, session.id, "native", status=status,
            model=self.model or self.configuration.model, usage=self.usage,
            completion=HarnessCompletionEvidence(True), errors=errors,
            metadata={"model_revision": self.configuration.model_revision, "local_execution_verified": True,
                      "actual_providers": [self.configuration.provider]})

    async def request_cancellation(self, session, run_id):
        self.cancelled = True

    async def close_session(self, session):
        self.closed = True
        if self.behavior == "cleanup-failure":
            raise RuntimeError("External process could not be confirmed stopped")


def factory_capture(created, **kwargs):
    def factory(configuration, root, task, plan):
        backend = FakeBackend(configuration, root, task, plan, **kwargs)
        created.append(backend)
        return backend
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["backend", "workspace", "result", "event", "conflicting-duplicate", "event-overflow"])
async def test_comparison_identity_and_event_buffer_fail_closed(mismatch):
    from dataclasses import replace
    class MismatchedBackend(FakeBackend):
        def get_metadata(self):
            return HarnessMetadata("codex" if mismatch == "backend" else self.configuration.backend, "fixture-v1")

        async def initialize_session(self, **kwargs):
            session = await super().initialize_session(**kwargs)
            return replace(session, workspace=str(self.root.parent)) if mismatch == "workspace" else session

        async def inspect_run(self, *args):
            result = await super().inspect_run(*args)
            return replace(result, run_id="other-run") if mismatch == "result" else result

        async def stream_events(self, session, run_id):
            if mismatch == "event-overflow":
                for number in range(1000):
                    yield HarnessEvent("text_delta", session.id, run_id, number, backend=self.configuration.backend)
                return
            if mismatch == "conflicting-duplicate":
                yield HarnessEvent("text_delta", session.id, run_id, id="reused", content="one", backend=self.configuration.backend)
                yield HarnessEvent("text_delta", session.id, run_id, id="reused", content="different", backend=self.configuration.backend)
                return
            async for event in super().stream_events(session, run_id):
                yield replace(event, backend="codex") if mismatch == "event" else event
    created = []
    def factory(*args):
        backend = MismatchedBackend(*args)
        created.append(backend)
        return backend
    report = await run_comparison(make_plan(configurations=1, max_events=16), backend_factory=factory, allow_paid=True)
    validate_comparison_report(report)
    assert not report["rows"][0]["success"] and not report["rows"][0]["comparable"]
    assert len(report["rows"][0]["trace"]) <= 16
    assert not created[0].root.exists()


@pytest.mark.asyncio
async def test_evaluation_failure_does_not_skip_backend_or_workspace_cleanup(monkeypatch):
    import firik_agent.harness.comparison as module
    created = []
    def fail(*args):
        raise OSError("Independent evaluator could not read source")
    monkeypatch.setattr(module, "evaluate_sources", fail)
    report = await run_comparison(make_plan(configurations=1), backend_factory=factory_capture(created), allow_paid=True)
    validate_comparison_report(report)
    assert not report["rows"][0]["success"]
    assert any(error["code"] == "evaluation_failed" for error in report["rows"][0]["errors"])
    assert created[0].closed and not created[0].root.exists()


@pytest.mark.asyncio
async def test_recovered_tool_errors_and_recovery_rates_are_separate_from_terminal_errors():
    class Recovered(FakeBackend):
        async def stream_events(self, session, run_id):
            yield HarnessEvent("tool_call_error", session.id, run_id, sequence=0,
                metadata={"tool_call_id": "failed-once"}, backend=self.configuration.backend)
            yield HarnessEvent("tool_call_error", session.id, run_id, sequence=1,
                metadata={"tool_call_id": "failed-once"}, backend=self.configuration.backend)
            yield HarnessEvent("recovery", session.id, run_id, sequence=2, backend=self.configuration.backend)
            async for event in super().stream_events(session, run_id):
                yield event
    report = await run_comparison(make_plan(configurations=1), backend_factory=lambda *args: Recovered(*args), allow_paid=True)
    report = validate_comparison_report(report)
    row, summary = report["rows"][0], report["summary"][0]
    assert row["success"] and row["tool_errors"] == 1
    assert summary["tool_errors"] == 1 and summary["tool_error_rate"] == 1
    assert summary["terminal_error_rate"] == summary["error_rate"] == 0
    assert summary["trials_with_recovery_rate"] == 1
    assert "does not assert successful recovery" in report["metric_definitions"]["trials_with_recovery_rate"]


@pytest.mark.asyncio
async def test_comparison_pins_fixtures_isolates_sessions_deduplicates_and_closes():
    created = []
    report = await run_comparison(make_plan(), backend_factory=factory_capture(created), allow_paid=True)
    checked = validate_comparison_report(report)
    assert len({backend.root for backend in created}) == 2
    assert all(backend.closed and not backend.root.exists() for backend in created)
    assert all(row["success"] and row["tool_calls"] == 1 for row in report["rows"])
    assert all(row["usage"]["input_tokens"] is None and row["usage"]["cost_usd"] is None for row in report["rows"])
    assert all(summary["usage"]["cost_usd"]["total"] is None for summary in checked["summary"])
    assert all(backend.policy.permissions == report["plan"]["permissions"] for backend in created)
    assert report["rows"][0]["starting_revision"] == report["rows"][1]["starting_revision"]


@pytest.mark.asyncio
async def test_missing_backend_is_a_failed_trial_without_invented_model_or_usage():
    def missing(*args):
        raise HarnessError("executable_missing", "Pi executable is absent.", backend="pi")
    report = await run_comparison(make_plan(), backend_factory=missing, allow_paid=True)
    validate_comparison_report(report)
    assert all(row["status"] == "failed" and not row["success"] and not row["comparable"] for row in report["rows"])
    assert report["rows"][0]["errors"][0]["message"] == "Pi executable is absent."


@pytest.mark.asyncio
async def test_crash_preserves_backend_error_and_teardown():
    created = []
    report = await run_comparison(make_plan(configurations=1), backend_factory=factory_capture(created, behavior="crash"), allow_paid=True)
    validate_comparison_report(report)
    row = report["rows"][0]
    assert row["status"] == "failed" and not row["success"]
    assert any(error["code"] == "rpc_disconnected" for error in row["errors"])
    assert any("RPC disconnected" in error["message"] for error in row["errors"])
    assert created[0].closed and created[0].cancelled


@pytest.mark.asyncio
async def test_timeout_propagates_cancellation_and_closes_backend():
    created = []
    report = await run_comparison(make_plan(configurations=1, timeout_seconds=0.02),
        backend_factory=factory_capture(created, behavior="hang"), allow_paid=True)
    validate_comparison_report(report)
    assert report["rows"][0]["status"] == "timeout"
    assert created[0].cancelled and created[0].closed and not created[0].root.exists()


@pytest.mark.asyncio
async def test_caller_cancellation_closes_session_without_leaking_workspace():
    created = []
    task = asyncio.create_task(run_comparison(make_plan(configurations=1),
        backend_factory=factory_capture(created, behavior="hang"), allow_paid=True))
    while not created or not hasattr(created[0], "run_id"):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].closed and created[0].cancelled and not created[0].root.exists()


@pytest.mark.asyncio
async def test_uncertain_shutdown_retains_workspace_and_blocks_success():
    created = []
    report = await run_comparison(make_plan(configurations=1), backend_factory=factory_capture(created, behavior="cleanup-failure"), allow_paid=True)
    try:
        validate_comparison_report(report)
        row = report["rows"][0]
        assert row["cleanup"] == "retained_for_cleanup" and not row["success"]
        assert Path(row["retained_workspace"]).exists()
    finally:
        shutil.rmtree(created[0].root.parent)


@pytest.mark.asyncio
async def test_unknown_actual_model_cannot_be_a_matched_success():
    report = await run_comparison(make_plan(configurations=1),
        backend_factory=factory_capture([], model="different-model"), allow_paid=True)
    validate_comparison_report(report)
    assert not report["rows"][0]["comparable"] and not report["rows"][0]["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [{"actual_providers": ["fixture", "fallback"]},
                                       {"actual_providers": []},
                                       {"actual_models": ["different-model", "fixture-model"]}])
async def test_provider_or_model_fallback_cannot_establish_matched_evidence(observed):
    from dataclasses import replace
    class Fallback(FakeBackend):
        async def inspect_run(self, *args):
            result = await super().inspect_run(*args)
            return replace(result, metadata={**result.metadata, **observed})
    report = await run_comparison(make_plan(configurations=1), backend_factory=lambda *args: Fallback(*args), allow_paid=True)
    validate_comparison_report(report)
    assert not report["rows"][0]["comparable"] and not report["rows"][0]["success"]


@pytest.mark.asyncio
async def test_hard_token_budget_requires_positive_backend_capability():
    report = await run_comparison(make_plan(configurations=1, token_budget=100, hard_token_budget=True),
        backend_factory=factory_capture([]), allow_paid=True)
    validate_comparison_report(report)
    assert "cannot enforce" in report["rows"][0]["errors"][0]["message"]


@pytest.mark.asyncio
async def test_manifest_local_label_never_authorizes_model_requests():
    plan = make_plan(configurations=1)
    plan.configurations[0].local_only = True
    called = []
    with pytest.raises(PermissionError, match="explicit authorization"):
        await run_comparison(plan, backend_factory=lambda *args: called.append(args))
    assert called == []


@pytest.mark.parametrize("mutation", [
    lambda plan: plan["configurations"][1].update(model="different"),
    lambda plan: plan["configurations"][1].update(model_revision="different"),
    lambda plan: plan["configurations"][1].update(provider="different"),
    lambda plan: plan["configurations"][1].update(provider_settings={"temperature": 1}),
    lambda plan: plan["permissions"].update({"shell.safe": "allow"}),
    lambda plan: plan["configurations"][0].update(provider_settings={"api_key": "do-not-store"}),
    lambda plan: plan.update(hard_token_budget=True),
])
def test_invalid_or_unmatched_controls_rejected(mutation):
    plan = make_plan().model_dump()
    mutation(plan)
    with pytest.raises(ValueError):
        ComparisonPlan.model_validate(plan)


@pytest.mark.asyncio
async def test_native_adapter_comparison_uses_real_guarded_runtime_without_credentials(monkeypatch):
    import firik_agent.providers.registry as factory
    def forbidden(*args, **kwargs):
        raise AssertionError("The replay benchmark must not construct a credentialed provider")
    monkeypatch.setattr(factory, "get_api_key", forbidden)
    plan = make_plan(configurations=1)
    plan.configurations[0] = ComparativeConfiguration(id="native-replay", provider="replay", model="scripted-v1", model_revision="scripted-v1")
    report = await run_comparison(plan)
    validate_comparison_report(report)
    row = report["rows"][0]
    assert row["success"], row["errors"]
    assert row["tool_calls"] >= 4
    assert row["model_revision_verified"]
    recommendation = recommend(report)
    assert recommendation["recommendation"] is None and not recommendation["automatic_selection"]


@pytest.mark.asyncio
async def test_report_validation_recomputes_summary_and_rejects_corrupted_trials():
    import copy
    report = await run_comparison(make_plan(configurations=1), backend_factory=factory_capture([]), allow_paid=True)
    report["summary"][0]["success_rate"] = 1000
    assert validate_comparison_report(report)["summary"][0]["success_rate"] == 1
    mutations = [
        lambda item: item["rows"].append(item["rows"][0]),
        lambda item: item["rows"][0].update(success=False),
        lambda item: item["rows"][0].update(task_sha256="0" * 64),
        lambda item: item["rows"][0].update(actual_model_revision="changed"),
        lambda item: item["rows"][0]["usage"].update(cost_usd=-1),
        lambda item: item["rows"][0].update(duration_ms=float("nan")),
        lambda item: item["rows"][0].update(verified=1, completion_claimed=1),
    ]
    for mutation in mutations:
        corrupted = copy.deepcopy(report)
        mutation(corrupted)
        with pytest.raises(ValueError):
            validate_comparison_report(corrupted)


@pytest.mark.asyncio
async def test_optimizer_uses_success_thresholds_known_costs_and_fixed_verification():
    plan = make_plan(tasks=5, repetitions=3)
    def factory(configuration, root, task, plan):
        return FakeBackend(configuration, root, task, plan,
            usage=HarnessUsage(input_tokens=50, output_tokens=10, cost_usd=0.1 if configuration.id == "config-0" else 0.2, source="provider"))
    report = await run_comparison(plan, backend_factory=factory, allow_paid=True)
    for row in report["rows"]:
        row["duration_ms"] = 20.0 if row["configuration_id"] == "config-0" else 10.0
    assert recommend(report, "minimize_cost")["recommendation"]["configuration"]["id"] == "config-0"
    assert recommend(report, "minimize_latency")["recommendation"]["configuration"]["id"] == "config-1"
    assert recommend(report, "local_only")["recommendation"]
    for row in report["rows"]:
        if row["configuration_id"] == "config-0":
            row["usage"]["cost_usd"] = None
    assert recommend(report, "minimize_cost")["recommendation"]["configuration"]["id"] == "config-1"
    recommendation = recommend(report, "balanced")
    assert not recommendation["automatic_selection"] and not recommendation["applied"]
    assert recommendation["recommendation"]["configuration"]["verification_depth"] == "host-source-ast"


@pytest.mark.asyncio
async def test_false_completion_and_source_changes_disqualify_recommendations():
    report = await run_comparison(make_plan(configurations=1),
        backend_factory=factory_capture([], behavior="false-completion"), allow_paid=True)
    validate_comparison_report(report)
    assert report["rows"][0]["false_completion"] and not report["rows"][0]["success"]
    assert recommend(report, constraints={"minimum_tasks": 1, "minimum_repetitions": 1})["recommendation"] is None
    report = await run_comparison(make_plan(configurations=1), backend_factory=factory_capture([]), allow_paid=True)
    report["source_changed"] = True
    assert recommend(report, constraints={"minimum_tasks": 1, "minimum_repetitions": 1})["recommendation"] is None
    report["source_changed"] = False
    report["source_sha256"] = "1" * 64
    assert recommend(report, constraints={"minimum_tasks": 1, "minimum_repetitions": 1})["recommendation"] is None
