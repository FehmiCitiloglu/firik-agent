"""Deterministic replay against the real native runtime and host permission gate."""
from __future__ import annotations

import ast
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import sqlite3
import subprocess
import tempfile
import time
from typing import Any
from uuid import uuid4

from firik_agent import __version__
from firik_agent.benchmarks.manifests import EVALUATOR_VERSION, TaskManifest, builtin_tasks
from firik_agent.core.agent import Agent
from firik_agent.core.compaction import CompactionState, ContextCompactor
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.metrics import RunMetrics
from firik_agent.core.session import ChatSession
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import StreamChunk, ToolCall, ToolCallFunction, ToolResult
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.coding_sessions import CodingSessionStore, redact_sensitive
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.indexer import read_project_text
from firik_agent.projects.models import Project
from firik_agent.projects.model_capabilities import usage_tokens
from firik_agent.providers.base import BaseProvider
from firik_agent.providers.registry import ProviderRegistry

LAB_VERSION = "1"
PROMPT_REVISION = "native-coding-v1"
TOOL_REVISION = "guarded-file-tools-and-ast-v1"
CAPABILITIES = {"supports_tools": True, "supports_streaming": True, "context_window": 32_000,
                "source": "synthetic_replay_contract"}
LABEL = "Deterministic offline replay; synthetic usage; no live-model performance or billing claim."


from firik_agent.benchmarks.providers import ReplayBudgetExceeded, ReplayProvider
from firik_agent.benchmarks.evaluator import evaluate_sources


class BenchmarkToolFilter(CodingToolFilter):
    """Narrower than the native gate. No fixture command reaches a subprocess."""
    ALLOWED = {"read_file", "list_files", "search_project", "search_symbols", "edit_file", "write_file",
               "update_plan", "record_verification", "run_tests"}

    def __init__(self, root, control, manifest):
        self.manifest = manifest
        super().__init__(root, control)

    def is_tool_allowed(self, name, session):
        return name in self.ALLOWED and super().is_tool_allowed(name, session)

    async def execute(self, tool_call, session):
        if tool_call.function.name == "run_tests":
            try:
                arguments = json.loads(tool_call.function.arguments)
            except (TypeError, ValueError):
                arguments = None
            if arguments != {"command": "builtin:verify"}:
                return ToolResult(tool_call_id=tool_call.id, name="run_tests", is_error=True,
                                  content="Benchmark permits only the fixed builtin:verify evaluator.")
        return await super().execute(tool_call, session)

    async def run_tests(self, command, cwd=".", timeout=60):
        # Called through native execute/authorization. No shell, import or exec
        # of repository contents: these are exact source and parser checks.
        if command != "builtin:verify" or cwd != ".":
            raise PermissionError("Only the fixed source evaluator is available.")
        started, revision = time.monotonic(), self.control.workspace_revision
        before = await asyncio.to_thread(self._workspace_version)
        passed, detail = evaluate_sources(self.root, self._expected_files)
        after = await asyncio.to_thread(self._workspace_version)
        fresh = before["complete"] and after["complete"] and before["digest"] == after["digest"] and revision == self.control.workspace_revision
        result = {"evidence_id": f"verify_{uuid4().hex}", "kind": "test", "command": "builtin:verify",
                  "evaluator": EVALUATOR_VERSION, "execution_backend": "in_process_ast", "executed": True,
                  "passed": passed and fresh, "fresh": fresh, "exit_code": None,
                  "status": "passed" if passed and fresh else "failed", "output": detail,
                  "workspace_revision": revision, "workspace_hash": after["digest"],
                  "duration_ms": (time.monotonic() - started) * 1000}
        self.control.verifications.append(result)
        self._evidence_versions[result["evidence_id"]] = after
        if result["passed"]:
            self._verified_evidence.add(result["evidence_id"])
        self.control.emit(AgentEventType.VERIFICATION, "Fixed source/AST evaluator executed.", **result)
        return ToolResult(tool_call_id="", name="run_tests", content=json.dumps(result), is_error=not result["passed"])



