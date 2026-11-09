"""Small, bounded JSON-RPC transport around the real application service.

Only an owning desktop process has the private stdio handles. Nothing listens
on the network. Model arguments never enter this host-control method table.
"""
from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from pydantic import ValidationError

from firik_agent.desktop_rpc import MAX_FRAME_BYTES, PROTOCOL_VERSION
from firik_agent.desktop_rpc.models import (
    Ack, ApprovalState, ChatMessage, ChatState, ChatSummary, DesktopEvent,
    HelloResult, METHODS, MCPState, ModelsResult, RunState, SettingsState, Snapshot,
)

logger = logging.getLogger(__name__)


class RPCError(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message


class DesktopServer:
    def __init__(self, service, output: BinaryIO):
        self.service, self.output = service, output
        self.instance_id = uuid4().hex
        self.sequence = 0
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._closing = False
        self._hello = False
        self._runs: dict[str, RunState] = {}
        self._run_generation = 0
        self._futures: dict[str, Future] = {}
        self._cancel_requested: set[str] = set()
        self._transcripts: dict[str, list[dict]] = {}
        self._approvals: dict[str, tuple[ApprovalState, Callable, threading.Timer]] = {}
        self._owned_chats: set[str] = set()
        self._model_refresh = False
        self._known_key = ""
        self._refresh_key()

    def _refresh_key(self) -> None:
        try:
            self._known_key = self.service.get_key() or ""
        except Exception:
            self._known_key = ""

    def send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > MAX_FRAME_BYTES:
            raise RPCError(-32001, "Yanıt boyut sınırını aştı.")
        with self._write_lock:
            if not self._closing:
                try:
                    self.output.write(data)
                    self.output.flush()
                except (OSError, ValueError):
                    self._closing = True

    def emit(self, kind: str, **fields) -> None:
        with self._lock:
            self.sequence += 1
            event = DesktopEvent(backend_instance_id=self.instance_id, sequence=self.sequence,
                                 kind=kind, **fields)
            self.send({"jsonrpc": "2.0", "method": "desktop.event", "params": event.model_dump()})

    def _clean(self, value: Any, limit: int = 16384) -> str:
        from firik_agent.projects.coding_sessions import redact_sensitive
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        if self._known_key:
            text = text.replace(self._known_key, "[gizlendi]")
        return str(redact_sensitive(text))[:limit]

    def settings(self) -> SettingsState:
        self._refresh_key()
        has_key = bool(self._known_key)
        return SettingsState(base_url=self.service.base_url, default_model=self.service.default_model,
                             timeout=self.service.timeout, has_api_key=has_key)

    def models(self) -> ModelsResult:
        names = list(dict.fromkeys([self.service.default_model, *self.service._cached_models]))
        return ModelsResult(models=names,
                            effort_levels={name: self.service.model_effort_levels(name) for name in names})

    def _history(self) -> list[ChatSummary]:
        rows = {row["session_id"]: row for row in self.service.list_chat_history()}
        for sid in self._owned_chats:
            if sid not in rows and sid in self.service.sessions:
                session = self.service.sessions[sid]
                rows[sid] = {"session_id": sid, "title": session.title,
                             "created_at": session.created_at, "updated_at": session.created_at}
        return [ChatSummary(conversation_id=row["session_id"], title=row["title"],
                            created_at=row["created_at"], updated_at=row["updated_at"])
                for row in sorted(rows.values(), key=lambda item: item["updated_at"], reverse=True)]

    def snapshot(self) -> Snapshot:
        with self._lock:
            mcps = [MCPState(name=row["name"], status=str(row.get("status", "disconnected")),
                             enabled=bool(row.get("enabled", True))) for row in self.service.get_mcp_servers()]
            return Snapshot(backend_instance_id=self.instance_id, sequence=self.sequence,
                            settings=self.settings(), conversations=self._history(),
                            runs=list(self._runs.values()),
                            approvals=[row[0] for row in self._approvals.values()],
                            models=self.models(), mcps=mcps)

    def _session(self, sid: str):
        # Existing histories are owned by this local user's backend. Unknown IDs
        # cannot silently create conversations or address another run.
        if sid not in self.service.sessions:
            if not self.service.chat_history.load(sid):
                raise RPCError(-32004, "Sohbet bulunamadı.")
            self.service.restore_chat_session(sid)
        self._owned_chats.add(sid)
        return self.service.sessions[sid]

    def _active_run(self, sid: str) -> RunState | None:
        return next((run for run in self._runs.values()
                     if run.conversation_id == sid and run.status == "running"), None)

    def chat(self, sid: str) -> ChatState:
        with self._lock:
            session = self._session(sid)
            rows = self._transcripts.get(sid)
            if rows is None:
                rows = self.service.load_chat_transcript(sid)
            visible = [(i, row) for i, row in enumerate(rows) if row.get("role") in {"user", "assistant"}]
            messages = [ChatMessage(id=f"{sid}:{i}", role=row["role"],
                                    content=self._display_content(row.get("content", ""))[:65536],
                                    pending=bool(row.get("pending", False)),
                                    timestamp=str(row.get("timestamp", ""))) for i, row in visible[-100:]]
            return ChatState(conversation_id=sid, title=session.title, model=session.model,
                             reasoning_effort=session.reasoning_effort, messages=messages,
                             earlier_messages=max(0, len(visible) - len(messages)),
                             active_mcp_servers=sorted(session.active_mcp_servers), run=self._active_run(sid))

    @staticmethod
    def _display_content(value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "\n".join(part.get("text", "[Görsel]") for part in value if isinstance(part, dict))
        return ""

    def _finish(self, run_id: str, status: str, text: str = "", error: str | None = None) -> None:
        with self._lock:
            run = self._runs[run_id]
            if run.status != "running":
                return
            if run_id in self._cancel_requested:
                status, error = "cancelled", None
                self._cancel_requested.discard(run_id)
            run.status, run.error = status, self._clean(error) if error else None
            rows = self._transcripts[run.conversation_id]
            if text:
                rows[-1]["content"] = text
            if not rows[-1]["content"]:
                rows[-1]["content"] = run.error or ("Yanıt durduruldu." if status == "cancelled" else "")
            rows[-1]["pending"] = False
            session = self.service.sessions[run.conversation_id]
            self.service.save_chat_transcript(session, rows)
            self._close_approvals(run_id)
            self.emit("run", conversation_id=run.conversation_id, run_id=run_id, run=run)
            self.emit("history")
            self._futures.pop(run_id, None)
            # Live snapshots remain bounded even after many conversations.
            finished = [key for key, item in self._runs.items() if item.status != "running"]
            for key in finished[:-32]:
                self._runs.pop(key, None)
            if self._transcripts.get(run.conversation_id) is rows:
                self._transcripts.pop(run.conversation_id, None)

    def _close_approvals(self, run_id: str) -> None:
        for approval_id, (state, decide, timer) in list(self._approvals.items()):
            if state.run_id == run_id:
                self._approvals.pop(approval_id)
                timer.cancel()
                decide(False)
                self.emit("approval_closed", run_id=run_id, conversation_id=state.conversation_id,
                          approval_id=approval_id)

    def _approval(self, run_id: str, payload: dict, decide: Callable) -> None:
        with self._lock:
            run = self._runs[run_id]
            if self._closing or run.status != "running":
                decide(False)
                return
            approval_id = uuid4().hex
            state = ApprovalState(approval_id=approval_id, run_id=run_id,
                                  conversation_id=run.conversation_id,
                                  tool_name=str(payload.get("tool_name", "Araç işlemi"))[:256],
                                  preview=self._clean(payload, 8192))

            def expire():
                with self._lock:
                    pending = self._approvals.pop(approval_id, None)
                    if pending:
                        pending[1](False)
                        self.emit("approval_closed", run_id=run_id, conversation_id=run.conversation_id,
                                  approval_id=approval_id)

            timer = threading.Timer(600, expire)
            timer.daemon = True
            self._approvals[approval_id] = (state, decide, timer)
            timer.start()
            self.emit("approval", conversation_id=run.conversation_id, run_id=run_id, approval=state)

    def start(self, params) -> RunState:
        with self._lock:
            session = self._session(params.conversation_id)
            if self._active_run(session.session_id):
                raise RPCError(-32009, "Bu sohbette bir yanıt zaten hazırlanıyor.")
            if sum(run.status == "running" for run in self._runs.values()) >= 8:
                raise RPCError(-32009, "Aynı anda en fazla 8 sohbet çalışabilir.")
            if not self.settings().has_api_key:
                raise RPCError(-32010, "Önce Ayarlar bölümünden API anahtarınızı kaydedin.")
            session.model = params.model
            levels = self.service.model_effort_levels(params.model)
            if params.reasoning_effort is not None and params.reasoning_effort not in levels:
                raise RPCError(-32602, "Bu model için doğrulanmış bir reasoning seviyesi seçin.")
            session.reasoning_effort = params.reasoning_effort
            rows = self.service.load_chat_transcript(session.session_id)
            timestamp = datetime.now(timezone.utc).isoformat()
            rows.extend([{"role": "user", "content": params.prompt, "timestamp": timestamp},
                         {"role": "assistant", "content": "", "timestamp": timestamp, "pending": True}])
            self._transcripts[session.session_id] = rows
            self.service.save_chat_transcript(session, rows)
            self._run_generation += 1
            run = RunState(run_id=uuid4().hex, generation=self._run_generation, conversation_id=session.session_id,
                           status="running", error=None)
            self._runs[run.run_id] = run
            last_save = time.monotonic()

            def event(evt):
                nonlocal last_save
                with self._lock:
                    if run.status != "running":
                        return
                    event_type = evt.type.value
                    content = None
                    if event_type == "text_delta":
                        content = evt.content or ""
                        rows[-1]["content"] += content
                    elif event_type in {"reasoning_delta", "error", "recovery"}:
                        content = self._clean(evt.content or "", 8192)
                    self.emit("agent", conversation_id=run.conversation_id, run_id=run.run_id,
                              event_type=event_type, content=content, tool_name=evt.tool_name, run=run)
                    if time.monotonic() - last_save > 1:
                        self.service.save_chat_transcript(session, rows)
                        last_save = time.monotonic()

            try:
                future = self.service.chat_agent_stream_async(
                    session.session_id, params.prompt, on_event=event,
                    on_done=lambda text: self._finish(run.run_id, "completed", text),
                    on_error=lambda error: self._finish(run.run_id, "failed", error=error),
                    on_approval=lambda payload, decide: self._approval(run.run_id, payload, decide))
                self._futures[run.run_id] = future

                def finished(result):
                    if result.cancelled():
                        self._finish(run.run_id, "cancelled")
                    elif result.exception() is not None:
                        self._finish(run.run_id, "failed", error="Yanıt hazırlanamadı.")

                future.add_done_callback(finished)
            except Exception:
                self._finish(run.run_id, "failed", error="Yanıt başlatılamadı.")
                raise
            self.emit("history")
            return run.model_copy()

    def dispatch(self, method: str, params):
        with self._lock:
            if method == "system.hello":
                self._hello = True
                from importlib.metadata import version
                return HelloResult(protocol_version=PROTOCOL_VERSION, backend_instance_id=self.instance_id,
                                   app_version=version("firik-agent"),
                                   capabilities=["chat", "history", "settings", "streaming", "cancel",
                                                 "mcp-selection", "approvals"])
            if not self._hello:
                raise RPCError(-32000, "Önce protokol sürümü doğrulanmalıdır.")
            if method == "state.snapshot":
                return self.snapshot()
            if method == "chat.create":
                session = self.service.get_or_create_session()
                if params.model:
                    session.model = params.model
                self._owned_chats.add(session.session_id)
                return self.chat(session.session_id)
            if method == "chat.get":
                return self.chat(params.conversation_id)
            if method == "chat.start":
                return self.start(params)
            if method == "run.cancel":
                run = self._runs.get(params.run_id)
                if run is None:
                    raise RPCError(-32004, "Çalışma bulunamadı.")
                if run.status == "running":
                    self._cancel_requested.add(run.run_id)
                    self._close_approvals(run.run_id)
                    self.service.cancel_active_stream(run.conversation_id)
                return Ack()
            if method in {"settings.update", "credentials.set"}:
                if any(run.status == "running" for run in self._runs.values()):
                    raise RPCError(-32009, "Ayarları değiştirmeden önce çalışan yanıtları durdurun.")
                if method == "settings.update":
                    self.service.update_settings(params.base_url, params.default_model, params.timeout)
                    # Provider instances hold their original endpoint/credentials.
                    self.service._provider_cfg = self.service.config["providers"][self.service.provider]
                    self.service._session_agents.clear()
                    self.service.agent = None
                    return self.settings()
                self.service.set_key(params.api_key)
                self.service._session_agents.clear()
                self.service.agent = None
                return Ack()
            if method == "settings.get":
                return self.settings()
            if method == "mcp.set":
                self._session(params.conversation_id)
                if self._active_run(params.conversation_id):
                    raise RPCError(-32009, "Araç seçimini yanıt tamamlandıktan sonra değiştirin.")
                if params.server_name not in self.service.mcp_manager.connections:
                    raise RPCError(-32004, "MCP sunucusu bulunamadı.")
                self.service.set_session_mcp(params.conversation_id, params.server_name, params.enabled)
                return self.chat(params.conversation_id)
            if method == "approval.resolve":
                pending = self._approvals.get(params.approval_id)
                if pending is None or pending[0].run_id != params.run_id:
                    raise RPCError(-32004, "Bu çalışma için güncel bir onay bulunamadı.")
                state, decide, timer = self._approvals.pop(params.approval_id)
                timer.cancel()
                decide(params.allowed and self._runs[state.run_id].status == "running")
                self.emit("approval_closed", conversation_id=state.conversation_id,
                          run_id=state.run_id, approval_id=state.approval_id)
                return Ack()
            if method == "system.shutdown":
                return Ack()
            raise RPCError(-32601, "Yöntem bulunamadı.")

    def handle(self, message: Any) -> bool:
        request_id = None
        try:
            if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                    or set(message) - {"jsonrpc", "id", "method", "params"}):
                raise RPCError(-32600, "Geçersiz RPC isteği.")
            request_id = message.get("id")
            if (type(request_id) not in {int, str} or
                    (isinstance(request_id, str) and len(request_id) > 256)):
                request_id = None
                raise RPCError(-32600, "İstek kimliği gereklidir.")
            method = message.get("method")
            if not isinstance(method, str) or method not in METHODS:
                raise RPCError(-32601, "Yöntem bulunamadı.")
            params_type, result_type = METHODS[method]
            try:
                params = params_type.model_validate(message.get("params", {}))
            except ValidationError:
                # Pydantic errors contain the supplied values, possibly a key.
                raise RPCError(-32602, "İstek alanları doğrulanamadı.") from None
            if method == "models.list":
                if not self._hello:
                    raise RPCError(-32000, "Önce protokol sürümü doğrulanmalıdır.")
                if self._model_refresh:
                    result = self.models()
                else:
                    self._model_refresh = True

                    def done(_):
                        self._model_refresh = False
                        self.send({"jsonrpc": "2.0", "id": request_id,
                                   "result": self.models().model_dump()})

                    self.service.fetch_models_async(done, lambda _: self.emit(
                        "warning", content="Model kataloğu yenilenemedi; kayıtlı liste gösteriliyor."))
                    return True
            else:
                result = self.dispatch(method, params)
            checked = result_type.model_validate(result)
            self.send({"jsonrpc": "2.0", "id": request_id, "result": checked.model_dump()})
            return method != "system.shutdown"
        except RPCError as error:
            self.send({"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": error.code, "message": error.message}})
        except Exception:
            # Do not leak exception payloads, credentials, or local paths.
            logger.warning("Desktop RPC operation failed (details omitted).")
            self.send({"jsonrpc": "2.0", "id": request_id,
                       "error": {"code": -32001, "message": "İşlem tamamlanamadı."}})
        return True

    def serve(self, source: BinaryIO) -> None:
        try:
            while not self._closing:
                frame = source.readline(MAX_FRAME_BYTES + 1)
                if not frame:
                    break
                if len(frame) > MAX_FRAME_BYTES or not frame.endswith(b"\n"):
                    self.send({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32600, "message": "RPC çerçeve sınırı aşıldı."}})
                    break
                try:
                    message = json.loads(frame, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (ValueError, UnicodeDecodeError):
                    self.send({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "Geçersiz JSON."}})
                    continue
                if not self.handle(message):
                    break
        finally:
            with self._lock:
                self._closing = True
                for run in list(self._runs.values()):
                    if run.status == "running":
                        self._cancel_requested.add(run.run_id)
                        self._close_approvals(run.run_id)
            self.service.shutdown()
            if hasattr(self.service.projects, "scheduler"):
                self.service.projects.scheduler.stop()
