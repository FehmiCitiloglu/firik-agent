"""Bounded development servers on the existing authorized execution backend.

This module owns lifecycle, not user authorization. Its caller must authorize the
foreground shell command and exact origin using CodingRunControl before start.
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
from pathlib import Path
import shlex
import socket
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from firik_agent.browser.models import BrowserError, BrowserGrant
from firik_agent.browser.security import BrowserSecurityPolicy, origin_of, redact
from firik_agent.core.execution import CommandRequest, ExecutionConfig, ExecutionError, HostExecutionBackend
from firik_agent.projects.indexer import safe_project_path


class DevServerError(RuntimeError):
    def __init__(self, code, message, *, requires_reconciliation=False):
        super().__init__(message)
        self.code = code
        self.requires_reconciliation = requires_reconciliation


class DevServerSupervisor:
    """One foreground server, its own backend workspace, bounded lifetime/logs."""

    def __init__(self, root, execution_config=None, grant_provider=None, cancelled=None):
        self.root = Path(root).resolve()
        self.config = execution_config if isinstance(execution_config, ExecutionConfig) else ExecutionConfig(**(execution_config or {}))
        self.grant_provider, self.cancelled = grant_provider, cancelled
        self.backend = HostExecutionBackend(self.config) if self.config.backend == "host" else None
        self.policy = BrowserSecurityPolicy()
        self._lock = asyncio.Lock()
        self._backend_stop_lock = asyncio.Lock()
        self._workspace = self._task = self._monitor = self._collector = None
        self._command_id = self._command = self._origin = self._cwd = ""
        self._state = "idle"
        self._reason = ""
        self._result = None
        self._logs = ""
        self._truncated = False
        self._started_at = None
        self._ready_at = None
        self._lifetime = 0

    def _grant(self):
        try:
            grant = self.grant_provider() if self.grant_provider else BrowserGrant()
        except Exception:
            grant = BrowserGrant()
        return grant if isinstance(grant, BrowserGrant) else BrowserGrant()

    def _cancelled(self):
        return bool(self.cancelled is not None and self.cancelled.is_set())

    async def _authorized_origin(self, value, *, require_explicit_port=False):
        parsed = urlsplit(value)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or (require_explicit_port and parsed.port is None):
            raise DevServerError("invalid_origin", "Development servers require an exact HTTP(S) loopback origin with an explicit port.")
        origin = origin_of(value)
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
            if loopback:
                try:
                    answers = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo("localhost", parsed.port, type=socket.SOCK_STREAM), 2)
                    loopback = bool(answers) and all(ipaddress.ip_address(item[4][0]).is_loopback for item in answers)
                except (OSError, ValueError, asyncio.TimeoutError):
                    loopback = False
        if not loopback:
            raise DevServerError("origin_denied", "Managed development servers support explicitly granted loopback origins only.")
        grant = self._grant()
        if origin not in grant.origins or origin not in grant.allow_private_origins:
            raise DevServerError("permission_denied", "The development origin and port require a project browser grant and private-origin grant.")
        await self.policy.validate(origin, grant)
        if self._grant() != grant or self._cancelled():
            raise DevServerError("permission_denied", "Development server permission changed during validation.")
        return origin

    def _validate_command(self, command):
        if not isinstance(command, str) or not command.strip() or len(command) > 8000 or any(c in command for c in "\x00\r\n`") or "$(" in command:
            raise DevServerError("invalid_command", "Use a bounded, reviewed foreground command.")
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            raise DevServerError("invalid_command", "The foreground command has invalid quoting.") from None
        if not tokens or any(token and all(c in ";&|<>()" for c in token) for token in tokens):
            raise DevServerError("invalid_command", "Shell control operators and backgrounding are unavailable; use the cwd option and one foreground command.")
        if Path(tokens[0]).name.lower() in {"nohup", "setsid", "start", "start-process"}:
            raise DevServerError("invalid_command", "Detached development servers are unavailable.")

    async def _probe(self):
        await self._authorized_origin(self._origin)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=1.0) as client:
            response = await client.get(self._origin + "/")
        await self._authorized_origin(self._origin)
        return response.status_code

    async def _ensure_port_unused(self, origin):
        parsed = urlsplit(origin)
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), .5)
        except ConnectionRefusedError:
            return
        except (OSError, asyncio.TimeoutError):
            raise DevServerError("port_state_unknown", "Could not establish that the development port is unused.") from None
        writer.close()
        await writer.wait_closed()
        raise DevServerError("port_in_use", "This port already accepts connections. A managed server cannot claim an unrelated process.")

    async def _collect_logs(self):
        try:
            async for event in self.backend.stream_output(self._command_id):
                remaining = max(0, self.config.output_limit - len(self._logs))
                self._logs += event.text[:remaining]
                self._truncated |= len(event.text) > remaining
        except Exception:
            # The completed CommandResult remains the authoritative fallback.
            pass

    def _settle(self):
        if self._task is None or not self._task.done():
            return
        if self._process_alive():
            self._state, self._reason = "uncertain", "The backend command ended without confirming that its server process terminated."
            return
        if self._task.cancelled():
            if self._state not in {"stopped", "cancelled", "revoked", "uncertain"}:
                self._state = "cancelled"
            return
        try:
            self._result = self._task.result()
        except Exception:
            if self._state != "uncertain":
                self._state, self._reason = "failed", "The execution backend did not return a valid server result."
            return
        if self._result.cleanup_error:
            self._state, self._reason = "uncertain", "Development server process cleanup is unresolved."
        elif self._result.timed_out:
            self._state, self._reason = "expired", "The bounded development server lifetime ended."
        elif self._state not in {"stopped", "cancelled", "revoked"}:
            self._state = "exited" if self._result.exit_code == 0 else "failed"
            self._reason = "The foreground server process exited."

    async def _watch(self):
        try:
            while self._task is not None and not self._task.done():
                if self._cancelled():
                    self._state, self._reason = "cancelled", "Coding run cancelled."
                    await self._stop_command()
                    break
                try:
                    await self._authorized_origin(self._origin)
                except Exception:
                    self._state, self._reason = "revoked", "Development origin permission was revoked."
                    await self._stop_command()
                    break
                await asyncio.sleep(.1)
        finally:
            self._settle()

    def _metadata(self):
        self._settle()
        running = self._task is not None and not self._task.done()
        ready = running and self._state == "ready"
        result = self._result
        logs = self._logs or ((result.stdout + "\n" + result.stderr) if result else "")
        return {"status": self._state, "ready": ready, "passed": ready,
                "origin": self._origin, "command": redact(self._command, 8000), "cwd": self._cwd,
                "backend": self.config.backend, "execution_boundary": "host" if self.backend else "unavailable",
                "command_id": self._command_id, "workspace_id": self._workspace.workspace_id if self._workspace else None,
                "started_at": self._started_at, "ready_at": self._ready_at, "lifetime_seconds": self._lifetime,
                "exit_code": result.exit_code if result else None, "reason": self._reason,
                "logs": redact(logs, self.config.output_limit), "logs_truncated": self._truncated or bool(result and result.truncated),
                "requires_reconciliation": self._state == "uncertain"}

    async def start(self, command, cwd=".", *, origin, ready_timeout=15, lifetime=120):
        if self.backend is None:
            raise DevServerError("docker_ports_unsupported", "Managed development servers need a verified Docker published-port contract; host fallback is disabled.")
        self._validate_command(command)
        for name, value in (("ready_timeout", ready_timeout), ("lifetime", lifetime)):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0 or value > self.config.max_timeout:
                raise DevServerError("invalid_timeout", f"{name} exceeds the configured execution lifetime bound.")
        if ready_timeout > min(60, lifetime):
            raise DevServerError("invalid_timeout", "Readiness timeout must fit the server lifetime and be at most 60 seconds.")
        directory = safe_project_path(self.root, cwd)
        if not directory.is_dir():
            raise DevServerError("invalid_cwd", "Development server cwd must be an existing project directory.")
        normalized = await self._authorized_origin(origin, require_explicit_port=True)
        async with self._lock:
            self._settle()
            if self._state == "uncertain":
                raise DevServerError("reconciliation_required", "Reconcile the previous development server cleanup before starting another process.", requires_reconciliation=True)
            if self._task is not None and not self._task.done():
                if (command, str(directory.relative_to(self.root)), normalized) != (self._command, self._cwd, self._origin):
                    raise DevServerError("server_already_running", "This coding run already owns a different development server; stop it first.")
                status = await self.status()
                if not status["ready"]:
                    raise DevServerError("server_unhealthy", "The existing development server is not healthy.")
                return {**status, "reused": True}
            await self._stop_impl()
            await self._ensure_port_unused(normalized)
            await self._authorized_origin(origin)
            self._command, self._cwd, self._origin = command, str(directory.relative_to(self.root)), normalized
            self._state, self._reason = "starting", ""
            self._logs, self._truncated, self._result = "", False, None
            self._started_at, self._ready_at, self._lifetime = time.time(), None, lifetime
            try:
                self._workspace = await self.backend.create_workspace(self.root)
                availability = await self.backend.prepare_environment(self._workspace)
                if not availability.available:
                    raise DevServerError("backend_unavailable", "The selected execution backend is unavailable.")
                self._command_id = "devserver_" + uuid4().hex
                self._task = asyncio.create_task(self.backend.execute_command(self._workspace,
                    CommandRequest(command=command, cwd=self._cwd, timeout=lifetime, command_id=self._command_id)))
                await asyncio.sleep(0)  # let the backend register the command record
                self._collector = asyncio.create_task(self._collect_logs())
                self._monitor = asyncio.create_task(self._watch())
                deadline = time.monotonic() + ready_timeout
                last_status = None
                while time.monotonic() < deadline:
                    self._settle()
                    if self._task.done() or self._state != "starting":
                        raise DevServerError("server_exited", "Development server exited before readiness: " + redact(self._metadata()["logs"], 1500))
                    try:
                        last_status = await self._probe()
                    except httpx.HTTPError:
                        last_status = None
                    if last_status is not None and 200 <= last_status < 400 and not self._task.done() and self._state == "starting":
                        self._state, self._ready_at = "ready", time.time()
                        return {**self._metadata(), "http_status": last_status, "reused": False}
                    await asyncio.sleep(.1)
                raise DevServerError("readiness_timeout", "The development server did not return a successful HTTP readiness response within the bound.")
            except BaseException:
                cleanup = asyncio.create_task(self._stop_impl())
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # A second cancellation must not make the caller return
                    # while process-group cleanup still runs in the background.
                    await cleanup
                    raise
                raise

    async def status(self):
        self._settle()
        status = self._metadata()
        if status["ready"]:
            try:
                code = await self._probe()
                status = self._metadata()
                status.update(http_status=code, ready=status["ready"] and 200 <= code < 400,
                              passed=status["ready"] and 200 <= code < 400)
                if not status["ready"] and self._state == "ready":
                    status.update(status="unhealthy", reason="The server no longer returns a successful HTTP readiness response.")
            except (BrowserError, DevServerError):
                self._state, self._reason = "revoked", "Development origin permission was revoked."
                await self._stop_impl()
                status = self._metadata()
            except httpx.HTTPError:
                status.update(status="unhealthy", ready=False, passed=False, reason="The foreground server is running but HTTP readiness failed.")
        if self._workspace is not None and self.backend is not None:
            workspace = self._workspace
            try:
                backend_status = await self.backend.inspect_status(workspace)
                status["processes"] = backend_status.get("commands", [])
            except ExecutionError:
                if self._workspace is workspace:
                    raise
                status = self._metadata()
        return status

    async def _stop_command(self):
        async with self._backend_stop_lock:
            if self._task is None:
                return
            if self._task.done():
                self._assert_process_terminal()
                return
            try:
                await self.backend.cancel_command(self._command_id)
                if not self._task.done():
                    status = await self.backend.inspect_status(self._workspace)
                    known = any(item.get("command_id") == self._command_id for item in status.get("commands", []))
                    if not known:
                        self._task.cancel()  # cancelled before backend registration
                await asyncio.wait_for(asyncio.shield(asyncio.gather(self._task, return_exceptions=True)), 10)
                self._assert_process_terminal()
            except Exception:
                self._state, self._reason = "uncertain", "Development server process-group cleanup was not confirmed."
                raise DevServerError("cleanup_unresolved", self._reason, requires_reconciliation=True) from None

    def _process_alive(self):
        # HostExecutionBackend owns this record and process handle. A completed
        # command coroutine alone does not prove OS process termination: the
        # backend can return an error after a failed process-group stop.
        record = getattr(self.backend, "commands", {}).get(self._command_id)
        process = getattr(record, "process", None)
        return process is not None and process.returncode is None

    def _assert_process_terminal(self):
        if self._process_alive():
            self._state, self._reason = "uncertain", "Development server process termination was not confirmed."
            raise DevServerError("cleanup_unresolved", self._reason, requires_reconciliation=True)

    async def _stop_impl(self):
        await self._stop_command()
        self._settle()
        current = asyncio.current_task()
        for task in (self._monitor, self._collector):
            if task is not None and task is not current and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self._workspace is not None:
            try:
                await self.backend.destroy_workspace(self._workspace)
            except Exception:
                self._state, self._reason = "uncertain", "Development server workspace cleanup was not confirmed."
                raise DevServerError("cleanup_unresolved", self._reason, requires_reconciliation=True) from None
            self._workspace = None
        self._monitor = self._collector = None

    async def stop(self):
        # Cancellation reaches the backend even while start holds its readiness
        # lock, then the lock serializes final metadata and workspace cleanup.
        if self._state not in {"uncertain", "revoked", "cancelled", "expired", "failed"}:
            self._state = "stopped"
        if self._task is not None and not self._task.done():
            cleanup = asyncio.create_task(self._stop_command())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
        async with self._lock:
            await self._stop_impl()
            if self._state not in {"uncertain", "revoked", "cancelled", "expired", "failed"}:
                self._state, self._reason = "stopped", "Development server process group stopped."
            return {**self._metadata(), "passed": self._state != "uncertain", "stopped": self._state != "uncertain" and (self._task is None or self._task.done())}

    async def close(self):
        return await self.stop()