def _git(root: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if not executable:
        raise RuntimeError("Offline benchmark repositories require Git on PATH.")
    # Git's environment/config/hooks must not turn an offline fixture into an
    # invocation of the user's helper, filter, signing program or template.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
               GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000", GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000")
    result = subprocess.run([executable, "-c", "core.hooksPath=" + os.devnull, "-c", "init.defaultBranch=main",
                             "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                             "-c", "commit.gpgsign=false", "-c", "user.name=Firik Benchmark",
                             "-c", "user.email=benchmark@invalid", "-C", str(root), *arguments],
                            env=env, capture_output=True, text=True, timeout=5, check=True)
    return result.stdout.strip()


def repository_metadata(root: Path | None = None) -> dict[str, Any]:
    try:
        directory = root or Path(__file__).resolve().parents[2]
        return {"commit": _git(directory, "rev-parse", "HEAD"),
                "dirty": bool(_git(directory, "status", "--porcelain", "--ignore-submodules=all"))}
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return {"commit": None, "dirty": None}


def source_fingerprint() -> str:
    """Hash installed Python harness sources, including uncommitted changes."""
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for source in sorted(package.rglob("*.py")):
        if source.is_symlink():
            raise ValueError("Harness source fingerprint refuses symbolic links.")
        digest.update(source.relative_to(package).as_posix().encode() + b"\x00")
        digest.update(source.read_bytes() + b"\x00")
    return digest.hexdigest()


def _agent(provider: BaseProvider, root: Path, iterations: int) -> Agent:
    # Match the desktop's run-local dependency setup without Agent.__init__,
    # whose provider factory reads user credentials even for explicit config.
    agent = Agent.__new__(Agent)
    agent._is_initialized = True
    agent.max_iterations, agent.temperature = iterations, 0.0
    agent.messages, agent.compaction = [], CompactionState()
    agent.default_cwd = str(root)
    agent.context_compactor = ContextCompactor({"enabled": False})
    agent.tools, agent.providers = ToolRegistry(), ProviderRegistry()
    agent.providers.register(provider.name, provider, set_active=True)
    return agent


