"""Project command receipts for Electron, using the existing execution backends.

This is a command terminal, not an interactive PTY. Each explicit user command
gets a new process. Agent commands enter through their normal run approval path.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time

from firik_agent.core.execution import CommandRequest, ExecutionConfig, build_backend
from firik_agent.desktop_rpc.models import Ack, TerminalCommand, TerminalListResult
from firik_agent.projects.coding_runtime import CodingRunControl, command_policy_actions
from firik_agent.projects.indexer import safe_project_path


_ACTIVE = {"starting", "running", "stopping"}
_OUTPUT_LIMIT = 32768
_HISTORY_LIMIT = 10


@dataclass
class _Command:
    row: TerminalCommand
    output: dict[str, str] = field(default_factory=lambda: {"stdout": "", "stderr": ""})
    cancel: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None
    finalizing: bool = False


class TerminalRPC:
    def __init__(self, workspaces):
        self.workspaces = workspaces
        self.server, self.service = workspaces.server, workspaces.service
        self._commands: dict[str, _Command] = {}
        self._futures = {}

    def busy(self, project_id=None):
        with self.workspaces._lock:
            return any(record.row.source == "user" and record.row.status in _ACTIVE
                       and (project_id is None or record.row.project_id == project_id)
                       for record in self._commands.values())

    def _emit(self, record, event_type):
        if not self.workspaces._closed:
            self.server.emit("terminal", project_id=record.row.project_id,
                command_id=record.row.command_id, event_type=event_type)

    def _snapshot(self, record):
        row = record.row.model_copy(deep=True)
        row.command = self.server._clean(row.command, 8000)
        row.cwd = self.server._clean(row.cwd, 4096)
        row.stdout = self.server._clean(record.output["stdout"], _OUTPUT_LIMIT)
        row.stderr = self.server._clean(record.output["stderr"], _OUTPUT_LIMIT)
        row.error = self.server._clean(row.error, 2048)
        return row

    def _prune(self, project_id):
        project = [key for key, value in self._commands.items() if value.row.project_id == project_id]
        for key in project[:-_HISTORY_LIMIT]:
            if self._commands[key].row.status not in _ACTIVE:
                del self._commands[key]
        # This display history is deliberately ephemeral and globally bounded.
        for key in list(self._commands):
            if len(self._commands) <= 100:
                break
            if self._commands[key].row.status not in _ACTIVE:
                del self._commands[key]

    def list(self, project_id):
        if self.service.projects.db.get_project(project_id) is None:
            self.workspaces.fail("Proje bulunamadı.", -32004)
        with self.workspaces._lock:
            commands = [self._snapshot(record) for record in self._commands.values()
                        if record.row.project_id == project_id][-_HISTORY_LIMIT:]
        # Escaped control characters and Unicode can expand a JSON frame. Keep
        # aggregate history well below the transport's 2 MiB frame bound.
        while len(commands) > 1 and len(json.dumps([row.model_dump() for row in commands], ensure_ascii=False).encode()) > 1024 * 1024:
            commands.pop(0)
        prefs = self.service.projects.db.get_settings(project_id).coding_preferences
        config = ExecutionConfig(**dict(prefs.get("execution_config", {})))
        return TerminalListResult(commands=commands, busy=any(row.status in _ACTIVE for row in commands),
            backend=config.backend, boundary="sandbox" if config.backend == "docker" else "host",
            max_timeout=config.max_timeout)

    def _append(self, record, stream, text):
        if stream not in {"stdout", "stderr"}:
            return
        with self.workspaces._lock:
            available = max(0, _OUTPUT_LIMIT - len(record.output[stream]))
            previous = record.output[stream]
            record.output[stream] += str(text)[:available]
            changed = previous != record.output[stream]
            was_truncated = record.row.truncated
            record.row.truncated |= len(str(text)) > available
            changed |= was_truncated != record.row.truncated
        if changed:
            self._emit(record, "output")

    def start(self, p):
        from uuid import uuid4

        project = self.workspaces._project(p.project_id)
        with self.workspaces._project_lock(p.project_id):
            prefs = self.service.projects.db.get_settings(p.project_id).coding_preferences
            effective = self.workspaces._preferences(p.project_id)
            if not effective.allow_terminal or not effective.allow_reads:
                self.workspaces.fail("Bu projede komut çalıştırma veya dosya okuma izni kapalı.", -32003)
            config = ExecutionConfig(**dict(prefs.get("execution_config", {})))
            control = CodingRunControl("desktop-terminal", p.project_id, mode="assisted",
                permissions=effective.model_dump(), policy=dict(prefs.get("policy", {})),
                execution_config=config, excluded_paths=list(prefs.get("excluded_files", [])))
            if any(control.permission_decision(action) == "deny" for action in command_policy_actions(p.command, config)):
                self.workspaces.fail("Komutun shell, ağ veya Git işlemi proje politikasında engellendi.", -32003)
            if p.timeout > config.max_timeout:
                self.workspaces.fail(f"Bu projenin komut zaman sınırı {config.max_timeout:g} saniye.", -32602)
            directory = safe_project_path(project.local_path, p.cwd)
            if not directory.is_dir():
                self.workspaces.fail("Komut çalışma yolu mevcut bir proje klasörü olmalıdır.", -32602)
            relative = directory.relative_to(Path(project.local_path).resolve()).as_posix()
            with self.workspaces._lock:
                if sum(record.row.source == "user" and record.row.status in _ACTIVE for record in self._commands.values()) >= 4:
                    self.workspaces.fail("En fazla dört projede eşzamanlı terminal komutu çalıştırılabilir.")
                if self.busy(p.project_id) or any(state.busy and state.project_id == p.project_id for state in self.workspaces._states.values()):
                    self.workspaces.fail("Bu projede bir çalışma yürütülüyor; bitmesini bekleyin veya durdurun.")
                if self.workspaces._closed:
                    self.workspaces.fail("Çalışma alanı kapatılıyor.")
                record = _Command(TerminalCommand(command_id="terminal_" + uuid4().hex,
                    project_id=p.project_id, source="user", command=p.command, cwd=relative,
                    backend=config.backend, boundary="sandbox" if config.backend == "docker" else "host"))
                self._commands[record.row.command_id] = record
                self._prune(p.project_id)
                # Reservation and scheduling share the project lock with coding
                # start, Git execution and permission changes.
                operation = self._run(record, project.local_path, config, p.timeout)
                try:
                    future = self.service.runtime.run_coroutine(operation)
                except Exception:
                    operation.close()
                    del self._commands[record.row.command_id]
                    self.workspaces.fail("Terminal yürütme servisi kullanılamıyor.")
                self._futures[record.row.command_id] = future
                future.add_done_callback(lambda completed: self._completed(record, completed))
                result = self._snapshot(record)
        self._emit(record, "started")
        return result

    def _completed(self, record, future):
        with self.workspaces._lock:
            self._futures.pop(record.row.command_id, None)
            # A runtime may shut down before a submitted coroutine ever enters
            # its finally block. Release that reservation as well.
            if record.task is None and record.row.status in _ACTIVE:
                record.row.status = "cancelled" if future.cancelled() else "error"
                record.row.error = "Terminal yürütmesi tamamlanamadı."
        self._emit(record, "finished")

    async def _run(self, record, root, config, timeout):
        record.task = asyncio.current_task()
        started = time.monotonic()
        backend, workspace, command_task, output_task = None, None, None, None
        final_status = "error"
        try:
            if record.cancel.is_set():
                raise asyncio.CancelledError
            backend = build_backend(config)
            workspace = await backend.create_workspace(root)
            availability = await backend.prepare_environment(workspace)
            if not availability.available:
                raise RuntimeError(availability.diagnostic)
            if record.cancel.is_set():
                raise asyncio.CancelledError
            request = CommandRequest(command=record.row.command, cwd=record.row.cwd,
                timeout=timeout, command_id=record.row.command_id)
            record.row.status = "running"
            self._emit(record, "running")
            command_task = asyncio.create_task(backend.execute_command(workspace, request))

            async def stream():
                if not callable(getattr(backend, "stream_output", None)):
                    return
                while request.command_id not in getattr(backend, "commands", {}):
                    if command_task.done():
                        return
                    await asyncio.sleep(.01)
                async for event in backend.stream_output(request.command_id):
                    self._append(record, event.stream, event.text)

            output_task = asyncio.create_task(stream())
            result = await command_task
            await output_task
            with self.workspaces._lock:
                final_status = result.status
                record.row.exit_code = result.exit_code
                record.row.truncated |= result.truncated
                record.row.timed_out = result.timed_out
                record.row.error = result.cleanup_error
                record.output = {key: getattr(result, key)[:_OUTPUT_LIMIT] for key in ("stdout", "stderr")}
                record.row.truncated |= any(len(getattr(result, key)) > _OUTPUT_LIMIT for key in record.output)
        except asyncio.CancelledError:
            if command_task is not None:
                command_task.cancel()
                await asyncio.gather(command_task, return_exceptions=True)
            final_status = "cancelled"
        except Exception as error:
            final_status, record.row.error = "error", str(error)
        finally:
            with self.workspaces._lock:
                record.finalizing = True
                record.row.status = "stopping"
            if output_task is not None:
                collected = asyncio.gather(output_task, return_exceptions=True)
                try:
                    await asyncio.shield(collected)
                except asyncio.CancelledError:
                    await collected
            if backend is not None and workspace is not None:
                try:
                    # Destroying a Docker command workspace does not apply its
                    # artifacts to the user's checkout. Host cleanup never
                    # deletes the project directory.
                    cleanup = asyncio.create_task(backend.destroy_workspace(workspace, discard=True))
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                except Exception as error:
                    record.row.error = f"{record.row.error}\nTemizlik: {error}".strip()
                    if final_status == "completed":
                        final_status = "error"
            record.row.duration_seconds = round(time.monotonic() - started, 3)
            record.row.status = final_status
            record.task = None
            self._emit(record, "finished")

    def cancel(self, p):
        with self.workspaces._lock:
            record = self._commands.get(p.command_id)
            if record is None or record.row.project_id != p.project_id:
                self.workspaces.fail("Komut bu projeye ait değil.", -32004)
            if record.row.source != "user":
                self.workspaces.fail("Ajan komutunu durdurmak için kodlama çalışmasını durdurun.", -32003)
            if record.row.status not in _ACTIVE:
                self.workspaces.fail("Bu komut artık çalışmıyor.", -32004)
            if not record.cancel.is_set():
                record.cancel.set()
                record.row.status = "stopping"
                if record.task is not None and not record.finalizing:
                    self.service.runtime.loop.call_soon_threadsafe(record.task.cancel)
        self._emit(record, "stopping")
        return Ack()

    def coding_event(self, project_id, session_id, run_id, event):
        kind = event.type.value
        command_id = event.metadata.get("command_id")
        if kind not in {"command_started", "command_output", "command_finished"} or not command_id:
            return
        with self.workspaces._lock:
            if kind == "command_started":
                record = _Command(TerminalCommand(command_id=command_id, project_id=project_id,
                    source="agent", session_id=session_id, run_id=run_id, status="running",
                    command=str(event.metadata.get("command", event.content or "")),
                    cwd=str(event.metadata.get("cwd", ".")), backend=str(event.metadata.get("backend", "host")),
                    boundary=str(event.metadata.get("boundary", "host"))))
                self._commands[command_id] = record
                self._prune(project_id)
            else:
                record = self._commands.get(command_id)
                if record is None or record.row.project_id != project_id or record.row.run_id != run_id:
                    return
                if kind == "command_output":
                    self._append(record, event.metadata.get("stream", "stdout"), event.content or "")
                    return
                for name in ("status", "exit_code", "timed_out", "truncated", "duration_seconds", "error"):
                    if name in event.metadata:
                        setattr(record.row, name, event.metadata[name])
                for key in ("stdout", "stderr"):
                    value = str(event.metadata.get(key, record.output[key]))
                    record.output[key] = value[:_OUTPUT_LIMIT]
                    record.row.truncated |= len(value) > _OUTPUT_LIMIT
        self._emit(record, kind)

    def close(self):
        from firik_agent.desktop_rpc.models import TerminalCancelParams
        from firik_agent.desktop_rpc.server import RPCError
        with self.workspaces._lock:
            active = [record for record in self._commands.values()
                      if record.row.source == "user" and record.row.status in _ACTIVE]
        for record in active:
            try:
                self.cancel(TerminalCancelParams(project_id=record.row.project_id, command_id=record.row.command_id))
            except RPCError as error:
                # A command can finish between the snapshot and cancellation.
                # That stale receipt must not skip cleanup of the other commands.
                if error.code != -32004:
                    raise
        # Concurrent futures settle only after coroutine cleanup; unlike Future.cancel,
        # cancellation above cannot report completion while a process still runs.
        deadline = time.monotonic() + 15
        with self.workspaces._lock:
            futures = list(self._futures.values())
        for future in futures:
            try:
                future.result(timeout=max(.01, deadline - time.monotonic()))
            except Exception:
                pass
