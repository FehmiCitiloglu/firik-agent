"""Supported external protocols with explicit, deliberately narrow authority.

Pi is a tool-free RPC backend, not a replacement for guarded coding tools. Codex
is explicitly unavailable until whole-runtime isolation is verified. Neither
adapter imports credentials or configuration from an existing installation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Any, Mapping
from uuid import uuid4

from .models import (HarnessCapability, HarnessCompletionEvidence, HarnessError,
                     HarnessEvent, HarnessExecutionPolicy, HarnessMetadata,
                     HarnessResult, HarnessSession, HarnessTask, HarnessUsage)
from .transport import JsonlProcess, TransportError

PI_VERSION = "1.4.0"
PI_FLAGS = ("--mode", "rpc", "--no-session", "--no-tools", "--no-extensions", "--no-mcp",
            "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files",
            "--no-approve", "--offline")
PROVIDER_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                 "google": "GEMINI_API_KEY"}


def clean_environment(root: str, credentials: Mapping[str, str] | None = None) -> dict[str, str]:
    """Allowlist runtime paths, never copy arbitrary host environment variables."""
    node = shutil.which("node")
    path = ([str(Path(node).parent)] if node else []) + ["/usr/bin", "/bin"]
    env = {"PATH": os.pathsep.join(dict.fromkeys(path)), "HOME": root, "USERPROFILE": root,
           "XDG_CONFIG_HOME": str(Path(root) / "config"), "XDG_DATA_HOME": str(Path(root) / "data"),
           "XDG_CACHE_HOME": str(Path(root) / "cache"), "TMPDIR": root, "NO_COLOR": "1",
           "PI_CODING_AGENT_DIR": str(Path(root) / "pi"), "PI_OFFLINE": "1", "PI_TELEMETRY": "0"}
    # Only explicitly handed-over supported provider secrets may cross boundary.
    for name, value in (credentials or {}).items():
        if name not in PROVIDER_KEYS.values() or not isinstance(value, str) or "\x00" in value:
            raise ValueError("Unsupported external credential environment entry")
        env[name] = value
    return env


def _version_probe(executable: str, root: str) -> tuple[bytes, int]:
    """Bound even version-only output and terminate hung descendant groups."""
    process = subprocess.Popen([executable, "--version"], cwd=root,
        env=clean_environment(root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True)
    output = bytearray()
    deadline = time.monotonic() + 5
    try:
        assert process.stdout is not None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise subprocess.TimeoutExpired([executable, "--version"], 5)
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > 8192:
                raise TransportError("version_output_limit", "Pi version output exceeds its bounded limit")
        return bytes(output), process.wait(timeout=max(.01, deadline - time.monotonic()))
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.stdout:
            process.stdout.close()
        process.wait(timeout=2)


@dataclass
class _PiRun:
    result: HarnessResult
    queue: asyncio.Queue
    worker: asyncio.Task | None = None
    cancellation: bool = False
    cancellation_task: asyncio.Task | None = None
    stop_requested: bool = False
    finishing: bool = False
    sequence: int = 0
    seen: set[str] = field(default_factory=set)
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _PiSession:
    session: HarnessSession
    policy: HarnessExecutionPolicy
    provider: dict[str, Any]
    temporary: Any
    transport: JsonlProcess
    run: _PiRun | None = None
    closed: bool = False
    close_task: asyncio.Task | None = None


class PiBackend:
    """Official Pi 1.4.0 JSONL RPC, restricted to one tool-free task per session.

    Tool-free is an application restriction, not OS sandboxing. The installed Pi
    executable is trusted host software. No repository files are admitted; only
    the explicitly supplied prompt and provider key leave Firik.
    """
    name = "pi"

    def __init__(self, executable: str | None = None, expected_version: str | None = PI_VERSION,
                 mode: str = "tool_free"):
        selected = executable or shutil.which("pi")
        self.executable = str(Path(selected).resolve()) if selected else None
        self.expected_version, self.mode = expected_version or PI_VERSION, mode
        self._sessions: dict[str, _PiSession] = {}
        self._metadata: HarnessMetadata | None = None
        self._lock = asyncio.Lock()

    def get_metadata(self) -> HarnessMetadata:
        if self._metadata is not None:
            return self._metadata
        reason, version = None, None
        if os.name == "nt":
            reason = "Windows process-tree cleanup is not verified; external execution disabled."
        elif not self.executable or not Path(self.executable).is_file():
            reason = "Pi executable is missing."
        elif self.expected_version != PI_VERSION:
            reason = "Only the audited Pi 1.4.0 interface is supported."
        elif self.mode != "tool_free":
            reason = "Only tool_free Pi mode preserves the supported host boundary."
        else:
            # Version-only invocation: private cwd/home, no extensions, no model request.
            try:
                with tempfile.TemporaryDirectory(prefix="firik-pi-probe-") as root:
                    stdout, returncode = _version_probe(self.executable, root)
                output = stdout.decode("utf-8", "replace").strip()
                match = re.fullmatch(r"(?:pi\s+)?(\d+\.\d+\.\d+)", output)
                version = match[1] if match else None
                if returncode or version != PI_VERSION:
                    reason = "Pi version is incompatible or could not be verified."
            except (OSError, subprocess.TimeoutExpired, TransportError) as exc:
                reason = "Pi version probe failed: " + type(exc).__name__
        self._metadata = HarnessMetadata("pi", version=version, available=reason is None,
            reason=reason, interface="official-jsonl-rpc/tool-free", license="MIT",
            security_notes=("No tools, extensions, MCP, skills or project context are loaded.",
                            "Private ephemeral cwd/home; this is not an OS sandbox.",
                            "Only explicit provider credentials; no existing installation authentication.",
                            "No verified coding completion, file changes, resume or hard token cap."))
        return self._metadata

    def get_capabilities(self) -> dict[str, HarnessCapability]:
        values = {"streaming": True, "persistent_sessions": False, "external_model_selection": True,
                  "custom_tools": False, "tool_level_approvals": False, "cancellation": True,
                  "subagents": False, "sandboxing": False, "structured_usage": True,
                  "resumable_execution": False, "context_inspection": True,
                  "host_permission_enforcement": False, "file_changes": False,
                  "hard_token_budget": False, "reconnection": False}
        return {name: HarnessCapability(name, value, "Pi 1.4.0 RPC; tool-free adapter restrictions")
                for name, value in values.items()}

    def validate_configuration(self, policy: HarnessExecutionPolicy,
                               provider: Mapping[str, Any] | None = None) -> None:
        info = self.get_metadata()
        if not info.available:
            raise HarnessError("backend_unavailable", info.reason or "Pi unavailable", backend="pi")
        if policy.tool_mode != "none" or policy.require_sandbox or policy.workspace:
            raise HarnessError("unsupported_policy", "Pi supports tool-free prompts only, without workspace admission or OS sandbox claims.", backend="pi")
        if policy.token_budget is not None:
            raise HarnessError("unsupported_budget", "Pi cannot enforce a hard total token budget before billing.", backend="pi")
        if policy.permissions and any(value not in (False, "deny") for value in policy.permissions.values()):
            raise HarnessError("unsupported_policy", "Pi tool-free sessions cannot grant tool permissions.", backend="pi")
        config = dict(provider or {})
        if set(config) - {"name", "model", "api_key", "allow_live"}:
            raise HarnessError("invalid_provider", "Unsupported Pi provider settings; endpoint/custom code configuration is not admitted.", backend="pi")
        if config.get("name") not in PROVIDER_KEYS or not isinstance(config.get("model"), str) or not config["model"].strip():
            raise HarnessError("invalid_provider", "An explicit supported provider and exact model ID are required.", backend="pi")
        if not isinstance(config.get("api_key"), str) or not config["api_key"] or "\x00" in config["api_key"]:
            raise HarnessError("authentication_required", "An explicitly authorized provider API key is required; existing Pi credentials are never imported.", backend="pi")
        if not policy.allow_network or config.get("allow_live") is not True:
            raise HarnessError("authorization_required", "Explicit live inference authorization and provider network permission are required.", backend="pi")

    async def _response(self, state: _PiSession, kind: str, **values: Any) -> dict[str, Any]:
        request_id = uuid4().hex
        await state.transport.send({"id": request_id, "type": kind, **values})
        deadline = time.monotonic() + min(10, state.policy.timeout_seconds)
        for _ in range(32):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            frame = await state.transport.read(timeout=remaining)
            if frame.get("type") == "response" and frame.get("success") is False:
                raise HarnessError("rpc_error", self._safe(str(frame.get("error", "Pi command failed")), state), backend="pi",
                                   details={"command": self._safe(str(frame.get("command", "unknown")), state)})
            if frame.get("type") == "response" and frame.get("id") == request_id:
                if frame.get("success") is not True:
                    raise HarnessError("rpc_error", self._safe(str(frame.get("error", "Pi command failed")), state), backend="pi",
                                       details={"command": kind})
                return frame.get("data") or {}
            kind = frame.get("type")
            if not isinstance(kind, str) or kind.startswith(("tool_", "extension_", "bash_", "agent_", "message_")):
                raise HarnessError("policy_violation", "Unexpected execution activity during tool-free Pi initialization.", backend="pi")
        raise HarnessError("startup_event_limit", "Pi initialization exceeded the bounded startup event count.", backend="pi")

    async def initialize_session(self, *, policy: HarnessExecutionPolicy,
                                 provider: Mapping[str, Any] | None = None,
                                 session_id: str | None = None) -> HarnessSession:
        self.validate_configuration(policy, provider)
        config = dict(provider or {})
        async with self._lock:
            if len(self._sessions) >= min(4, policy.max_sessions):
                raise HarnessError("session_limit", "Pi concurrent-session limit reached.", backend="pi")
            identifier = session_id or "pi_" + uuid4().hex
            if identifier in self._sessions:
                raise HarnessError("duplicate_session", "Pi session identity already exists.", backend="pi")
            temporary = tempfile.TemporaryDirectory(prefix="firik-pi-session-")
            transport = JsonlProcess(max_frames=min(policy.max_events, 4096))
            session = HarnessSession(identifier, "pi", policy.project_id, None,
                {"mode": "tool_free", "provider": config["name"], "model": config["model"], "isolation": "private-config-only"})
            state = _PiSession(session, policy, config, temporary, transport)
            self._sessions[identifier] = state
        try:
            await transport.start([self.executable, *PI_FLAGS, "--provider", config["name"], "--model", config["model"]],
                cwd=str(Path(temporary.name).resolve()), env=clean_environment(str(Path(temporary.name).resolve()), {PROVIDER_KEYS[config["name"]]: config["api_key"]}))
            data = await self._response(state, "get_state")
            model = data.get("model") or {}
            if model.get("id") != config["model"] or model.get("provider") != config["name"]:
                raise HarnessError("model_mismatch", "Pi selected a different model/provider than explicitly requested.", backend="pi",
                                   details={"actual_model": model.get("id"), "actual_provider": model.get("provider")})
            return session
        except BaseException as exc:
            diagnostic = self._safe(transport.stderr.decode("utf-8", "replace"), state)
            await self.close_session(session)
            if isinstance(exc, TransportError):
                raise HarnessError(exc.code, str(exc), backend="pi", details={**exc.details, "stderr": diagnostic,
                                   "process": transport.inspect()}) from exc
            if isinstance(exc, asyncio.TimeoutError):
                raise HarnessError("startup_timeout", "Pi did not finish session initialization before its deadline.", backend="pi",
                                   details={"stderr": diagnostic, "process": transport.inspect()}) from exc
            raise

    def _state(self, session: HarnessSession) -> _PiSession:
        state = self._sessions.get(session.id)
        if state is None or state.session is not session or state.closed:
            raise HarnessError("unknown_session", "Session is closed or belongs to another backend instance.", backend="pi")
        return state

    @staticmethod
    def _safe(value: str, state: _PiSession) -> str:
        from firik_agent.projects.coding_runtime import safe_text
        return safe_text(value.replace(state.provider.get("api_key", "\x00"), "[REDACTED]"))

    def _emit(self, state: _PiSession, run: _PiRun, kind: str, content: str | None = None,
              **metadata: Any) -> None:
        run.sequence += 1
        # Metadata also originates at the external boundary; recursively redact
        # the explicitly handed-over key as well as normal secret patterns.
        from firik_agent.projects.coding_runtime import safe_payload
        def redact(value):
            if isinstance(value, str):
                return self._safe(value, state)
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value
        event = HarnessEvent(kind, state.session.id, run.result.run_id, sequence=run.sequence,
            content=self._safe(content, state) if content else content, metadata=safe_payload(redact(metadata)), backend="pi")
        if run.queue.full():
            raise HarnessError("event_overflow", "Pi execution event buffer is full; run stopped rather than silently losing evidence.", backend="pi")
        run.queue.put_nowait(event)

    async def execute_task(self, session: HarnessSession, task: HarnessTask) -> str:
        state = self._state(session)
        if state.run is not None:
            raise HarnessError("session_consumed", "Pi sessions accept one task; initialize an isolated session for another task.", backend="pi")
        if task.model is not None and task.model != state.provider["model"]:
            raise HarnessError("model_mismatch", "Active Pi sessions cannot silently switch models.", backend="pi")
        run_id = "run_" + uuid4().hex
        run = _PiRun(HarnessResult(run_id, session.id, "pi", model=state.provider["model"],
            metadata={"profile": task.profile, "task_id": task.id, "execution_boundary": "external-tool-free",
                      "verification": "none", "provider": state.provider["name"]}), asyncio.Queue(maxsize=state.policy.max_events))
        state.run = run
        run.worker = asyncio.create_task(self._run(state, task, run))
        return run_id

    async def _run(self, state: _PiSession, task: HarnessTask, run: _PiRun) -> None:
        try:
            await asyncio.wait_for(self._consume(state, task, run), timeout=state.policy.timeout_seconds)
        except asyncio.TimeoutError:
            self._fail(state, run, HarnessError("timeout", "Pi task exceeded its host deadline.", backend="pi"))
        except asyncio.CancelledError:
            run.result = replace(run.result, status="cancelled")
        except TransportError as exc:
            self._fail(state, run, HarnessError(exc.code, self._safe(str(exc), state), backend="pi", details=exc.details))
        except HarnessError as exc:
            self._fail(state, run, exc)
        except Exception as exc:
            self._fail(state, run, HarnessError("malformed_event", self._safe(str(exc), state), backend="pi",
                                               details={"exception_type": type(exc).__name__}))
        finally:
            run.finishing = True
            try:
                await state.transport.close()
            except Exception as exc:
                self._fail(state, run, HarnessError("cleanup_failed", self._safe(str(exc), state), backend="pi"))
            diagnostic = self._safe(state.transport.stderr.decode("utf-8", "replace"), state)
            for error in run.result.errors:
                error.details.update(stderr=diagnostic, process=state.transport.inspect())
            run.result = replace(run.result, metadata={**run.result.metadata, "process": state.transport.inspect()})
            # Terminal status survives a full buffer; overflow itself is in result.errors.
            while run.queue.full():
                run.queue.get_nowait()
            run.sequence += 1
            run.queue.put_nowait(HarnessEvent("finished", state.session.id, run.result.run_id,
                sequence=run.sequence, metadata={"status": run.result.status}, backend="pi"))

    async def _consume(self, state: _PiSession, task: HarnessTask, run: _PiRun) -> None:
        if run.cancellation:
            run.result = replace(run.result, status="cancelled")
            return
        self._emit(state, run, "started", model=run.result.model)
        await state.transport.send({"id": task.id, "type": "prompt", "message": task.prompt})
        while True:
            frame = await state.transport.read()
            kind = frame.get("type")
            identity = frame.get("eventId")
            if identity is not None:
                identity = str(identity)
                if identity in run.seen:
                    continue
                run.seen.add(identity)
            if len(run.seen) > 4096:
                raise HarnessError("event_limit", "Pi event identity limit exceeded.", backend="pi")
            if kind == "response":
                if frame.get("success") is False:
                    raise HarnessError("rpc_error", self._safe(str(frame.get("error", "Pi command failed")), state), backend="pi",
                                       details={"command": self._safe(str(frame.get("command", "unknown")), state)})
                if frame.get("command") == "prompt" and (frame.get("data") or {}).get("disposition") in {"handled", "queued"}:
                    raise HarnessError("unexpected_disposition", "Pi did not start the submitted task.", backend="pi")
                continue
            if isinstance(kind, str) and kind.startswith(("tool_", "extension_", "bash_")):
                self._emit(state, run, "policy_violation", "Unexpected external tool activity", backend_event=kind)
                raise HarnessError("policy_violation", "Pi emitted tool activity despite the tool-free policy.", backend="pi")
            if kind == "message_update":
                update = frame.get("assistantMessageEvent") or {}
                if update.get("type") == "text_delta":
                    text = update.get("delta")
                    if not isinstance(text, str):
                        raise HarnessError("malformed_event", "Pi text delta is not a string.", backend="pi")
                    if len(run.result.text) + len(text) > 131072:
                        raise HarnessError("text_limit", "Pi response exceeds the bounded result limit.", backend="pi")
                    run.result = replace(run.result, text=run.result.text + self._safe(text, state))
                    self._emit(state, run, "text_delta", text)
            elif kind == "message_end" and (frame.get("message") or {}).get("role") == "assistant":
                message = frame["message"]
                fingerprint = hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
                if fingerprint in run.messages:
                    continue
                run.messages[fingerprint] = message
                if len(run.messages) > 64:
                    raise HarnessError("message_limit", "Pi assistant-message limit exceeded.", backend="pi")
                blocks = message.get("content") or []
                if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
                    raise HarnessError("malformed_event", "Pi assistant content is malformed.", backend="pi")
                text = "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")
                if any(block.get("type") == "toolCall" for block in blocks):
                    raise HarnessError("policy_violation", "Pi requested a tool in tool-free mode.", backend="pi")
                if len(text) > 131072:
                    raise HarnessError("text_limit", "Pi response exceeds the bounded result limit.", backend="pi")
                actual_model = message.get("model")
                if actual_model and actual_model != state.provider["model"]:
                    raise HarnessError("model_mismatch", "Pi response model differs from the requested model.", backend="pi")
                run.result = replace(run.result, text=self._safe(text, state))
                run.result = replace(run.result, usage=self._usage(run.messages.values()))
                if message.get("stopReason") == "error" or message.get("errorMessage"):
                    raise HarnessError("provider_error", self._safe(str(message.get("errorMessage", "Pi provider returned an error")), state), backend="pi")
                self._emit(state, run, "usage", usage=run.result.usage.__dict__)
            elif kind in {"auto_retry_start", "auto_retry_end", "compaction_start", "compaction_end"}:
                self._emit(state, run, "recovery" if kind.startswith("auto_retry") else "context", backend_event=kind)
                if kind == "auto_retry_end" and frame.get("success") is False:
                    raise HarnessError("provider_error", self._safe(str(frame.get("finalError", "Pi retry failed")), state), backend="pi")
            elif kind == "agent_settled":
                aborted = frame.get("aborted") is True or run.cancellation
                if not aborted and not run.messages:
                    raise HarnessError("missing_completion", "Pi settled without a completed assistant response.", backend="pi")
                run.result = replace(run.result, status="cancelled" if aborted else "completed",
                    completion=HarnessCompletionEvidence(False, (), "backend_finished_unverified"))
                break
            elif kind in {"agent_end", "agent_start", "turn_start", "turn_end", "message_start", "queue_update"}:
                self._emit(state, run, "step", backend_event=kind)
            elif not isinstance(kind, str):
                raise HarnessError("malformed_event", "Pi event has no string type.", backend="pi")
            else:
                self._emit(state, run, "backend_event", backend_event=kind)

    def _fail(self, state: _PiSession, run: _PiRun, error: HarnessError) -> None:
        run.result = replace(run.result, status="failed", errors=(*run.result.errors, error))
        try:
            self._emit(state, run, "error", str(error), code=error.code)
        except HarnessError:
            pass

    @staticmethod
    def _usage(messages) -> HarnessUsage:
        values = [message.get("usage") for message in messages]
        def count(key):
            counts = [value.get(key) if isinstance(value, dict) else None for value in values]
            return sum(counts) if counts and all(type(value) is int and value >= 0 for value in counts) else None
        return HarnessUsage(count("input"), count("output"), count("cacheRead"), None,
                            "backend_reported" if any(isinstance(value, dict) for value in values) else "unknown")

    def _run_state(self, session: HarnessSession, run_id: str) -> tuple[_PiSession, _PiRun]:
        state = self._state(session)
        if state.run is None or state.run.result.run_id != run_id:
            raise HarnessError("unknown_run", "Run does not belong to this session.", backend="pi")
        return state, state.run

    async def stream_events(self, session: HarnessSession, run_id: str):
        _, run = self._run_state(session, run_id)
        while True:
            event = await run.queue.get()
            yield event
            if event.type == "finished":
                return

    async def inspect_run(self, session: HarnessSession, run_id: str) -> HarnessResult:
        _, run = self._run_state(session, run_id)
        return run.result

    async def request_cancellation(self, session: HarnessSession, run_id: str) -> None:
        state, run = self._run_state(session, run_id)
        if run.result.status != "running":
            return
        run.cancellation = True
        if run.cancellation_task is None:
            run.cancellation_task = asyncio.create_task(self._cancel(state, run))
        await asyncio.shield(run.cancellation_task)

    async def _cancel(self, state: _PiSession, run: _PiRun) -> None:
        try:
            await state.transport.send({"type": "clear_queue", "id": uuid4().hex})
            await state.transport.send({"type": "abort", "id": uuid4().hex})
            if run.worker:
                await asyncio.wait_for(asyncio.shield(run.worker), timeout=2)
        except (TransportError, asyncio.TimeoutError):
            if run.worker and not run.worker.done():
                if not run.stop_requested and not run.finishing:
                    run.stop_requested = True
                    run.worker.cancel()
                await asyncio.shield(asyncio.gather(run.worker, return_exceptions=True))
        if run.result.status == "running":
            run.result = replace(run.result, status="cancelled")

    async def close_session(self, session: HarnessSession) -> None:
        state = self._sessions.get(session.id)
        if state is None:
            return
        if state.session is not session:
            raise HarnessError("unknown_session", "Session identity does not belong to this backend.", backend="pi")
        if state.close_task is None:
            state.close_task = asyncio.create_task(self._dispose(state))
        await asyncio.shield(state.close_task)

    async def _dispose(self, state: _PiSession) -> None:
        state.closed = True
        if state.run and state.run.worker and not state.run.worker.done():
            if not state.run.stop_requested and not state.run.finishing:
                state.run.stop_requested = True
                state.run.worker.cancel()
            await asyncio.shield(asyncio.gather(state.run.worker, return_exceptions=True))
        if state.run and state.run.cancellation_task:
            await asyncio.shield(asyncio.gather(state.run.cancellation_task, return_exceptions=True))
        await state.transport.close()
        # Only dispose temporary state after confirmed process/descriptor cleanup.
        state.provider.clear()
        state.temporary.cleanup()
        self._sessions.pop(state.session.id, None)


class CodexBackend:
    """Honest unavailable adapter; no fallback to unrestricted CLI execution."""
    name = "codex"

    def __init__(self, executable: str | None = None, expected_version: str | None = None,
                 mode: str = "unavailable"):
        self.executable = executable or shutil.which("codex")
        self.expected_version, self.mode = expected_version, mode

    def get_metadata(self) -> HarnessMetadata:
        return HarnessMetadata("codex", available=False, interface="official-app-server (not admitted)", license="Apache-2.0",
            reason=("Codex executable is missing." if not self.executable else
                    "Whole-runtime scope and configuration isolation have not been verified for this Codex build; execution is disabled."),
            security_notes=("Read-only defaults allow broad reads; restricted read roots are version dependent.",
                            "No credential import, login scraping or unrestricted shell fallback.",
                            "App-server support alone does not prove Firik policy compatibility."))

    def get_capabilities(self) -> dict[str, HarnessCapability]:
        return {name: HarnessCapability(name, None, "Not negotiated against an admitted installation")
                for name in ("streaming", "persistent_sessions", "external_model_selection", "custom_tools",
                             "tool_level_approvals", "cancellation", "subagents", "sandboxing", "structured_usage",
                             "resumable_execution", "context_inspection")}

    def validate_configuration(self, policy, provider=None):
        raise HarnessError("backend_unavailable", self.get_metadata().reason, backend="codex")

    async def initialize_session(self, *, policy, provider=None, session_id=None):
        self.validate_configuration(policy, provider)

    async def execute_task(self, session, task):
        self.validate_configuration(None)

    async def stream_events(self, session, run_id):
        self.validate_configuration(None)
        yield  # Keep the backend-neutral async-iterator contract even when unavailable.

    async def request_cancellation(self, session, run_id):
        self.validate_configuration(None)

    async def inspect_run(self, session, run_id):
        self.validate_configuration(None)

    async def close_session(self, session):
        return None