async def run_trial(manifest: TaskManifest, trial: int = 1, *, profile: str = "native-replay",
                    provider: BaseProvider | None = None, configuration=None) -> dict[str, Any]:
    """Execute one fresh run through native events, tools, permissions and persistence.

    A supplied provider is a trusted host dependency. Live callers must explicitly
    opt in before constructing it; fixture data can never choose a provider.
    """
    manifest = TaskManifest.model_validate(manifest.model_dump())
    manifest_sha256 = manifest.fingerprint()
    if isinstance(trial, bool) or not isinstance(trial, int) or not 1 <= trial <= 100:
        raise ValueError("Trial number must be between 1 and 100.")
    configured = configuration is not None
    if configured:
        from firik_agent.benchmarks.models import HarnessConfiguration
        configuration = HarnessConfiguration.model_validate(
            configuration.model_dump() if hasattr(configuration, "model_dump") else configuration)
        config = configuration.model_dump(mode="json")
        profile = configuration.id
    else:
        if profile != "native-replay":
            raise ValueError("A non-default harness profile requires an explicit configuration.")
        config = {"id": profile, "request_budget": manifest.request_budget,
                  "token_budget": manifest.token_budget, "timeout_seconds": manifest.timeout_seconds,
                  "max_output_tokens": 2048, "temperature": 0.0,
                  "model": {"provider": "replay", "model": "scripted-v1", "endpoint_sha256": None},
                  "permissions": {"allow_edits": manifest.allow_edits, "allow_tests": manifest.require_native_evidence,
                                  "allow_terminal": False, "allow_mcp": False, "network": False},
                  "evaluator_version": EVALUATOR_VERSION, "compaction": False, "fallback": False}
    permissions = config["permissions"]
    if any(permissions.get(key, False) for key in ("allow_terminal", "allow_mcp", "network")):
        raise ValueError("The benchmark host supports inert file tools only; executable/network fixture tools require a sandbox.")
    if config.get("compaction") or config.get("fallback"):
        raise ValueError("This bounded lab does not enable compaction or provider fallback.")
    if config.get("evaluator_version") != EVALUATOR_VERSION:
        raise ValueError("Unsupported trusted evaluator version.")
    request_limit = min(manifest.request_budget, config["request_budget"])
    timeout = min(manifest.timeout_seconds, config["timeout_seconds"])
    token_limit = min(manifest.token_budget, config["token_budget"])
    # Clone the replay contract before imposing trusted host budget limits.
    manifest = TaskManifest.model_validate({**manifest.model_dump(), "token_budget": token_limit})
    selected_provider = provider or ReplayProvider(manifest)
    offline = isinstance(selected_provider, ReplayProvider)
    if configured and (selected_provider.name != config["model"]["provider"] or
                       selected_provider.current_model != config["model"]["model"]):
        raise ValueError("Injected provider identity disagrees with the harness configuration.")
    known_secrets = tuple(value for value in (getattr(selected_provider, "api_key", ""),) if value)
    from firik_agent.benchmarks.models import fingerprint
    configuration_sha = fingerprint(config)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="firik-benchmark-") as directory:
        base, root = Path(directory), Path(directory) / "repository"
        root.mkdir()
        for name, content in manifest.files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        (base / "outside.txt").write_text("outside fixture canary", encoding="utf-8")
        # Expected bytes are held by the host, never written inside the agent root.
        expected_files = dict(manifest.expected_files)
        expected_sha = hashlib.sha256(json.dumps(expected_files, sort_keys=True).encode()).hexdigest()
        _git(root, "init", "--quiet", "--template=", "--object-format=sha1")
        _git(root, "add", "--all")
        _git(root, "commit", "--quiet", "--allow-empty", "-m", "Deterministic fixture")
        fixture_revision = _git(root, "rev-parse", "HEAD")
        db = ProjectDatabase(base / "runs.db")
        db.create_project(Project(id="benchmark", name=manifest.id, local_path=str(root)))
        store = CodingSessionStore(db, known_secrets=known_secrets)
        recorder = RunMetrics()
        session = ChatSession(model=selected_provider.current_model, temperature=config["temperature"],
                              max_tokens=config["max_output_tokens"], active_mcp_servers=set())
        record = store.create_run("benchmark", session_id=session.session_id, provider=selected_provider.name,
                                  model=session.model, user_request=manifest.prompt, mode=manifest.mode)
        trace, status, final = [], "completed", ""
        terminal_reason, done_received = "completed", False
        control = CodingRunControl(session.session_id, "benchmark", mode=manifest.mode, run_id=record.id,
            permissions={"allow_edits": manifest.allow_edits and permissions["allow_edits"],
                         "allow_deletes": False, "allow_terminal": False,
                         "allow_tests": manifest.require_native_evidence and permissions["allow_tests"],
                         "allow_mcp": False, "allow_subagents": False},
            policy={"network": "deny", "shell.network": "deny", "mcp": "deny", "spawn_agents": "deny"})
        control.capabilities = dict(CAPABILITIES) if offline else {
            **getattr(selected_provider, "model_metadata", {}).get(selected_provider.current_model, {}),
            "source": "configured_provider_metadata"}
        control.permit_provider_fallback = False
        control.permit_context_compaction = False

        def observe(event):
            nonlocal terminal_reason
            safe = redact_sensitive(asdict(event), known_secrets)
            store.append_event(record.id, safe)
            recorder.observe(event)
            trace.append(safe)
            if event.type == AgentEventType.MODEL_REQUEST_FINISHED:
                failure = event.metadata.get("error_type")
                reasons = {"ReplayRateLimit": "rate_limit", "ReplayContextExhausted": "context_exhaustion",
                           "ReplayInterrupted": "interrupted", "ReplayIncompleteToolStream": "incomplete_tool_stream",
                           "ReplayBudgetExceeded": "token_budget_exhausted"}
                if failure:
                    terminal_reason = reasons.get(failure, "provider_error")
            if event.type == AgentEventType.APPROVAL_REQUIRED:
                allowed = (event.metadata.get("tool_name") == "run_tests"
                           and event.metadata.get("arguments") == {"command": "builtin:verify"})
                control.decide_approval(event.metadata["request_id"], allowed)

        control.on_event = observe
        tools = BenchmarkToolFilter(root, control, manifest)
        # Give the gate its own immutable snapshot of the independent expectations.
        tools._expected_files = expected_files
        agent = _agent(selected_provider, root, request_limit + 1)
        runtime_started = time.monotonic()
        deadline = runtime_started + timeout
        propagate_cancellation = False
        requested = 0

        def before_request(*_):
            nonlocal requested, terminal_reason
            if requested >= request_limit:
                terminal_reason = "request_budget_exhausted"
                raise ReplayBudgetExceeded("Benchmark model request budget exhausted.")
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError()
            requested += 1
        control.before_model_request = before_request

        async def consume():
            nonlocal status, final, done_received, terminal_reason
            stream = agent.run_stream(manifest.prompt, session=session, session_tool_filter=tools,
                                      run_control=control, cwd=str(root))
            try:
                async for event in stream:
                    observe(event)
                    if time.monotonic() >= deadline:
                        raise asyncio.TimeoutError()
                    if event.type == AgentEventType.ERROR:
                        # Preserve native completion blocking before any DONE.
                        # This changes the adapter, never the fixture oracle.
                        blocked = event.metadata.get("completion_classification") == "blocked"
                        status = "blocked" if blocked else "failed"
                        if blocked:
                            terminal_reason = "completion_gate"
                    elif event.type == AgentEventType.DONE:
                        final = event.content or ""
                        done_received = True
            finally:
                await stream.aclose()

        try:
            await asyncio.wait_for(consume(), timeout=timeout)
        except asyncio.CancelledError:
            status, terminal_reason = "cancelled", "cancelled"
            control.cancel()
            propagate_cancellation = not getattr(selected_provider, "fixture_cancelled", False)
        except asyncio.TimeoutError:
            status, terminal_reason = "failed", "timeout"
            control.cancel()
        except Exception as error:
            status = "failed"
            if terminal_reason == "completed":
                terminal_reason = "crash"
            final = f"Harness exception: {type(error).__name__}"
        if terminal_reason == "interrupted":
            status = "interrupted"
        if status == "completed" and not done_received:
            status, terminal_reason = "failed", "interrupted"
        runtime_wall_time_ms = (time.monotonic() - runtime_started) * 1000
        gate = await tools.completion_gate_async()
        if status == "completed" and not gate["passed"]:
            status, terminal_reason = "blocked", "completion_gate"
        sources_ok, evaluation = evaluate_sources(root, expected_files)
        if status == "completed" and not sources_ok:
            status, terminal_reason = "blocked", "verification_failed"
        metrics = recorder.snapshot()
        safety_violations = int(not sources_ok and not manifest.allow_edits and manifest.expected_source_match)
        outside_intact = (base / "outside.txt").read_text(encoding="utf-8") == "outside fixture canary"
        safety_violations += int(not outside_intact)
        text_ok = manifest.expected_text in final
        evidence_ok = not manifest.require_native_evidence or bool(gate["evidence_ids"])
        expected_reason = manifest.expected_terminal_reason is None or terminal_reason == manifest.expected_terminal_reason
        fixture_passed = (sources_ok == manifest.expected_source_match and text_ok and evidence_ok and
                          status == manifest.expected_status and expected_reason and
                          metrics["tool_errors"] >= manifest.minimum_tool_errors and safety_violations == 0)
        reported_tokens = (selected_provider.tokens if not selected_provider.unknown_usage_requests else None) if offline else metrics["usage"].get("total_tokens")
        result = {"task_id": manifest.id, "manifest_sha256": manifest_sha256, "trial": trial,
                  "run_id": record.id, "trial_id": record.id, "session_id": session.session_id,
                  "mode": "offline" if offline else "live", "configuration": config,
                  "configuration_sha256": configuration_sha,
                  "runtime_status": status, "expected_runtime_status": manifest.expected_status,
                  "terminal_outcome": {"status": status, "reason": terminal_reason},
                  "completion_claimed": done_received,
                  "fixture_passed": fixture_passed,
                  "task_success": fixture_passed and status == "completed",
                  "verified": fixture_passed and status == "completed",
                  "verification": {"evaluator": EVALUATOR_VERSION, "source_match": sources_ok,
                                   "expected_text_match": text_ok, "evidence_match": evidence_ok,
                                   "detail": evaluation, "native_gate": gate, "expected_sha256": expected_sha},
                  "provenance": {"starting_revision": fixture_revision, "evaluator_version": EVALUATOR_VERSION,
                                 "evaluator_sha256": expected_sha, "configuration_sha256": configuration_sha,
                                 "permissions": control.permissions, "request_budget": request_limit,
                                 "token_budget": token_limit, "timeout_seconds": timeout,
                                 "workspace_id": base.name, "workspace_retained": False},
                  "safety_violations": safety_violations, "metrics": metrics,
                  "runtime_wall_time_ms": runtime_wall_time_ms,
                  "token_budget": {"limit": token_limit, "reported_tokens": reported_tokens,
                                   "reported_lower_bound": selected_provider.tokens if offline else metrics["reported_token_lower_bound"],
                                   "unreported_requests": metrics["request_count"] - metrics["coverage"]["total_tokens"],
                                   "enforcement": "Reported usage; unknown usage is bounded by the request/output budgets, never invented."},
                  "wall_time_ms": (time.monotonic() - started) * 1000,
                  "fixture_revision": fixture_revision, "trace": trace}
        store.finish_run(record.id, status=status, result=status, summary=final,
                         messages=[message.model_dump(exclude_none=True) for message in session.messages],
                         usage={**metrics.get("usage", {}), "harness_metrics": metrics},
                         tests_executed=control.verifications, file_journal=control.changes)
        persisted = store.get_run(record.id)
        result["persistence_verified"] = bool(persisted and persisted.status == status and persisted.usage.get("harness_metrics") == metrics)
        result["fixture_passed"] = fixture_passed and result["persistence_verified"]
        result["task_success"] = result["verified"] = result["fixture_passed"] and status == "completed"
        result["wall_time_ms"] = (time.monotonic() - started) * 1000
        if propagate_cancellation:
            raise asyncio.CancelledError()
        return redact_sensitive(enrich_result(result), known_secrets)

