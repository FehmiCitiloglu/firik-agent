"""Lifecycle adapter over the existing runtime, registries and permission owner."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from contextlib import aclosing
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from firik_agent.core.metrics import RunMetrics
from .models import (HarnessCapability, HarnessCompletionEvidence, HarnessError,
                     HarnessEvent, HarnessExecutionPolicy, HarnessMetadata,
                     HarnessResult, HarnessSession, HarnessTask, HarnessToolInvocation, HarnessUsage)


@dataclass
class _Run:
    result: HarnessResult
    queue: asyncio.Queue
    worker: asyncio.Task | None = None
    exception: BaseException | None = None
    streaming: bool = False
    stopping: bool = False
    metrics: RunMetrics = field(default_factory=RunMetrics)


class NativeHarnessBackend:
    """The bound agent remains owner of provider, context, tools and recovery.

    The adapter does not initialize MCP, replace the permission gate, copy the
    runtime, or replay journals. Backpressure bounds pending events. Persistent
    history continues to belong to CodingSessionStore/ExecutionJournal.
    """
    def __init__(self, agent=None, chat_session=None, runtime_options=None):
        self.agent, self.chat_session = agent, chat_session
        self.runtime_options = dict(runtime_options or {})
        self._sessions: dict[str, tuple[HarnessSession, HarnessExecutionPolicy]] = {}
        self._runs: dict[str, _Run] = {}
        self._closed: set[str] = set()
        self._closing: dict[str, asyncio.Task] = {}

    def get_metadata(self):
        from firik_agent import __version__
        return HarnessMetadata("native", __version__, available=True,
            interface="Agent._run_native_stream",
            security_notes=("Existing Firik permission owner remains authoritative.",
                            "Host tools retain host authority; Docker command isolation is separate."))

    def get_capabilities(self):
        values = {"streaming": True, "persistent_sessions": True,
            "external_model_selection": True, "custom_tools": True,
            "tool_level_approvals": True, "cancellation": True, "subagents": True,
            "sandboxing": None, "structured_usage": True, "resumable_execution": True,
            "context_inspection": True,
            "hard_token_budget": getattr(self.agent, "_harness_hard_token_budget", False) is True}
        return {name: HarnessCapability(name, value, "Existing Firik runtime; capabilities depend on bound run policy."
            if name != "sandboxing" else "Execution environment and individual tool boundary determine isolation.")
            for name, value in values.items()}

    def validate_configuration(self, policy, provider: Mapping[str, Any] | None = None):
        if self.agent is None:
            raise HarnessError("runtime_missing", "Native backend requires a bound Firik runtime.", backend="native")
        if policy.require_sandbox or policy.tool_mode == "sandbox":
            raise HarnessError("policy_unsupported", "Native harness cannot promise whole-runtime OS confinement. Select individual execution isolation explicitly.", backend="native")
        tool_filter = self.runtime_options.get("session_tool_filter")
        control = self.runtime_options.get("run_control") or getattr(tool_filter, "control", None)
        if control is not None:
            if self.chat_session is None or control.session_id != self.chat_session.session_id or tool_filter is None:
                raise HarnessError("identity_mismatch", "Native coding session and permission owner must match.")
            if policy.project_id and policy.project_id != control.project_id:
                raise HarnessError("identity_mismatch", "Harness policy belongs to a different project.")
            root = getattr(tool_filter, "root", None)
            if root is not None and policy.workspace and Path(root).resolve() != Path(policy.workspace).resolve():
                raise HarnessError("workspace_mismatch", "Harness and coding workspace disagree.")
        elif policy.permissions:
            raise HarnessError("permission_owner_missing", "Explicit permissions require the existing coding permission owner.")
        if policy.tool_mode == "none":
            schemas = tool_filter.get_schemas(self.chat_session) if tool_filter is not None else self.agent.tools.get_schemas()
            if schemas or not getattr(self.agent, "_is_initialized", False):
                raise HarnessError("policy_unsupported", "Tool-free native mode requires an initialized runtime with an empty permitted tool inventory.")
        if control is not None:
            effective = {**control.permissions, **control.policy_snapshot()}
            for key, value in policy.permissions.items():
                if key not in effective or effective[key] != value:
                    raise HarnessError("permission_mismatch", "Harness policy must match the bound native permission owner.")
        if policy.token_budget is not None and not self.get_capabilities()["hard_token_budget"].supported:
            raise HarnessError("budget_unsupported", "Provider-reported usage cannot enforce a hard pre-billing token cap.")
        if provider:
            active = self.agent.providers.get_active()
            if provider.get("name") and provider["name"] != getattr(active, "name", None):
                raise HarnessError("provider_mismatch", "Configure the native provider through Firik's existing provider registry.")
            selected = getattr(active, "current_model", None)
            if provider.get("model") and provider["model"] != selected:
                raise HarnessError("model_mismatch", "Harness model disagrees with the configured native session.")

    async def initialize_session(self, *, policy, provider=None, session_id=None):
        self.validate_configuration(policy, provider)
        if len(self._sessions) >= policy.max_sessions:
            raise HarnessError("session_limit", "Native concurrent session limit reached.")
        sid = session_id or getattr(self.chat_session, "session_id", None) or "harness_" + uuid4().hex
        if sid in self._sessions or sid in self._closed:
            raise HarnessError("session_conflict", "Session identity is already in use or closed.")
        if self.chat_session is not None and sid != self.chat_session.session_id:
            raise HarnessError("identity_mismatch", "Harness session must preserve the native session identity.")
        if self._sessions:
            raise HarnessError("session_conflict", "A bound native runtime cannot be shared by multiple sessions.")
        session = HarnessSession(sid, "native", policy.project_id, policy.workspace,
                                 {"permission_owner": "existing_native_runtime"})
        self._sessions[sid] = (session, policy)
        return session

    def _session(self, session):
        found = self._sessions.get(session.id)
        if found is None or found[0] is not session:
            raise HarnessError("session_mismatch", "Session does not belong to this backend.")
        return found[1]

    def _run(self, session, run_id):
        self._session(session)
        run = self._runs.get(run_id)
        if run is None or run.result.session_id != session.id:
            raise HarnessError("run_mismatch", "Run does not belong to this session.")
        return run

    async def execute_task(self, session, task):
        policy = self._session(session)
        if any(not run.worker.done() for run in self._runs.values()):
            raise HarnessError("run_active", "A native runtime already has an active task.")
        # Bound retained inspection state as well as streaming buffers.
        if len(self._runs) >= 32:
            raise HarnessError("run_limit", "Close the native session before starting more tasks.")
        active = self.agent.providers.get_active()
        model = getattr(active, "current_model", None)
        if task.model and task.model != model:
            raise HarnessError("model_mismatch", "Task model differs from the existing native provider/session.")
        control = self.runtime_options.get("run_control")
        rid = getattr(control, "run_id", None) or "harness_run_" + uuid4().hex
        if rid in self._runs:
            raise HarnessError("run_conflict", "A coding run identity cannot be replayed.")
        run = _Run(HarnessResult(rid, session.id, "native", model=model,
            metadata={"task_id": task.id, "profile": task.profile, "permission_effect": "none",
                      "model_revision": getattr(active, "model_revision", None),
                      "network_authority": "existing_native_permission_owner",
                      "actual_models": [], "actual_providers": []}),
            asyncio.Queue(maxsize=policy.max_events))
        self._runs[rid] = run
        run.worker = asyncio.create_task(self._execute(session, policy, task, run))
        return rid

    async def _execute(self, session, policy, task, run):
        sequence, final, done, errored = 0, "", False, False
        try:
            # Explicit child ownership preserves cancellation cleanup on Python 3.10+.
            async def consume():
                nonlocal sequence, final, done, errored
                runner = getattr(self.agent, "_run_native_stream", None) or self.agent.run_stream
                options = dict(self.runtime_options)
                options.setdefault("cwd", session.workspace)
                async with aclosing(runner(prompt=task.prompt, session=self.chat_session, **options)) as iterator:
                    async for event in iterator:
                        await deliver(event)

            async def deliver(event):
                nonlocal sequence, final, done, errored
                if run.stopping:
                    # The original provider reducer emits its final accounting
                    # frame while unwinding cancellation. Preserve that frame
                    # without waiting for an event consumer that is unwinding.
                    if event.type.value in {"model_request_finished", "model_usage", "tool_call_error"}:
                        run.metrics.observe(event)
                        sequence += 1
                        event.metadata = {**event.metadata, "harness_backend": "native",
                                          "harness_model": run.result.model}
                        if not run.queue.full():
                            run.queue.put_nowait((HarnessEvent(event.type.value, session.id, run.result.run_id,
                                sequence, event.content, dict(event.metadata), backend="native", native_event=event), asyncio.Event()))
                    return
                sequence += 1
                if event.type.value == "model_request_started" and event.metadata.get("model"):
                    actual_model = event.metadata["model"]
                    actual_models = list(run.result.metadata["actual_models"])
                    if actual_model not in actual_models and len(actual_models) < 32:
                        actual_models.append(actual_model)
                    providers = list(run.result.metadata["actual_providers"])
                    actual_provider = event.metadata.get("provider")
                    if actual_provider and actual_provider not in providers and len(providers) < 32:
                        providers.append(actual_provider)
                    run.result = replace(run.result, model=actual_model,
                        metadata={**run.result.metadata, "actual_models": actual_models, "actual_providers": providers})
                event.metadata = {**event.metadata, "harness_backend": "native",
                                  "harness_model": run.result.model}
                run.metrics.observe(event)
                kind = event.type.value
                if kind == "done":
                    done, final = True, event.content or ""
                if kind == "error":
                    errored = True
                    error = HarnessError(str(event.metadata.get("failure_kind", "runtime_error")),
                        event.content or "Native runtime error.", backend="native", details=dict(event.metadata))
                    run.result = replace(run.result, errors=(*run.result.errors, error))
                delivered = asyncio.Event()
                normalized = dict(event.metadata)
                if event.tool_name:
                    invocation = HarnessToolInvocation(str(event.metadata.get("tool_call_id", "")),
                        event.tool_name, event.arguments or {}, status=kind,
                        execution_boundary=event.metadata.get("execution_boundary", "unknown"),
                        result=event.content if kind in {"tool_call_result", "tool_call_error"} else None)
                    normalized["tool_invocation"] = invocation.__dict__
                if event.duration_ms is not None:
                    normalized["duration_ms"] = event.duration_ms
                await run.queue.put((HarnessEvent(kind, session.id, run.result.run_id, sequence,
                    event.content, normalized, backend="native", native_event=event), delivered))
                # A consumer can revoke policy or cancel in response to an
                # event. Preserve the original generator's delivery boundary
                # before allowing another model/tool dispatch.
                await delivered.wait()
            pending = asyncio.create_task(consume())
            try:
                finished, _ = await asyncio.wait((pending,), timeout=policy.timeout_seconds)
                if not finished:
                    raise asyncio.TimeoutError("Native harness time budget exceeded.")
                await pending
            finally:
                if not pending.done():
                    run.stopping = True
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            control = self.runtime_options.get("run_control")
            state = getattr(control, "state", "")
            status = "cancelled" if state == "cancelled" else "blocked" if state in {"blocked", "verification_failed"} else "failed" if errored or not done else "completed"
            # Native DONE is an answer, not proof of verified filesystem artifacts.
            snapshot = control.snapshot() if control is not None else {}
            completion = snapshot.get("completion", {})
            classification = completion.get("completion_classification", completion.get("classification", "unverified"))
            verified = bool(completion.get("last_result", {}).get("passed") is True and classification == "completed_verified")
            run.result = replace(run.result, status=status, text=final,
                completion=HarnessCompletionEvidence(verified, tuple(snapshot.get("verifications", [])), classification))
        except asyncio.CancelledError as exc:
            run.result = replace(run.result, status="cancelled")
            if not run.stopping:
                run.exception = exc
        except BaseException as exc:
            code = "timeout" if isinstance(exc, asyncio.TimeoutError) else "native_failure"
            error = HarnessError(code, str(exc) or type(exc).__name__, backend="native",
                details={"exception_type": type(exc).__name__, "failure_kind": str(getattr(exc, "kind", ""))})
            run.result = replace(run.result, status="failed" if isinstance(exc, Exception) else "interrupted",
                                 errors=(*run.result.errors, error))
            run.exception = exc
        finally:
            metrics = run.metrics.snapshot()
            usage = metrics["usage"]
            run.result = replace(run.result, usage=HarnessUsage(usage.get("prompt_tokens"),
                usage.get("completion_tokens"), usage.get("cached_tokens"), usage.get("cost"),
                "native_shared_metrics" if metrics["request_count"] else "unknown"),
                metadata={**run.result.metadata, "harness_metrics": metrics})

    async def stream_events(self, session, run_id):
        run = self._run(session, run_id)
        if run.streaming:
            raise HarnessError("stream_in_use", "Only one event consumer is supported.")
        run.streaming = True
        try:
            while not run.worker.done() or not run.queue.empty():
                if not run.queue.empty():
                    event, delivered = run.queue.get_nowait()
                    try:
                        yield event
                    finally:
                        delivered.set()
                    continue
                pending = asyncio.create_task(run.queue.get())
                try:
                    await asyncio.wait((pending, run.worker), return_when=asyncio.FIRST_COMPLETED)
                    if pending.done():
                        event, delivered = pending.result()
                        try:
                            yield event
                        finally:
                            delivered.set()
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
            if run.exception is not None:
                raise run.exception
        except asyncio.CancelledError:
            await self.request_cancellation(session, run_id)
            while not run.queue.empty():
                event, delivered = run.queue.get_nowait()
                try:
                    if event.type in {"model_request_finished", "model_usage", "tool_call_error"}:
                        yield event
                finally:
                    delivered.set()
            raise
        finally:
            run.streaming = False

    async def request_cancellation(self, session, run_id):
        run = self._run(session, run_id)
        if not run.worker.done() and not run.stopping:
            run.stopping = True
            event = self.runtime_options.get("cancel_event")
            if event is not None:
                event.set()
            run.worker.cancel()
        await asyncio.shield(asyncio.gather(run.worker, return_exceptions=True))

    async def inspect_run(self, session, run_id):
        return self._run(session, run_id).result

    async def close_session(self, session):
        if session.id in self._closed:
            return
        self._session(session)
        cleanup = self._closing.get(session.id)
        if cleanup is None:
            cleanup = asyncio.create_task(self._close(session))
            self._closing[session.id] = cleanup
        try:
            await asyncio.shield(cleanup)
        finally:
            if cleanup.done():
                self._closing.pop(session.id, None)

    async def _close(self, session):
        for rid, run in list(self._runs.items()):
            if run.result.session_id == session.id:
                await self.request_cancellation(session, rid)
                del self._runs[rid]
        self._sessions.pop(session.id)
        # Keep no unbounded collection of retired identities.
        self._closed = {session.id}


# Short alias for host integrations.
NativeBackend = NativeHarnessBackend
