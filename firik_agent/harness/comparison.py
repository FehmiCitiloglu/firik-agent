"""Paired harness experiments over the Lab's inert, versioned source fixtures.

The evaluator and starting revision are the existing Harness Benchmark Lab's.
Factories are trusted host dependencies, never import paths from a manifest.
Every configuration receives a new repository; no candidate is applied to the
user's checkout. Unknown billing/usage and unverified model revisions stay unknown.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import stat
import statistics
import tempfile
import time
from typing import Any, Callable, Literal, Mapping
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from firik_agent.benchmarks.evaluator import evaluate_sources
from firik_agent.benchmarks.fixtures import fixture_revision
from firik_agent.benchmarks.manifests import EVALUATOR_VERSION, StrictModel
from firik_agent.benchmarks.models import BenchmarkSuite, TaskSpecification, fingerprint
from firik_agent.projects.coding_sessions import redact_sensitive


def _remove_workspace(directory: Path) -> None:
    def retry_readonly(function, path, error):
        if os.name != "nt" or not isinstance(error[1], PermissionError):
            raise error[1]
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise error[1]
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)

    shutil.rmtree(directory, onerror=retry_readonly)


class ComparativeConfiguration(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    backend: Literal["native", "pi", "codex"] = "native"
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(min_length=1, max_length=200)
    profile: str = Field(default="coding", min_length=1, max_length=100)
    context_strategy: Literal["profile"] = "profile"
    tool_configuration: Literal["guarded-source"] = "guarded-source"
    verification_depth: Literal["host-source-ast"] = "host-source-ast"
    concurrency: Literal[1] = 1
    provider_settings: dict[str, Any] = Field(default_factory=dict)
    local_only: bool = False

    def fingerprint(self):
        return fingerprint(self)

    @field_validator("provider_settings")
    @classmethod
    def nonsecret_settings(cls, value):
        # Authentication is injected by the host. Reports/manifests may contain
        # only non-secret controls which can meaningfully be matched.
        supported = {"temperature", "max_output_tokens", "reasoning_effort", "endpoint_sha256"}
        if set(value) - supported:
            raise ValueError("Provider settings permit only temperature, max_output_tokens, reasoning_effort and endpoint_sha256; inject credentials separately.")
        encoded = json.dumps(value, allow_nan=False)
        if len(encoded) > 4096:
            raise ValueError("Provider settings exceed the bounded comparison limit.")
        if "temperature" in value and (type(value["temperature"]) not in (int, float) or not 0 <= value["temperature"] <= 2):
            raise ValueError("Invalid comparison temperature.")
        if "max_output_tokens" in value and (type(value["max_output_tokens"]) is not int or not 1 <= value["max_output_tokens"] <= 32768):
            raise ValueError("Invalid comparison output budget.")
        if "reasoning_effort" in value and (not isinstance(value["reasoning_effort"], str) or len(value["reasoning_effort"]) > 100):
            raise ValueError("Invalid reasoning effort.")
        if "endpoint_sha256" in value and (not isinstance(value["endpoint_sha256"], str) or len(value["endpoint_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in value["endpoint_sha256"])):
            raise ValueError("Endpoint identity must be a SHA-256, not an endpoint with credentials.")
        return value


class ComparisonPlan(StrictModel):
    schema_version: Literal[1] = 1
    suite: BenchmarkSuite
    configurations: list[ComparativeConfiguration] = Field(min_length=1, max_length=8)
    repetitions: int = Field(default=3, ge=1, le=100)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    token_budget: int | None = Field(default=None, ge=1, le=1_000_000)
    hard_token_budget: bool = False
    tool_mode: Literal["none", "native"] = "native"
    allow_network: Literal[False] = False
    require_sandbox: bool = False
    permissions: dict[str, str] = Field(default_factory=lambda: {
        "filesystem.read": "allow", "filesystem.write": "allow", "filesystem.delete": "deny",
        "shell.safe": "ask", "shell.network": "deny", "network": "deny", "mcp": "deny", "spawn_agents": "deny"})
    max_events: int = Field(default=1024, ge=16, le=10000)

    def fingerprint(self):
        return fingerprint(self)

    @model_validator(mode="after")
    def matched_controls(self):
        identifiers = [configuration.id for configuration in self.configurations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Comparison configuration identities must be unique.")
        identities = {(configuration.provider, configuration.model, configuration.model_revision,
                       fingerprint(configuration.provider_settings)) for configuration in self.configurations}
        if len(identities) != 1:
            raise ValueError("A paired comparison requires the same provider, model, revision and provider settings.")
        if self.hard_token_budget and self.token_budget is None:
            raise ValueError("A hard token budget requires an explicit limit.")
        supported_permissions = {"filesystem.read", "filesystem.write", "filesystem.delete", "shell.safe",
                                 "shell.network", "shell.sudo", "network", "git.read", "git.commit", "git.push",
                                 "git.forcePush", "mcp", "secrets", "spawn_agents"}
        if set(self.permissions) - supported_permissions or any(value not in {"allow", "ask", "deny"} for value in self.permissions.values()):
            raise ValueError("Comparison permissions contain unsupported operations or values.")
        # These fixtures are inert input, never a source of executable commands.
        if any(self.permissions.get(name, "deny") != "deny" for name in
               ("shell.network", "shell.sudo", "network", "mcp", "secrets", "spawn_agents", "git.commit", "git.push", "git.forcePush")):
            raise ValueError("The source evaluator comparison cannot authorize shell, MCP, secrets, subagents or network tools.")
        if self.permissions.get("shell.safe", "deny") not in {"ask", "deny"}:
            raise ValueError("Source-evaluator operations require individual approval; unrestricted shell permission is unsupported.")
        for task in self.suite.tasks:
            if task.evaluation.version != EVALUATOR_VERSION:
                raise ValueError("Unsupported comparison evaluator.")
            if fixture_revision(task.repository.files) != task.repository.starting_revision:
                raise ValueError("Task starting revision disagrees with the exact fixture bytes.")
        return self


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("Expected a structured harness record.")


def _supported(backend, name: str) -> bool:
    capabilities = backend.get_capabilities()
    capability = capabilities.get(name, capabilities.get("supports_" + name))
    if capability is None:
        return False
    return (capability.get("supported") if isinstance(capability, Mapping) else getattr(capability, "supported", None)) is True


def _safe_event(event: Any) -> dict:
    record = redact_sensitive(_plain(event))
    if isinstance(record.get("content"), str):
        record["content"] = record["content"][:8192]
    # Observability is bounded independently of external output. A truncated
    # metadata frame remains explicitly a preview and is never usage authority.
    if len(json.dumps(record, default=str, allow_nan=False)) > 32768:
        record["metadata"] = {"truncated": True}
    return record


def _tool_error_count(trace: list[dict]) -> int:
    """Count failed tool invocations once, independently of terminal errors."""
    identities = set()
    for event in trace:
        if event.get("type") != "tool_call_error":
            continue
        metadata = event.get("metadata") or {}
        invocation = metadata.get("tool_invocation") or {}
        identity = metadata.get("tool_call_id") or invocation.get("id") or event.get("id")
        identities.add(str(identity))
    return len(identities)


def _fixture(root: Path, task: TaskSpecification) -> str:
    from firik_agent.benchmarks.runner import _git
    root.mkdir()
    for name, content in task.repository.files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    _git(root, "init", "--quiet", "--template=", "--object-format=sha1")
    _git(root, "add", "--all")
    _git(root, "commit", "--quiet", "--allow-empty", "-m", "Deterministic fixture")
    revision = _git(root, "rev-parse", "HEAD")
    if revision != task.repository.starting_revision:
        raise ValueError("Created repository does not match its pinned starting revision.")
    return revision


async def _trial(plan, configuration, task, repetition, backend_factory):
    from firik_agent.harness.models import HarnessExecutionPolicy, HarnessTask

    directory = Path(tempfile.mkdtemp(prefix="firik-harness-comparison-"))
    root = directory / "repository"
    started = time.monotonic()
    backend = session = run_id = None
    trace, seen, errors = [], {}, []
    result, status, verification = {}, "failed", {"passed": False, "detail": "Execution did not reach evaluation."}
    cleanup_ok, comparable, model_revision_verified = True, False, False
    retained = None
    metadata = {}
    identity_valid, event_overflow, caller_cancelled = True, False, False
    task_hash = task.fingerprint()
    evaluator_hash = fingerprint(task.evaluation)
    try:
        # Short, bounded Git fixture setup is completed before exposing the
        # workspace to a backend; cancellation cannot leave a writer thread.
        revision = _fixture(root, task)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    policy = HarnessExecutionPolicy(workspace=str(root), project_id="harness-comparison", tool_mode=plan.tool_mode,
        allow_network=plan.allow_network, require_sandbox=plan.require_sandbox, timeout_seconds=plan.timeout_seconds,
        max_events=plan.max_events, max_sessions=1, token_budget=plan.token_budget, permissions=dict(plan.permissions))
    provider = {"name": configuration.provider, "provider": configuration.provider, "model": configuration.model, **configuration.provider_settings}

    async def execute():
        nonlocal backend, session, run_id, result, status, comparable, model_revision_verified, metadata, identity_valid, event_overflow
        candidate = backend_factory(configuration, root, task, plan)
        backend = await candidate if inspect.isawaitable(candidate) else candidate
        metadata = redact_sensitive(_plain(backend.get_metadata()))
        if metadata.get("name") != configuration.backend:
            identity_valid = False
            raise ValueError("Actual backend identity differs from the comparison configuration.")
        for capability in ("streaming", "cancellation"):
            if not _supported(backend, capability):
                raise ValueError(f"Backend does not verify the required {capability} capability.")
        if plan.hard_token_budget and not _supported(backend, "hard_token_budget"):
            raise ValueError("Selected backend cannot enforce the requested hard token budget.")
        if plan.require_sandbox and not _supported(backend, "sandboxing"):
            raise ValueError("Selected backend does not verify the required sandbox policy.")
        backend.validate_configuration(policy, provider)
        session = await backend.initialize_session(policy=policy, provider=provider)
        session_record = _plain(session)
        if (session_record.get("backend") != configuration.backend or session_record.get("project_id") != policy.project_id
                or not isinstance(session_record.get("id"), str) or not session_record["id"]
                or not isinstance(session_record.get("workspace"), str) or Path(session_record["workspace"]).resolve() != root.resolve()):
            identity_valid = False
            raise ValueError("Backend session/project/workspace identity differs from the controlled policy.")
        harness_task = HarnessTask(prompt=task.prompt, model=configuration.model, profile=configuration.profile,
            metadata={"comparison_configuration": configuration.id, "task_id": task.id,
                      "model_revision": configuration.model_revision})
        run_id = await backend.execute_task(session, harness_task)
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 256:
            identity_valid = False
            raise ValueError("Backend run identity is invalid.")
        async for event in backend.stream_events(session, run_id):
            item = _safe_event(event)
            if (item.get("backend") != configuration.backend or item.get("session_id") != session.id
                    or item.get("run_id") != run_id):
                identity_valid = False
                raise ValueError("An event belongs to a different backend/session/run.")
            identity = item.get("id") or (item.get("backend"), item.get("session_id"), item.get("run_id"), item.get("sequence"))
            digest = fingerprint(item)
            if identity in seen:
                if seen[identity] != digest:
                    identity_valid = False
                    raise ValueError("Duplicate event identity contains conflicting execution data.")
                continue
            if len(trace) >= plan.max_events:
                event_overflow = True
                raise ValueError("Trial exceeded the bounded event history; comparison stopped without silently losing evidence.")
            seen[identity] = digest
            trace.append(item)
        result = redact_sensitive(_plain(await backend.inspect_run(session, run_id)))
        if (result.get("backend") != configuration.backend or result.get("session_id") != session.id
                or result.get("run_id") != run_id):
            identity_valid = False
            raise ValueError("Final result belongs to a different backend/session/run.")
        status = result.get("status", "failed")
        if status not in {"completed", "failed", "blocked", "cancelled", "timeout", "interrupted", "crashed"}:
            status = "failed"
            errors.append({"code": "invalid_status", "message": "Backend returned an unsupported terminal status."})
        actual = result.get("model")
        comparable = actual == configuration.model
        if actual != configuration.model:
            comparable = False
            status = "failed"
            errors.append({"code": "model_identity_mismatch", "message": "Actual backend model is unknown or differs from the requested model.", "actual_model": actual})
        actual_revision = result.get("metadata", {}).get("model_revision")
        if actual_revision is not None and actual_revision != configuration.model_revision:
            comparable = False
            status = "failed"
            errors.append({"code": "model_revision_mismatch", "message": "Actual backend model revision differs from the controlled revision."})
        model_revision_verified = actual_revision == configuration.model_revision

    try:
        await asyncio.wait_for(execute(), timeout=plan.timeout_seconds)
    except asyncio.TimeoutError:
        status = "timeout"
        errors.append({"code": "timeout", "message": "Comparison trial exceeded its common wall time budget."})
    except asyncio.CancelledError:
        caller_cancelled, status = True, "cancelled"
    except Exception as error:
        # Preserve the meaningful backend message and normalized type, while
        # using the existing journal redactor for any credential-like values.
        errors.append(redact_sensitive(error.to_dict()) if hasattr(error, "to_dict") else
                      {"code": type(error).__name__, "message": redact_sensitive(str(error))})
    finally:
        async def finalize():
            nonlocal result, cleanup_ok, verification, retained, identity_valid
            if not result and backend is not None and session is not None and run_id is not None:
                try:
                    result = redact_sensitive(_plain(await asyncio.wait_for(backend.inspect_run(session, run_id), timeout=2)))
                    if (result.get("backend") != configuration.backend or result.get("session_id") != session.id
                            or result.get("run_id") != run_id):
                        identity_valid = False
                except Exception as error:
                    errors.append({"code": "inspection_failed", "message": redact_sensitive(str(error))})
            if backend is not None and session is not None:
                if status in {"timeout", "failed", "cancelled"} and run_id is not None:
                    try:
                        await asyncio.wait_for(backend.request_cancellation(session, run_id), timeout=5)
                    except Exception as error:
                        errors.append({"code": "cancellation_failed", "message": redact_sensitive(str(error))})
                try:
                    await asyncio.wait_for(backend.close_session(session), timeout=5)
                except Exception as error:
                    cleanup_ok = False
                    errors.append({"code": "cleanup_failed", "message": redact_sensitive(str(error))})
            try:
                passed, detail = await asyncio.to_thread(evaluate_sources, root, task.evaluation.expected_files)
            except Exception as error:
                passed, detail = False, "Independent evaluator failed: " + type(error).__name__
                errors.append({"code": "evaluation_failed", "message": redact_sensitive(str(error))})
            verification = {"passed": passed, "detail": detail, "evaluator": EVALUATOR_VERSION,
                            "evaluator_sha256": evaluator_hash, "independent": True}
            if cleanup_ok:
                try:
                    await asyncio.to_thread(_remove_workspace, directory)
                except Exception as error:
                    cleanup_ok = False
                    errors.append({"code": "workspace_cleanup_failed", "message": redact_sensitive(str(error))})
            if not cleanup_ok:
                retained = str(directory)
        # Repeated caller cancellation must not interrupt backend disposal or
        # turn a failed evaluator into a leaked process/workspace.
        cleanup_task = asyncio.create_task(finalize())
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                caller_cancelled = True
        await cleanup_task

    if caller_cancelled:
        raise asyncio.CancelledError()

    actual_revision = result.get("metadata", {}).get("model_revision")
    actual_models = result.get("metadata", {}).get("actual_models", [])
    actual_providers = result.get("metadata", {}).get("actual_providers", [])
    comparable = (result.get("model") == configuration.model and actual_revision in {None, configuration.model_revision}
                  and isinstance(actual_models, list) and all(model == configuration.model for model in actual_models)
                  and isinstance(actual_providers, list) and bool(actual_providers)
                  and all(provider_name == configuration.provider for provider_name in actual_providers)
                  and identity_valid and not event_overflow)
    model_revision_verified = actual_revision == configuration.model_revision
    completion = result.get("completion") or {}
    completion_claimed = status == "completed"
    success = completion_claimed and verification["passed"] and comparable and cleanup_ok
    usage = result.get("usage") or {}
    types = [event.get("type", "") for event in trace]
    diagnostic_errors = []
    diagnostic_ids = set()
    for error in errors + (result.get("errors") or []):
        identity = fingerprint(error)
        if identity not in diagnostic_ids:
            diagnostic_ids.add(identity)
            diagnostic_errors.append(error)
    return {"configuration_id": configuration.id, "configuration_sha256": configuration.fingerprint(),
        "task_id": task.id, "task_sha256": task_hash, "repetition": repetition,
        "trial_id": uuid4().hex, "session_id": _plain(session).get("id") if session else None,
        "run_id": run_id, "starting_revision": revision, "evaluator_sha256": evaluator_hash,
        "status": status, "success": success, "completion_claimed": completion_claimed,
        "false_completion": completion_claimed and not verification["passed"],
        "backend_verified": completion.get("verified") is True,
        "comparable": comparable, "actual_model": result.get("model"),
        "backend_metadata": metadata, "identity_valid": identity_valid,
        "actual_model_revision": actual_revision,
        "actual_models": actual_models,
        "actual_providers": actual_providers,
        "model_revision_verified": model_revision_verified,
        "local_execution_verified": configuration.provider == "replay" or result.get("metadata", {}).get("local_execution_verified") is True,
        "duration_ms": (time.monotonic() - started) * 1000,
        "usage": usage, "tool_calls": sum(name in {"tool_call_started", "tool_started", "tool.start"} for name in types),
        "tool_errors": _tool_error_count(trace),
        "recoveries": sum(name in {"recovery", "recovery_event"} for name in types),
        "error_count": len(diagnostic_errors),
        "errors": diagnostic_errors, "verification": verification,
        "trace": trace, "trace_truncated": event_overflow,
        "cleanup": "closed" if cleanup_ok else "retained_for_cleanup", "retained_workspace": retained,
        "token_enforcement": "hard" if plan.hard_token_budget else "reported_only" if plan.token_budget is not None else "no_limit_requested"}


async def run_comparison(plan: ComparisonPlan | dict, *, backend_factory: Callable | None = None,
                         allow_paid: bool = False) -> dict:
    """Execute each task/configuration in its own pinned fixture repository.

    A non-replay provider always needs explicit authorization, even if an
    untrusted manifest labels it local or free. The default host factory only
    constructs a credential-free, synthetic native replay; live callers must
    inject a factory bound to authorized, configured providers.
    """
    from firik_agent.benchmarks.runner import source_fingerprint
    plan = ComparisonPlan.model_validate(plan.model_dump() if hasattr(plan, "model_dump") else plan)
    if any(configuration.provider != "replay" for configuration in plan.configurations) and not allow_paid:
        raise PermissionError("Comparative model requests require explicit authorization covering every selected configuration.")
    if backend_factory is None:
        backend_factory = offline_native_factory
    source_start = source_fingerprint()
    rows = []
    for repetition in range(1, plan.repetitions + 1):
        for task in plan.suite.tasks:
            for configuration in plan.configurations:
                rows.append(await _trial(plan, configuration, task, repetition, backend_factory))
    report = {"schema_version": 1, "kind": "harness-backend-comparison", "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(), "plan": plan.model_dump(mode="json"),
        "plan_sha256": plan.fingerprint(), "suite_sha256": plan.suite.fingerprint(),
        "source_sha256": source_start, "source_changed": source_start != source_fingerprint(),
        "rows": rows,
        "metric_definitions": {
            "error_rate": "Alias of terminal_error_rate: trials containing normalized runtime/startup/cleanup diagnostics divided by all trials; recovered tool failures are separate.",
            "terminal_error_rate": "Trials with one or more normalized terminal/runtime diagnostics divided by all trials.",
            "tool_errors": "Distinct failed tool invocation identities in the retained normalized trace.",
            "tool_error_rate": "Distinct failed tool invocations divided by observed tool calls; unknown when no tool calls were observed.",
            "trials_with_recovery_rate": "Trials containing one or more recovery events divided by all trials; does not assert successful recovery."},
        "limitations": ["Exact source/AST evaluation measures small canonical transformations, not production behavior.",
            "Configured model revisions are declared controls; revision verification is reported per trial.",
            "Reported token limits cannot undo billed usage; hard limits require an affirmative backend capability.",
            "Temporary repositories isolate artifacts; they are not an OS sandbox.",
            "Synthetic replay results are runtime regression evidence, not model quality or paid-provider performance."]}
    report["summary"] = summarize_comparison(report)
    return redact_sensitive(report)


def summarize_comparison(report: dict) -> list[dict]:
    """Recompute summaries from outcomes, retaining coverage for nullable data."""
    plan = ComparisonPlan.model_validate(report["plan"])
    summaries = []
    for configuration in plan.configurations:
        rows = [row for row in report["rows"] if row["configuration_id"] == configuration.id]
        count = len(rows)
        durations = [row["duration_ms"] for row in rows]
        usage = {}
        for field in ("input_tokens", "output_tokens", "cached_tokens", "cost_usd"):
            values = [row.get("usage", {}).get(field) for row in rows]
            observed = [value for value in values if value is not None]
            usage[field] = {"total": sum(observed) if observed else None, "coverage": len(observed),
                            "complete": len(observed) == count and count > 0}
        success = sum(row["success"] for row in rows)
        tool_calls = sum(row["tool_calls"] for row in rows)
        tool_errors = sum(row["tool_errors"] for row in rows)
        terminal_error_trials = sum(row["error_count"] > 0 for row in rows)
        summaries.append({"configuration_id": configuration.id, "backend": configuration.backend,
            "model": configuration.model, "profile": configuration.profile, "trials": count,
            "tasks": len({row["task_id"] for row in rows}), "verified_successes": success,
            "success_rate": success / count if count else None,
            "average_duration_ms": statistics.mean(durations) if durations else None,
            "usage": usage, "tool_calls": tool_calls, "tool_errors": tool_errors,
            "tool_error_rate": tool_errors / tool_calls if tool_calls else None,
            "errors": sum(row["error_count"] for row in rows),
            "terminal_error_trials": terminal_error_trials,
            "terminal_error_rate": terminal_error_trials / count if count else None,
            "error_rate": terminal_error_trials / count if count else None,
            "recoveries": sum(row["recoveries"] for row in rows),
            "trials_with_recovery_rate": sum(row["recoveries"] > 0 for row in rows) / count if count else None,
            "false_completions": sum(row["false_completion"] for row in rows),
            "comparable": bool(rows) and all(row["comparable"] and row["cleanup"] == "closed" for row in rows) and not report.get("source_changed", False),
            "revision_verified": bool(rows) and all(row["model_revision_verified"] for row in rows),
            "confidence": "descriptive-only" if len({row["task_id"] for row in rows}) >= 5 else "insufficient-task-diversity",
            "cost_per_success_usd": usage["cost_usd"]["total"] / success if success and usage["cost_usd"]["complete"] else None})
    return summaries


def validate_comparison_report(report: dict) -> dict:
    """Validate stored evidence before desktop display or recommendations.

    This checks internal provenance, not cryptographic authenticity. A report
    file remains untrusted input and cannot authorize execution or switching.
    """
    try:
        if report.get("schema_version") != 1 or report.get("kind") != "harness-backend-comparison":
            raise ValueError("Unsupported backend comparison report.")
        plan = ComparisonPlan.model_validate(report["plan"])
        if plan.fingerprint() != report.get("plan_sha256") or plan.suite.fingerprint() != report.get("suite_sha256"):
            raise ValueError("Comparison plan or suite fingerprint disagrees with its evidence.")
        if type(report.get("source_changed")) is not bool:
            raise ValueError("Missing source mutation evidence.")
        source_hash = report.get("source_sha256")
        if not isinstance(source_hash, str) or len(source_hash) != 64 or any(character not in "0123456789abcdef" for character in source_hash):
            raise ValueError("Invalid installed-source fingerprint.")
        expected = {(configuration.id, task.id, repetition) for configuration in plan.configurations
                    for task in plan.suite.tasks for repetition in range(1, plan.repetitions + 1)}
        if not isinstance(report.get("rows"), list) or len(report["rows"]) != len(expected):
            raise ValueError("Comparison report is missing declared trials.")
        configurations = {configuration.id: configuration for configuration in plan.configurations}
        tasks = {task.id: task for task in plan.suite.tasks}
        seen = set()
        for row in report["rows"]:
            identity = row["configuration_id"], row["task_id"], row["repetition"]
            if identity not in expected or identity in seen or type(row["repetition"]) is not int:
                raise ValueError("Duplicate or undeclared comparison trial.")
            seen.add(identity)
            configuration, task = configurations[identity[0]], tasks[identity[1]]
            if row.get("configuration_sha256") != configuration.fingerprint() or row.get("task_sha256") != task.fingerprint():
                raise ValueError("Trial fingerprints disagree with the controlled task/configuration.")
            if row.get("starting_revision") != task.repository.starting_revision or row.get("evaluator_sha256") != fingerprint(task.evaluation):
                raise ValueError("Trial repository/evaluator evidence differs from its plan.")
            for field in ("success", "completion_claimed", "false_completion", "backend_verified", "comparable", "model_revision_verified", "trace_truncated", "identity_valid", "local_execution_verified"):
                if type(row.get(field)) is not bool:
                    raise ValueError("Comparison outcomes must be strict booleans.")
            duration = row.get("duration_ms")
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                raise ValueError("Invalid comparison duration.")
            for field in ("tool_calls", "tool_errors", "recoveries", "error_count"):
                if type(row.get(field)) is not int or row[field] < 0:
                    raise ValueError("Invalid comparison metric count.")
            if row["status"] not in {"completed", "failed", "blocked", "cancelled", "timeout", "interrupted", "crashed"}:
                raise ValueError("Unsupported comparison status.")
            verification = row["verification"]
            if type(verification.get("passed")) is not bool or verification.get("evaluator_sha256") != fingerprint(task.evaluation):
                raise ValueError("Invalid independent verification evidence.")
            claimed = row["status"] == "completed"
            if row["completion_claimed"] != claimed or row["false_completion"] != (claimed and not verification["passed"]):
                raise ValueError("Completion evidence is inconsistent.")
            success = claimed and verification["passed"] and row["comparable"] and row["cleanup"] == "closed"
            if row["success"] != success or (row["comparable"] and row.get("actual_model") != configuration.model):
                raise ValueError("Verified success disagrees with runtime/model evidence.")
            if row["model_revision_verified"] != (row.get("actual_model_revision") == configuration.model_revision):
                raise ValueError("Model revision verification disagrees with the observed revision.")
            models = row.get("actual_models", [])
            if not isinstance(models, list) or len(models) > 128 or any(not isinstance(model, str) for model in models):
                raise ValueError("Invalid observed model identities.")
            if row["comparable"] and any(model != configuration.model for model in models):
                raise ValueError("A backend changed models during the controlled comparison.")
            providers = row.get("actual_providers", [])
            if not isinstance(providers, list) or len(providers) > 128 or any(not isinstance(provider_name, str) for provider_name in providers):
                raise ValueError("Invalid observed provider identities.")
            if row["comparable"] and (not providers or any(provider_name != configuration.provider for provider_name in providers)):
                raise ValueError("The actual model provider is unknown or changed during the controlled comparison.")
            if row["comparable"] and (row.get("actual_model_revision") not in {None, configuration.model_revision}
                    or not row["identity_valid"] or row["trace_truncated"]
                    or row.get("backend_metadata", {}).get("name") != configuration.backend):
                raise ValueError("Backend/revision identity or complete trace evidence is invalid.")
            usage = row.get("usage")
            if not isinstance(usage, dict):
                raise ValueError("Usage evidence must be structured, with unknown values retained.")
            for field in ("input_tokens", "output_tokens", "cached_tokens", "cost_usd"):
                value = usage.get(field)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError("Invalid usage measurement.")
            if not isinstance(row.get("trace"), list) or len(row["trace"]) > plan.max_events:
                raise ValueError("Invalid or unbounded execution trace.")
            if row["tool_errors"] != _tool_error_count(row["trace"]):
                raise ValueError("Failed tool counts disagree with invocation evidence.")
            if row["identity_valid"]:
                for event in row["trace"]:
                    if (event.get("backend") != configuration.backend or event.get("session_id") != row["session_id"]
                            or event.get("run_id") != row["run_id"]):
                        raise ValueError("Stored trace identity differs from its backend/session/run.")
        recomputed = summarize_comparison(report)
        return {**report, "summary": recomputed}
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Malformed backend comparison report.") from error


def offline_native_factory(configuration, root, task, plan):
    """Credential-free native adapter running the existing guarded Lab runtime.

    This script uses the curated expected source as synthetic replay data. It is
    intentionally unsuitable for judging the ability of a real coding model.
    """
    if configuration.backend != "native" or configuration.provider != "replay" or configuration.model != "scripted-v1":
        raise ValueError("Default experiment factory supports native/replay/scripted-v1 only; live backends need a host-bound authorized factory.")
    if configuration.model_revision != "scripted-v1":
        raise ValueError("Native replay's verified model revision is scripted-v1.")
    if plan.tool_mode != "native":
        raise ValueError("The native source replay requires native guarded tools.")
    from firik_agent.benchmarks.manifests import TaskManifest
    from firik_agent.benchmarks.providers import ReplayProvider
    from firik_agent.benchmarks.runner import BenchmarkToolFilter, CAPABILITIES, _agent
    from firik_agent.core.session import ChatSession
    from firik_agent.harness.native import NativeHarnessBackend
    from firik_agent.projects.coding_runtime import CodingRunControl
    from firik_agent.projects.harness_profiles import resolve_harness_profile

    step = {"id": "change", "title": "Apply canonical source transformation", "acceptance_criteria": [task.evaluation.description],
            "verification": "builtin:verify", "status": "in_progress"}
    turns = [{"calls": [{"name": "update_plan", "arguments": {"steps": [step]}}]}]
    changed = [name for name, value in task.evaluation.expected_files.items() if task.repository.files.get(name) != value]
    if set(task.repository.files) != set(task.evaluation.expected_files):
        raise ValueError("The synthetic default factory supports same-file source transformations only.")
    for offset in range(0, len(changed), 8):
        batch = changed[offset:offset + 8]
        turns.append({"calls": [{"name": "read_file", "arguments": {"path": name}} for name in batch]})
        turns.append({"calls": [{"name": "edit_file", "arguments": {"path": name, "expected_hash": "$hash:" + name,
            "old_text": task.repository.files[name], "new_text": task.evaluation.expected_files[name], "step_id": "change"}} for name in batch]})
    turns.extend([{"calls": [{"name": "run_tests", "arguments": {"command": "builtin:verify"}}]},
                  {"calls": [{"name": "update_plan", "arguments": {"steps": [{**step, "status": "completed", "evidence_ids": ["$evidence"]}]}}]},
                  {"text": "Canonical source transformation verified."}])
    for turn in turns:
        turn["usage"] = {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24}
    manifest = TaskManifest(id=task.id, prompt=task.prompt, mode="auto", files=task.repository.files,
        expected_files=task.evaluation.expected_files, turns=turns, allow_edits=True, require_native_evidence=True,
        timeout_seconds=min(plan.timeout_seconds, 30.0), token_budget=plan.token_budget or 10000)
    replay = ReplayProvider(manifest)
    replay.model_revision = "scripted-v1"
    session = ChatSession(model=configuration.model, temperature=configuration.provider_settings.get("temperature", 0.0),
                          max_tokens=configuration.provider_settings.get("max_output_tokens", 2048), active_mcp_servers=set())
    control = CodingRunControl(session.session_id, "harness-comparison", mode="auto",
        permissions={"allow_edits": plan.permissions.get("filesystem.write") == "allow", "allow_deletes": False,
                     "allow_terminal": False, "allow_tests": True, "allow_mcp": False, "allow_subagents": False},
        policy=dict(plan.permissions))
    control.capabilities = dict(CAPABILITIES)
    control.permit_provider_fallback = False
    control.permit_context_compaction = False
    control.harness_profile = resolve_harness_profile(session_override=configuration.profile)
    tools = BenchmarkToolFilter(root, control, manifest)
    tools._expected_files = dict(task.evaluation.expected_files)

    def approve_builtin(event):
        if getattr(event.type, "value", event.type) == "approval_required":
            metadata = event.metadata
            control.decide_approval(metadata["request_id"], metadata.get("tool_name") == "run_tests"
                                    and metadata.get("arguments") == {"command": "builtin:verify"})
    control.on_event = approve_builtin
    agent = _agent(replay, root, 32)
    # The replay provider checks this limit before emitting its synthetic turn;
    # real providers must never receive this opt-in flag from a plan file.
    agent._harness_hard_token_budget = True
    return NativeHarnessBackend(agent=agent, chat_session=session,
        runtime_options={"session_tool_filter": tools, "run_control": control, "cwd": str(root)})