from firik_agent.benchmarks.metrics import distribution, enrich_report, enrich_result


async def run_suite(tasks: list[TaskManifest] | None = None, *, trials: int = 3,
                    profile: str = "native-replay", label: str = "candidate", configuration=None) -> dict[str, Any]:
    source_start = source_fingerprint()
    if isinstance(trials, bool) or not isinstance(trials, int) or not 1 <= trials <= 100:
        raise ValueError("Trials must be between 1 and 100.")
    tasks = builtin_tasks() if tasks is None else [TaskManifest.model_validate(task.model_dump()) for task in tasks]
    if configuration is not None:
        from firik_agent.benchmarks.models import HarnessConfiguration
        configuration = HarnessConfiguration.model_validate(configuration.model_dump() if hasattr(configuration, "model_dump") else configuration)
        profile = configuration.id
    if not tasks or len(tasks) > 50 or len({task.id for task in tasks}) != len(tasks):
        raise ValueError("A suite requires 1–50 uniquely identified tasks.")
    rows = [await run_trial(task, trial, profile=profile, configuration=configuration) for trial in range(1, trials + 1) for task in tasks]
    settings = {"manifests": [task.model_dump() for task in tasks], "profile": profile,
                "capabilities": CAPABILITIES, "model": "scripted-v1", "provider": "replay",
                "evaluator": EVALUATOR_VERSION, "prompt_revision": PROMPT_REVISION,
                "tool_revision": TOOL_REVISION, "compaction": False, "fallback": False,
                "temperature": 0, "max_output_tokens": 2048, "lab_version": LAB_VERSION}
    report = {"schema_version": 1, "label": label, "method": LABEL,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "metadata": {"repository": repository_metadata(), "source_sha256": source_start,
                           "source_sha256_after": source_fingerprint(),
                           "controlled_settings_sha256": hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest(),
                           "harness_version": __version__, "lab_version": LAB_VERSION,
                           "model": "scripted-v1", "provider": "replay", "capability_evidence": CAPABILITIES,
                           "profile": profile, "prompt_revision": PROMPT_REVISION, "tool_revision": TOOL_REVISION,
                           "evaluator_version": EVALUATOR_VERSION, "python": os.sys.version.split()[0],
                           "environment": {"python": platform.python_version(), "os": platform.system(),
                                           "machine": platform.machine(), "sqlite": sqlite3.sqlite_version}},
              "tasks": [{"id": task.id, "manifest_sha256": task.fingerprint(),
                         "timeout_seconds": task.timeout_seconds, "token_budget": task.token_budget}
                        for task in tasks], "trials_per_task": trials, "results": rows}
    report["metadata"]["mode"] = "offline"
    report["metadata"]["source_changed"] = source_start != report["metadata"]["source_sha256_after"]
    if configuration is not None:
        from firik_agent.benchmarks.models import fingerprint
        config = configuration.model_dump(mode="json")
        report["metadata"]["configuration"] = config
        report["metadata"]["configuration_sha256"] = fingerprint(config)
        report["metadata"]["controls"] = {
            "dataset": [{"id": task.id, "manifest_sha256": task.fingerprint(),
                         "starting_revision": rows[index]["fixture_revision"]}
                        for index, task in enumerate(tasks)],
            "model": config["model"], "token_budget": config["token_budget"],
            "timeout_seconds": config["timeout_seconds"], "permissions": config["permissions"],
            "evaluator_version": config["evaluator_version"], "trials": trials}
    passed = sum(row["fixture_passed"] for row in rows)
    # A conservative interval is explicitly descriptive; timings are local and
    # non-independent, so no model/generalization claim follows from it.
    n, p = len(rows), passed / len(rows)
    z = 1.96
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    report["summary"] = {"trials": n, "fixtures_passed": passed, "fixture_pass_rate": p,
                         "completed_verified_tasks": sum(row["task_success"] for row in rows),
                         "descriptive_wilson95": [max(0, center-margin), min(1, center+margin)],
                         "safety_violations": sum(row["safety_violations"] for row in rows),
                         "wall_time_ms": distribution([row["wall_time_ms"] for row in rows]),
                         "per_task": {task.id: distribution([row["wall_time_ms"] for row in rows if row["task_id"] == task.id]) for task in tasks},
                         "cost": None, "successful_tasks_per_dollar": None,
                         "usage_label": "Synthetic replay usage; unknown values remain null."}
    return enrich_report(report)


# Public imports preserve the original vertical-slice API.
from firik_agent.benchmarks.comparison import compare_reports
from firik_agent.benchmarks.reporters import markdown_report
from firik_agent.benchmarks.storage import export_report
