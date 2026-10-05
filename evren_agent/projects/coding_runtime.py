"""Run-local coding tools and execution policy; terminal approval is not an OS sandbox."""
from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import difflib
import hashlib
import inspect
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from evren_agent.core.events import AgentEvent, AgentEventType
from evren_agent.core.session import ChatSession
from evren_agent.core.shell import run_shell
from evren_agent.core.tools import ToolRegistry
from evren_agent.core.types import Message, ToolCall, ToolDefinition, ToolResult
from evren_agent.projects.indexer import (IGNORED_DIRS, IGNORED_EXTENSIONS, is_sensitive_file,
                                          safe_project_path, sanitize_secrets)

logger = logging.getLogger(__name__)
MAX_FILE_BYTES = 1_048_576
MAX_OUTPUT_CHARS = 32_768
MODES = {"ask", "plan", "approved", "auto"}


def _redact_content(value: str) -> str:
    """Redact credential values even when source files use unquoted assignments."""
    value = re.sub(r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
                   lambda match: "\n".join("[GİZLENDİ]" for _ in match[0].splitlines()), value, flags=re.S)
    value = sanitize_secrets(value)
    value = re.sub(r"(?i)\b(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*([^\s,;]+)",
                   r"\1=[GİZLENDİ]", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [GİZLENDİ]", value)
    value = re.sub(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
                   "[GİZLENDİ]", value)
    value = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "[GİZLENDİ]", value)
    return value


def safe_text(value: str) -> str:
    return _redact_content(value)[:MAX_OUTPUT_CHARS]


def safe_payload(value: Any) -> Any:
    """Preserve structured JSON while masking nested credential values."""
    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, dict):
        return {key: "[GİZLENDİ]" if re.search(r"(?i)^(?:password|secret|api.?key|authorization|access_token|refresh_token)$", str(key))
                else safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_payload(item) for item in value]
    return value


def safe_serialized_text(value: str) -> str:
    """Structured tool responses remain valid JSON after credential filtering."""
    if value.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(safe_payload(json.loads(value)), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
    return safe_text(value)


def sanitize_message(message: Message) -> Message:
    copy = message.model_copy(deep=True)
    if isinstance(copy.content, str):
        copy.content = safe_serialized_text(copy.content) if copy.role == "tool" else _redact_content(copy.content)
    elif copy.content is not None:
        copy.content = safe_payload(copy.content)
    if copy.reasoning:
        copy.reasoning = _redact_content(copy.reasoning)
    for tool in copy.tool_calls or []:
        try:
            tool.function.arguments = json.dumps(safe_payload(json.loads(tool.function.arguments)), ensure_ascii=False)
        except (ValueError, TypeError):
            tool.function.arguments = safe_text(tool.function.arguments)
    for part in copy.parts or []:
        if part.text:
            part.text = safe_text(part.text)
        if part.metadata:
            part.metadata = safe_payload(part.metadata)
    return copy


def repair_tool_history(messages: list[Message]) -> None:
    """A cancelled call may have run: record unknown outcome without rerunning it."""
    repaired: list[Message] = []
    pending: dict[str, ToolCall] = {}
    def finish_pending():
        for call in pending.values():
            repaired.append(ToolResult(tool_call_id=call.id, name=call.function.name, is_error=True,
                content="Kesilen çalışmada araç sonucu kaydedilemedi. İşlem tekrarlanmadı; güncel dosya durumu yeniden doğrulanmalıdır.").to_message())
        pending.clear()
    for message in messages:
        if message.role != "tool":
            finish_pending()
        elif message.tool_call_id in pending:
            pending.pop(message.tool_call_id)
        repaired.append(message)
        if message.role == "assistant" and message.tool_calls:
            pending.update({call.id: call for call in message.tool_calls})
    finish_pending()
    messages[:] = repaired


def prepare_coding_messages(messages: list[Message], control: "CodingRunControl",
                            schemas: list[dict[str, Any]], output_tokens: int = 2048) -> list[Message]:
    """Bound model input by complete turns; full history remains in session storage."""
    safe = [sanitize_message(message) for message in messages]
    systems = [message for message in safe if message.role == "system"]
    conversation = [message for message in safe if message.role != "system"]
    window = control.capabilities.get("context_window")
    verified_window = isinstance(window, int) and not isinstance(window, bool) and window > 0
    schemas_chars = len(json.dumps(schemas, ensure_ascii=False))
    system_chars = sum(len(json.dumps(message.to_dict(), ensure_ascii=False)) for message in systems)
    # This is a conservative local estimate; provider tokenization is not available.
    budget = max(0, (window - output_tokens - 1024) * 3 - schemas_chars - system_chars) if verified_window else 16_000
    turns: list[list[Message]] = []
    for message in conversation:
        if message.role == "user" or not turns:
            turns.append([])
        turns[-1].append(message)
    def size(items):
        return sum(len(json.dumps(message.to_dict(), ensure_ascii=False)) for message in items)
    if not turns:
        return safe
    if size(turns[-1]) > budget:
        raise ValueError("Güncel istek veya araç çıktısı bağlam bütçesini aşıyor. İsteği küçültün veya daha geniş bağlamlı model seçin.")
    if size(conversation) <= budget:
        return safe
    state = {"state": control.state, "plan": control.plan,
             "verifications": [{key: entry.get(key) for key in ("kind", "criterion", "status", "command", "exit_code", "passed", "evidence_id")}
                               for entry in control.verifications],
             "decisions": control.decisions}
    state_text = json.dumps(safe_payload(state), ensure_ascii=False)
    summary_prefix = ("Önceki sohbet tam olarak kalıcı kayıtta korunur. Bağlam bütçesi nedeniyle eski tam turlar sıkıştırıldı. "
                      "Aşağıdakiler kayıtlı görev durumu ve doğrudan alıntılardır; yeni çıkarım değildir.\nGörev durumu:\n" + state_text + "\nÖnceki kullanıcı mesajları:\n")
    retained = turns[-1]
    removed = turns[:-1]
    available = budget - size(retained) - len(summary_prefix) - 200
    if available < 0:
        raise ValueError("Görev planı, kararlar ve doğrulamalar bağlam bütçesine sığmıyor; daha geniş bağlamlı model seçin.")
    excerpts = "\n".join(safe_text(str(turn[0].content))[:400] for turn in removed if turn[0].role == "user")
    excerpts = excerpts[-min(4000, available):] if available else ""
    summary = Message(role="system", content=summary_prefix + excerpts)
    control.emit(AgentEventType.TASK_STATE, "Eski sohbet turları bağlam için sıkıştırıldı; tam geçmiş kalıcı kayıtta korunuyor.",
                 state=control.state, compressed_turns=len(removed), budget_source="verified_window_estimate" if verified_window else "local_character_budget",
                 context_window=window if verified_window else None)
    return systems + [summary] + retained


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class _CodingRegistry(ToolRegistry):
    """Translate and redact handler errors before they reach logs or the model."""

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        name = tool_call.function.name
        try:
            handler = self._handlers.get(name)
            if handler is None:
                raise ValueError("Araç bulunamadı.")
            arguments = json.loads(tool_call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Araç parametreleri nesne olmalıdır.")
            schema = self._tools[name].parameters
            missing = set(schema.get("required", [])) - arguments.keys()
            extra = arguments.keys() - schema.get("properties", {}).keys()
            if missing:
                raise ValueError("Eksik araç parametresi: " + ", ".join(sorted(missing)))
            if extra:
                raise ValueError("Desteklenmeyen araç parametresi: " + ", ".join(sorted(extra)))
            types = {"string": str, "integer": int, "number": (int, float), "array": list, "object": dict, "boolean": bool}
            for key, argument in arguments.items():
                kind = schema["properties"][key].get("type")
                if kind in types and (not isinstance(argument, types[kind]) or (kind in {"integer", "number"} and isinstance(argument, bool))):
                    raise ValueError(f"Araç parametresinin türü geçersiz: {key}")
            if inspect.iscoroutinefunction(handler):
                value = await handler(**arguments)
            elif name in {"read_file", "list_files", "search_project", "search_symbols"}:
                value = await asyncio.to_thread(handler, **arguments)
            else:
                value = handler(**arguments)
            if isinstance(value, ToolResult):
                value.tool_call_id = tool_call.id
                value.content = safe_serialized_text(value.content)
                return value
            content = json.dumps(safe_payload(value), ensure_ascii=False) if isinstance(value, (dict, list)) else safe_text(str(value))
            failed = isinstance(value, dict) and value.get("passed") is False
            return ToolResult(tool_call_id=tool_call.id, name=name, content=content, is_error=failed)
        except asyncio.CancelledError:
            raise
        except FileNotFoundError:
            return ToolResult(tool_call_id=tool_call.id, name=name, content="Dosya veya çalışma klasörü bulunamadı.", is_error=True)
        except UnicodeError:
            return ToolResult(tool_call_id=tool_call.id, name=name, content="Dosya UTF-8 metin olarak okunamadı.", is_error=True)
        except Exception as exc:
            return ToolResult(tool_call_id=tool_call.id, name=name, content=safe_text(f"İşlem yapılamadı: {exc}"), is_error=True)


class CodingRunControl:
    """Thread-safe UI decisions and async checkpoints for one project/session run."""

    def __init__(self, session_id: str, project_id: str, task_id: Optional[str] = None,
                 mode: str = "approved", permissions: Optional[Dict[str, bool]] = None,
                 on_event: Optional[Callable[[AgentEvent], None]] = None,
                 run_id: Optional[str] = None):
        if mode not in MODES:
            raise ValueError("Geçersiz kodlama çalışma modu.")
        self.session_id, self.project_id, self.task_id = session_id, project_id, task_id
        self.mode = mode
        self.run_id = run_id or f"run_{uuid4().hex}"
        self.permissions = {"allow_reads": True, "allow_edits": True, "allow_deletes": True,
                            "allow_terminal": True, "allow_tests": True, "allow_mcp": False}
        for key, value in (permissions or {}).items():
            if key in self.permissions:
                if not isinstance(value, bool):
                    raise ValueError("İşlem izinleri doğru veya yanlış olmalıdır.")
                self.permissions[key] = value
        self.on_event = on_event
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self._lock = threading.RLock()
        self.pending_approvals: Dict[str, Dict[str, Any]] = {}
        self._decisions: Dict[str, bool] = {}
        self.state = "understanding"
        self.plan: list[dict[str, Any]] = []
        self.changes: list[dict[str, Any]] = []
        self.verifications: list[dict[str, Any]] = []
        self.failures: Dict[str, int] = {}
        self.capabilities: Dict[str, Any] = {}
        self.decisions: list[dict[str, Any]] = []

    def emit(self, event_type: AgentEventType, content: str = "", **metadata: Any) -> None:
        if self.on_event:
            event = AgentEvent(event_type, self.session_id, content=safe_text(content),
                               project_id=self.project_id, task_id=self.task_id,
                               metadata={"run_id": self.run_id, "project_id": self.project_id, "task_id": self.task_id, **metadata})
            try:
                self.on_event(event)
            except Exception:
                logger.exception("Kodlama olayı teslim edilemedi.")

    def set_state(self, state: str, content: str = "") -> None:
        with self._lock:
            self.state = state
        self.emit(AgentEventType.TASK_STATE, content, state=state)

    def pause(self) -> None:
        self.pause_event.set()
        self.set_state("paused", "Çalışma duraklatıldı; çalışan araç bitince yeni işleme geçilmez.")

    def resume(self) -> None:
        self.pause_event.clear()
        self.set_state("running", "Çalışmaya devam ediliyor.")

    def cancel(self) -> None:
        self.cancel_event.set()
        self.pause_event.clear()
        self.set_state("cancelled", "Çalışma kullanıcı tarafından durduruldu.")

    async def checkpoint(self) -> None:
        while self.pause_event.is_set() and not self.cancel_event.is_set():
            await asyncio.sleep(0.025)
        if self.cancel_event.is_set():
            raise asyncio.CancelledError

    def decide_approval(self, request_id: str, allowed: bool) -> bool:
        with self._lock:
            if request_id not in self.pending_approvals:
                return False
            self._decisions[request_id] = bool(allowed)
        return True

    async def request_approval(self, tool_name: str, arguments: dict[str, Any], reason: str) -> None:
        request_id = f"approval_{uuid4().hex}"
        if len(json.dumps(arguments, ensure_ascii=False)) > MAX_OUTPUT_CHARS:
            raise ValueError("Onaylanacak işlem çok büyük. İncelenebilir küçük işlemlere bölün.")
        display = safe_payload(arguments)
        with self._lock:
            self.pending_approvals[request_id] = {"tool_name": tool_name, "arguments": display,
                                                 "reason": reason, "created_at": time.time()}
        self.set_state("awaiting_approval", reason)
        self.emit(AgentEventType.APPROVAL_REQUIRED, reason, request_id=request_id,
                  tool_name=tool_name, arguments=display)
        try:
            while True:
                await self.checkpoint()
                with self._lock:
                    if request_id in self._decisions:
                        allowed = self._decisions.pop(request_id)
                        break
                await asyncio.sleep(0.025)
            self.emit(AgentEventType.APPROVAL_DECIDED, "İşlem onaylandı." if allowed else "İşlem reddedildi.",
                      request_id=request_id, allowed=allowed, tool_name=tool_name)
            self.decisions.append({"request_id": request_id, "tool_name": tool_name, "allowed": allowed,
                                   "arguments_hash": _digest(json.dumps(arguments, sort_keys=True).encode()), "timestamp": time.time()})
            self.set_state("running")
            if not allowed:
                raise PermissionError("Kullanıcı bu işleme izin vermedi.")
        finally:
            with self._lock:
                self.pending_approvals.pop(request_id, None)
                self._decisions.pop(request_id, None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"run_id": self.run_id, "session_id": self.session_id, "project_id": self.project_id, "task_id": self.task_id,
                    "mode": self.mode, "permissions": dict(self.permissions), "state": self.state,
                    "capabilities": dict(self.capabilities),
                    "decisions": [dict(decision) for decision in self.decisions],
                    "plan": [dict(p) for p in self.plan], "changes": [dict(c) for c in self.changes],
                    "verifications": [dict(v) for v in self.verifications],
                    "tests": [dict(v) for v in self.verifications if v.get("kind") == "test"],
                    "pending_approvals": dict(self.pending_approvals)}


@dataclass
class _Change:
    id: str
    path: str
    before: Optional[bytes]
    after: Optional[bytes]
    step_id: Optional[str]


class CodingToolFilter:
    """Owns an isolated registry. It never inherits unrestricted meta/plugin handlers."""

    READ_TOOLS = {"read_file", "list_files", "search_project", "search_symbols", "git_status", "git_diff"}
    EDIT_TOOLS = {"edit_file", "write_file", "apply_patch", "delete_file", "undo_change"}
    STATE_TOOLS = {"update_plan", "record_verification"}

    def __init__(self, root: str | Path, control: CodingRunControl,
                 mcp_registry: Optional[ToolRegistry] = None,
                 trusted_mcp_tools: Optional[set[str]] = None):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Proje kökü bir klasör olmalıdır.")
        safe_project_path(self.root, ".")
        self.control = control
        self.tools = _CodingRegistry()
        self.mcp_registry = mcp_registry
        self.trusted_mcp_tools = set(trusted_mcp_tools or ())
        self._changes: dict[str, _Change] = {}
        self._verified_evidence: set[str] = set()
        self._register_tools()

    def _resolve(self, raw: str, *, must_exist: bool = True) -> Path:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError("Geçerli bir proje yolu belirtin.")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        # Check lexical components before resolving: even an in-root symlink is rejected.
        candidate = Path(os.path.abspath(candidate))
        try:
            parts = candidate.relative_to(self.root).parts
        except ValueError:
            raise PermissionError("Proje dışındaki dosyalara erişim engellendi.") from None
        walked = self.root
        for part in parts:
            walked /= part
            if walked.is_symlink():
                raise PermissionError("Sembolik bağlantı üzerinden proje erişimi engellendi.")
            if part in {".git", ".aws", ".ssh", ".codex", ".agents"} or is_sensitive_file(part):
                raise PermissionError("Hassas dosya veya korumalı klasöre erişim engellendi.")
        resolved = candidate.resolve(strict=must_exist)
        if not resolved.is_relative_to(self.root):
            raise PermissionError("Proje dışındaki dosyalara erişim engellendi.")
        return resolved

    @contextmanager
    def _parent(self, path: Path):
        """Use no-follow dir descriptors on supporting systems to resist symlink races."""
        fds: list[int] = []
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                fds.append(fd)
                for part in path.relative_to(self.root).parts[:-1]:
                    fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    fds.append(fd)
                yield fd
            else:
                self._resolve(str(path), must_exist=False)
                yield None
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def _read_bytes(self, raw: str) -> bytes:
        path = self._resolve(raw)
        with self._parent(path) as parent:
            fd = os.open(path.name if parent is not None else path,
                         os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise PermissionError("Yalnızca bağımsız, normal proje dosyaları okunabilir.")
                if info.st_size > MAX_FILE_BYTES:
                    raise ValueError("Dosya çok büyük; en fazla 1 MiB dosya desteklenir.")
                data = handle.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                    raise ValueError("İkili veya çok büyük dosya işlenemez.")
                return data

    def _replace(self, path: Path, content: Optional[bytes], expected: Optional[bytes]) -> None:
        self._resolve(str(path), must_exist=False)
        if content is not None and len(content) > MAX_FILE_BYTES:
            raise ValueError("Yeni dosya içeriği 1 MiB sınırını aşamaz.")
        with self._parent(path) as parent:
            target = path.name if parent is not None else path
            # Validate the exact bytes again immediately before the mutation.
            if path.exists():
                if expected is None or self._read_bytes(str(path)) != expected:
                    raise ValueError("Dosya bu sırada değişti; kullanıcı düzenlemesi korunuyor.")
            elif expected is not None:
                raise ValueError("Dosya bu sırada silindi; işlem durduruldu.")
            if content is None:
                os.unlink(target, dir_fd=parent)
                return
            temp_name = f".evren-change-{uuid4().hex}.tmp"
            temporary = temp_name if parent is not None else path.parent / temp_name
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                         0o600, dir_fd=parent)
            try:
                if path.exists():
                    os.fchmod(fd, stat.S_IMODE(path.stat().st_mode))
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if expected is None:
                    # An exclusive hardlink publishes new files without overwriting a racing creator.
                    os.link(temporary, target, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                else:
                    if self._read_bytes(str(path)) != expected:
                        raise ValueError("Dosya bu sırada değişti; kullanıcı düzenlemesi korunuyor.")
                    os.replace(temporary, target, src_dir_fd=parent, dst_dir_fd=parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def _register(self, name: str, description: str, handler: Any,
                  props: dict[str, Any], required: list[str]) -> None:
        self.tools.register(ToolDefinition(name=name, description=description, source="builtin:coding",
                            parameters={"type": "object", "properties": props, "required": required,
                                        "additionalProperties": False}), handler)

    def _register_tools(self) -> None:
        s, n = {"type": "string"}, {"type": "integer"}
        self._register("read_file", "Proje dosyasını satır aralığı ve SHA-256 sürüm özetiyle oku.", self.read_file,
                       {"path": s, "start_line": n, "end_line": n}, ["path"])
        self._register("list_files", "Filtreli proje ağacını sınırlı ve kademeli listele.", self.list_files,
                       {"path": s, "limit": n, "offset": n}, [])
        self._register("search_project", "Kaynak dosya adlarında ve içeriğinde ara.", self.search_project,
                       {"query": s, "limit": n}, ["query"])
        self._register("search_symbols", "Kaynak kod sembollerini ve satırlarını bul.", self.search_symbols,
                       {"query": s, "limit": n}, ["query"])
        edit = {"path": s, "expected_hash": s, "step_id": s}
        self._register("edit_file", "Tam beklenen sürümde tek bir metin parçasını hedefli değiştir.", self.edit_file,
                       {**edit, "old_text": s, "new_text": s}, ["path", "expected_hash", "old_text", "new_text"])
        self._register("apply_patch", "Tek dosyalı, tam eşleşen unified diff yaması uygula.", self.apply_patch,
                       {**edit, "patch": s}, ["path", "expected_hash", "patch"])
        self._register("write_file", "Dosya oluştur veya beklenen sürüm kontrolüyle yaz; hedefli düzenlemeyi tercih et.", self.write_file,
                       {**edit, "content": s}, ["path", "content", "expected_hash"])
        self._register("delete_file", "Beklenen sürümdeki tek dosyayı kullanıcı onayıyla sil.", self.delete_file,
                       edit, ["path", "expected_hash"])
        self._register("undo_change", "Yalnız bu ajanın değişikliğini, eşzamanlı kullanıcı düzenlemesini koruyarak geri al.", self.undo_change,
                       {"change_id": s}, ["change_id"])
        command = {"command": s, "cwd": s, "timeout": {"type": "number"}}
        self._register("run_command", "Yerel terminal komutu; işletim sistemi sandboxı yoktur, her çağrı onay gerektirir.", self.run_command,
                       command, ["command"])
        self._register("run_tests", "Onaylı test komutunu çalıştır ve gerçek çıkış sonucunu kaydet.", self.run_tests,
                       command, ["command"])
        self._register("git_status", "Projenin Git çalışma ağacı durumunu salt okunur sorgula.", self.git_status, {}, [])
        self._register("git_diff", "Git farkını hassas dosyaları dışarıda bırakarak incele.", self.git_diff,
                       {"path": s}, [])
        self._register("update_plan", "Kabul kriterleri ve doğrulama yöntemleriyle görev adımlarını güncelle.", self.update_plan,
                       {"steps": {"type": "array", "items": {"type": "object"}}}, ["steps"])
        self._register("record_verification", "Kabul kriterini gerçek araç kanıtıyla ilişkilendir; doğrulanamayanı ayrı kaydet.", self.record_verification,
                       {"criterion": s, "status": s, "evidence_id": s, "detail": s}, ["criterion", "status"])

    def is_tool_allowed(self, name: str, session: ChatSession) -> bool:
        if session.session_id != self.control.session_id:
            return False
        if self.tools.get_tool(name):
            if name in self.READ_TOOLS and not self.control.permissions["allow_reads"]:
                return False
            mode_tools = self.READ_TOOLS if self.control.mode == "ask" else self.READ_TOOLS | {"update_plan"}
            if self.control.mode in {"ask", "plan"} and name not in mode_tools:
                return False
            if name in self.EDIT_TOOLS and not self.control.permissions["allow_edits"]:
                return False
            if name == "delete_file" and not self.control.permissions["allow_deletes"]:
                return False
            if name == "run_command" and not self.control.permissions["allow_terminal"]:
                return False
            if name == "run_tests" and not self.control.permissions["allow_tests"]:
                return False
            return True
        definition = self.mcp_registry.get_tool(name) if self.mcp_registry else None
        return bool(definition and definition.source.startswith("mcp:")
                    and name in self.trusted_mcp_tools and self.control.permissions["allow_mcp"]
                    and self.control.mode not in {"ask", "plan"}
                    and definition.source[4:] in session.active_mcp_servers)

    def get_schemas(self, session: ChatSession) -> list[dict[str, Any]]:
        definitions = self.tools.list_tools()
        if self.mcp_registry:
            definitions += [tool for tool in self.mcp_registry.list_tools() if not self.tools.get_tool(tool.name)]
        return [tool.to_schema() for tool in definitions if self.is_tool_allowed(tool.name, session)]

    async def execute(self, tool_call: ToolCall, session: ChatSession) -> ToolResult:
        name = tool_call.function.name
        failure_key = _digest(json.dumps([name, tool_call.function.arguments], sort_keys=True).encode())
        try:
            await self.control.checkpoint()
            if self.control.failures.get(failure_key, 0) >= 3:
                raise ValueError("Aynı işlem üç kez başarısız oldu; yöntem veya parametre değiştirin.")
            if not self.is_tool_allowed(name, session):
                raise PermissionError("Bu araç çalışma modunda veya oturumda izinli değil.")
            arguments = json.loads(tool_call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Araç parametreleri bir JSON nesnesi olmalıdır.")
            failure_key = _digest(json.dumps([name, arguments], sort_keys=True).encode())
            if self.control.failures.get(failure_key, 0) >= 3:
                raise ValueError("Aynı işlem üç kez başarısız oldu; yöntem veya parametre değiştirin.")
            for key in ("path", "cwd"):
                if key in arguments:
                    self._resolve(arguments[key], must_exist=name not in {"write_file"})
            if name in self.EDIT_TOOLS:
                changed_text = sum(len(arguments.get(key, "")) for key in ("old_text", "new_text", "content", "patch")
                                   if isinstance(arguments.get(key, ""), str))
                if changed_text > MAX_OUTPUT_CHARS:
                    raise ValueError("Değişiklik çok büyük; hedefli ve incelenebilir küçük yamalara bölün.")
            if name in {"run_command", "run_tests"} and len(str(arguments.get("command", ""))) > 8000:
                raise ValueError("Komut çok uzun; incelenebilir küçük komutlara bölün.")
            if name in self.EDIT_TOOLS:
                if not self.control.permissions["allow_edits"]:
                    raise PermissionError("Dosya değişikliği izni kapalı.")
                if name != "undo_change" and self.control.mode == "approved" and not self.control.plan:
                    raise PermissionError("Onaylı uygulamada önce kabul kriterleri ve doğrulama yöntemiyle görev planı oluşturun.")
                existing_overwrite = name == "write_file" and self._resolve(arguments["path"], must_exist=False).exists()
                if self.control.mode == "approved" or name == "delete_file" or existing_overwrite:
                    await self.control.request_approval(name, arguments, "Dosya değişikliğini uygulamadan önce onayınız gerekiyor.")
            elif name in {"run_command", "run_tests"}:
                permission = "allow_tests" if name == "run_tests" else "allow_terminal"
                if not self.control.permissions[permission]:
                    raise PermissionError("Bu terminal işleminin izni kapalı.")
                await self.control.request_approval(name, arguments,
                    "Komut yerel kullanıcı yetkileriyle çalışır; proje dışında erişim ve ağ kullanımı yapabilir. Bu komutu onaylayın.")
            elif not self.tools.get_tool(name):
                for key, value in arguments.items():
                    if key in {"file", "file_path", "directory", "root", "project_path", "working_directory"} and isinstance(value, str):
                        self._resolve(value, must_exist=False)
                await self.control.request_approval(name, arguments,
                    "Bu MCP çağrısının dış etkileri sunucuya bağlıdır; yalnız bu araç ve parametreler için onay gerekiyor.")
            await self.control.checkpoint()
            registry = self.tools if self.tools.get_tool(name) else self.mcp_registry
            if registry is self.tools:
                result = await registry.execute(tool_call)
            else:
                result = await asyncio.wait_for(registry.execute(tool_call), timeout=60)
            if not isinstance(registry, _CodingRegistry):
                result.content = safe_serialized_text(result.content)
            if result.is_error:
                self.control.failures[failure_key] = self.control.failures.get(failure_key, 0) + 1
            else:
                self.control.failures.pop(failure_key, None)
            return result
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.control.failures[failure_key] = self.control.failures.get(failure_key, 0) + 1
            return ToolResult(tool_call_id=tool_call.id, name=name, content="Araç çağrısı zaman aşımına uğradı.", is_error=True)
        except FileNotFoundError:
            self.control.failures[failure_key] = self.control.failures.get(failure_key, 0) + 1
            return ToolResult(tool_call_id=tool_call.id, name=name, content="Dosya veya çalışma klasörü bulunamadı.", is_error=True)
        except Exception as exc:
            self.control.failures[failure_key] = self.control.failures.get(failure_key, 0) + 1
            return ToolResult(tool_call_id=tool_call.id, name=name, content=safe_text(f"İşlem yapılamadı: {exc}"), is_error=True)

    def read_file(self, path: str, start_line: int = 1, end_line: Optional[int] = None) -> dict[str, Any]:
        if not isinstance(start_line, int) or isinstance(start_line, bool) or start_line < 1:
            raise ValueError("Başlangıç satırı pozitif tam sayı olmalıdır.")
        if end_line is None:
            end_line = start_line + 399
        if not isinstance(end_line, int) or isinstance(end_line, bool) or end_line < start_line or end_line - start_line > 999:
            raise ValueError("Satır aralığı en fazla 1000 satır içerebilir.")
        raw = self._read_bytes(path)
        lines = _redact_content(raw.decode("utf-8")).splitlines()
        selected: list[str] = []
        used, returned_end = 0, start_line - 1
        window = self.control.capabilities.get("context_window")
        text_limit = MAX_OUTPUT_CHARS // 2 if isinstance(window, int) and window > 0 else 8000
        for index, line in enumerate(lines[start_line - 1:end_line], start_line):
            numbered = f"{index}: {line}"
            if used + len(numbered) + 1 > text_limit:
                if not selected:
                    selected.append(numbered[:text_limit] + " [Satır kısaltıldı]")
                    returned_end = index
                break
            selected.append(numbered)
            used += len(numbered) + 1
            returned_end = index
        return {"path": str(self._resolve(path).relative_to(self.root)), "hash": _digest(raw),
                "total_lines": len(lines), "start_line": start_line,
                "end_line": returned_end, "truncated": returned_end < min(end_line, len(lines)),
                "next_line": returned_end + 1 if returned_end < min(end_line, len(lines)) else None,
                "content": safe_text("\n".join(selected))}

    def list_files(self, path: str = ".", limit: int = 200, offset: int = 0) -> dict[str, Any]:
        directory = self._resolve(path)
        if not directory.is_dir():
            raise ValueError("Dosya listesi için bir klasör belirtin.")
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("Liste sınırı 1–1000 arasında, başlangıcı sıfır veya büyük olmalıdır.")
        entries = []
        for p in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if p.is_symlink() or p.name in IGNORED_DIRS or p.name.startswith(".") or is_sensitive_file(p, project_root=self.root) or p.suffix in IGNORED_EXTENSIONS:
                continue
            entries.append({"path": str(p.relative_to(self.root)), "name": p.name, "is_dir": p.is_dir()})
        return {"entries": entries[offset:offset + limit], "next_offset": offset + limit if len(entries) > offset + limit else None}

    def _files(self):
        count = 0
        for base, directories, files in os.walk(self.root, followlinks=False):
            directories[:] = [d for d in directories if d not in IGNORED_DIRS and not d.startswith(".")
                              and not (Path(base) / d).is_symlink()]
            for name in sorted(files):
                path = Path(base) / name
                if path.is_symlink() or name.startswith(".") or is_sensitive_file(path, project_root=self.root) or path.suffix in IGNORED_EXTENSIONS:
                    continue
                if count >= 3000:
                    return
                count += 1
                yield path

    def search_project(self, query: str, limit: int = 50) -> dict[str, Any]:
        if not query.strip() or not 1 <= limit <= 200:
            raise ValueError("Arama metni ve 1–200 arasında sonuç sınırı belirtin.")
        matches = []
        needle = query.casefold()
        for path in self._files():
            rel = str(path.relative_to(self.root))
            try:
                content = _redact_content(self._read_bytes(rel).decode("utf-8"))
            except (OSError, ValueError, UnicodeError):
                continue
            if needle in rel.casefold():
                matches.append({"path": rel, "line": 1, "content": "Dosya adı eşleşti."})
            for line, text in enumerate(content.splitlines(), 1):
                if needle in text.casefold():
                    matches.append({"path": rel, "line": line, "content": safe_text(text[:500])})
                if len(matches) >= limit:
                    return {"matches": matches[:limit], "limited": True}
        return {"matches": matches[:limit], "limited": False}

    def search_symbols(self, query: str, limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ValueError("Sembol sonuç sınırı 1–200 arasında olmalıdır.")
        matches = []
        for path in self._files():
            rel = str(path.relative_to(self.root))
            try:
                text = self._read_bytes(rel).decode("utf-8")
                if path.suffix == ".py":
                    symbols = [(node.name, node.lineno) for node in ast.walk(ast.parse(text))
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
                else:
                    symbols = [(match.group(1), i) for i, line in enumerate(text.splitlines(), 1)
                               if (match := re.search(r"(?:class|function|interface|def|struct|enum)\s+(\w+)", line))]
                for name, line in symbols:
                    if query.casefold() in name.casefold():
                        matches.append({"path": rel, "name": name, "line": line})
                        if len(matches) >= limit:
                            return {"matches": matches, "limited": True}
            except (OSError, ValueError, SyntaxError, UnicodeError):
                continue
        return {"matches": matches, "limited": False}

    def _expected(self, path: str, expected_hash: str) -> tuple[Path, bytes]:
        target = self._resolve(path)
        before = self._read_bytes(path)
        if not expected_hash or _digest(before) != expected_hash:
            raise ValueError("Beklenen dosya sürümü değişti. Dosyayı yeniden okuyun; mevcut değişiklikler korunuyor.")
        return target, before

    def _record(self, path: Path, before: Optional[bytes], after: Optional[bytes], step_id: Optional[str]) -> dict[str, Any]:
        change_id = f"change_{uuid4().hex}"
        relative = str(path.relative_to(self.root))
        self._changes[change_id] = _Change(change_id, relative, before, after, step_id)
        diff = "".join(difflib.unified_diff((before or b"").decode("utf-8").splitlines(True),
                                          (after or b"").decode("utf-8").splitlines(True),
                                          fromfile=f"a/{relative}", tofile=f"b/{relative}"))
        change = {"change_id": change_id, "path": relative, "step_id": step_id,
                  "before_hash": _digest(before) if before is not None else None,
                  "after_hash": _digest(after) if after is not None else None,
                  "diff": safe_text(diff), "kind": "created" if before is None else "deleted" if after is None else "modified"}
        self.control.changes.append(change)
        self.control.set_state("implementing")
        self.control.emit(AgentEventType.FILE_CHANGED, f"Dosya değişti: {relative}", **change)
        return change

    def edit_file(self, path: str, expected_hash: str, old_text: str, new_text: str,
                  step_id: Optional[str] = None) -> dict[str, Any]:
        target, before = self._expected(path, expected_hash)
        original = before.decode("utf-8")
        if not old_text or original.count(old_text) != 1:
            raise ValueError("Değiştirilecek metin dosyada tam bir kez bulunmalıdır.")
        after = original.replace(old_text, new_text, 1).encode("utf-8")
        self._replace(target, after, before)
        return self._record(target, before, after, step_id)

    def write_file(self, path: str, content: str, expected_hash: str, step_id: Optional[str] = None) -> dict[str, Any]:
        target = self._resolve(path, must_exist=False)
        before = None
        if target.exists():
            target, before = self._expected(path, expected_hash)
        elif expected_hash != "new":
            raise ValueError("Yeni dosya oluşturmak için expected_hash='new' belirtin.")
        after = content.encode("utf-8")
        self._replace(target, after, before)
        return self._record(target, before, after, step_id)

    def apply_patch(self, path: str, expected_hash: str, patch: str, step_id: Optional[str] = None) -> dict[str, Any]:
        target, before = self._expected(path, expected_hash)
        source = before.decode("utf-8").splitlines(True)
        output: list[str] = []
        cursor, index, hunks = 0, 0, 0
        lines = patch.splitlines(True)
        while index < len(lines):
            line = lines[index]
            if line.startswith(("--- ", "+++ ")):
                header_path = line[4:].strip().split("\t", 1)[0]
                if header_path not in {path, f"a/{path}", f"b/{path}", "/dev/null"}:
                    raise ValueError("Yama başka bir dosyaya ait; yalnız belirtilen dosya işlenebilir.")
                index += 1
                continue
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not match:
                raise ValueError("Yama geçerli unified diff biçiminde olmalıdır.")
            old_start = int(match[1]) - (1 if int(match[1]) else 0)
            old_count = int(match[2]) if match[2] is not None else 1
            new_count = int(match[4]) if match[4] is not None else 1
            if old_start < cursor or old_start > len(source):
                raise ValueError("Yama satır aralığı dosyayla eşleşmiyor.")
            output.extend(source[cursor:old_start])
            cursor = old_start
            index += 1
            consumed, added = 0, 0
            while index < len(lines) and not lines[index].startswith("@@ "):
                body = lines[index]
                if body.startswith("\\ No newline at end of file"):
                    raise ValueError("Yeni satır işareti olmayan dosya için hedefli edit_file kullanın.")
                if not body or body[0] not in " +-":
                    raise ValueError("Yama satırı geçersiz.")
                if body[0] in " -":
                    if cursor >= len(source) or source[cursor] != body[1:]:
                        raise ValueError("Yama bağlamı dosyayla eşleşmiyor; mevcut değişiklikler korunuyor.")
                    cursor += 1
                    consumed += 1
                if body[0] in " +":
                    output.append(body[1:])
                    added += 1
                index += 1
            if consumed != old_count or added != new_count:
                raise ValueError("Yama satır sayıları eşleşmiyor.")
            hunks += 1
        if not hunks:
            raise ValueError("Yamada uygulanabilir değişiklik bulunamadı.")
        output.extend(source[cursor:])
        after = "".join(output).encode("utf-8")
        self._replace(target, after, before)
        return self._record(target, before, after, step_id)

    def delete_file(self, path: str, expected_hash: str, step_id: Optional[str] = None) -> dict[str, Any]:
        target, before = self._expected(path, expected_hash)
        self._replace(target, None, before)
        return self._record(target, before, None, step_id)

    def undo_change(self, change_id: str) -> dict[str, Any]:
        change = self._changes.get(change_id)
        if not change:
            raise ValueError("Bu oturuma ait geri alınabilir değişiklik bulunamadı.")
        target = self._resolve(change.path, must_exist=change.after is not None)
        self._replace(target, change.before, change.after)
        self._changes.pop(change_id)
        record = {"change_id": change_id, "path": change.path, "undone": True}
        self.control.emit(AgentEventType.FILE_CHANGED, f"Ajan değişikliği geri alındı: {change.path}", **record)
        for item in self.control.changes:
            if item["change_id"] == change_id:
                item["undone"] = True
        return record

    def undo_file(self, path: str) -> dict[str, Any]:
        """Explicit UI undo, limited to this run's latest recorded file change."""
        resolved = str(self._resolve(path, must_exist=False).relative_to(self.root))
        change = next((c for c in reversed(list(self._changes.values())) if c.path == resolved), None)
        if change is None:
            raise ValueError("Bu çalışmaya ait geri alınabilir dosya değişikliği bulunamadı.")
        return self.undo_change(change.id)

    async def run_command(self, command: str, cwd: str = ".", timeout: float = 60) -> dict[str, Any]:
        directory = self._resolve(cwd)
        if not directory.is_dir():
            raise ValueError("Komut çalışma yolu bir klasör olmalıdır.")
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise ValueError("Komut zaman aşımı 0–300 saniye arasında olmalıdır.")
        task = asyncio.create_task(run_shell(command, cwd=str(directory), timeout=timeout))
        try:
            while not task.done():
                if self.control.cancel_event.is_set():
                    task.cancel()
                    await task
                await asyncio.sleep(0.025)
            output = await task
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise
        match = re.match(r"Exit code (-?\d+):", output)
        code = int(match[1]) if match else None
        timed_out = output.startswith("Command timed out")
        translated = output.replace("Exit code ", "Çıkış kodu ", 1)
        if timed_out:
            translated = "Komut zaman aşımına uğradı; süreç ve alt süreçleri durduruldu.\n" + output.split("\n", 1)[-1]
        elif output.startswith("Command execution error:"):
            translated = "Komut başlatılamadı: " + output.split(":", 1)[-1]
        return {"command": safe_text(command), "cwd": str(directory.relative_to(self.root)),
                "exit_code": code, "timed_out": timed_out, "output": safe_text(translated),
                "passed": code == 0 and not timed_out}

    async def run_tests(self, command: str, cwd: str = ".", timeout: float = 60) -> ToolResult:
        self.control.set_state("verifying", "Doğrulama komutu çalıştırılıyor.")
        result = await self.run_command(command, cwd, timeout)
        result.update({"evidence_id": f"verify_{uuid4().hex}", "kind": "test", "executed": True})
        self.control.verifications.append(result)
        self._verified_evidence.add(result["evidence_id"])
        self.control.emit(AgentEventType.VERIFICATION, "Test başarılı." if result["passed"] else "Test başarısız veya çalıştırılamadı.", **result)
        return ToolResult(tool_call_id="", name="run_tests", content=json.dumps(result, ensure_ascii=False), is_error=not result["passed"])

    async def _git(self, *args: str) -> str:
        # Only fixed read-only argv are passed. No shell, external diff, pager or hooks.
        process = await asyncio.create_subprocess_exec("git", "--no-pager", "-c", "core.fsmonitor=false", *args,
            cwd=str(self.root), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        data = bytearray()
        async def drain(stream):
            while chunk := await stream.read(8192):
                data.extend(chunk[:max(0, MAX_OUTPUT_CHARS - len(data))])
        collector = asyncio.gather(process.wait(), drain(process.stdout), drain(process.stderr))
        try:
            await asyncio.wait_for(asyncio.shield(collector), timeout=15)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if process.returncode is None:
                process.kill()
            await collector
            raise
        if process.returncode != 0:
            return "Git deposu veya Git komutu kullanılabilir değil."
        return safe_text(data.decode("utf-8", errors="replace"))

    async def git_status(self) -> str:
        return await self._git("status", "--short", "--branch", "--untracked-files=normal", "--ignore-submodules=all")

    async def git_diff(self, path: Optional[str] = None) -> str:
        paths = [str(self._resolve(path).relative_to(self.root))] if path else [str(p.relative_to(self.root)) for p in self._files()]
        if not paths:
            return "İncelenebilir dosya farkı yok."
        return await self._git("diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--", *paths[:1000])

    def update_plan(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(steps, list) or len(steps) > 50:
            raise ValueError("Plan en fazla 50 adım içermelidir.")
        cleaned = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict) or not step.get("title"):
                raise ValueError("Her plan adımının başlığı olmalıdır.")
            status = step.get("status", "pending")
            if status not in {"pending", "in_progress", "completed", "blocked"}:
                raise ValueError("Plan adımı durumu geçersiz.")
            if self.control.mode == "plan" and status == "completed":
                raise ValueError("Planla modunda uygulama adımları tamamlanmış olarak işaretlenemez.")
            criteria, verification = step.get("acceptance_criteria", []), step.get("verification", "")
            evidence = step.get("evidence_ids", [])
            if not isinstance(criteria, list) or not all(isinstance(item, str) for item in criteria):
                raise ValueError("Kabul kriterleri metin listesinden oluşmalıdır.")
            if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
                raise ValueError("Doğrulama kanıtları kimlik listesinden oluşmalıdır.")
            if status == "completed" and (not criteria or not verification or not evidence
                    or any(item not in self._verified_evidence for item in evidence)
                    or any(not item.get("passed") for item in self.control.verifications
                           if item.get("kind") == "test" and item.get("evidence_id") in evidence)):
                raise ValueError("Tamamlanan adımın kabul kriterleri, doğrulama yöntemi ve başarılı gerçek araç kanıtı olmalıdır.")
            cleaned.append({"id": str(step.get("id", f"step_{index + 1}")), "title": safe_text(str(step["title"])),
                            "status": status, "acceptance_criteria": [safe_text(str(c)) for c in criteria],
                            "verification": safe_text(str(verification)), "evidence_ids": list(evidence)})
        if len({step["id"] for step in cleaned}) != len(cleaned):
            raise ValueError("Plan adımı kimlikleri birbirinden farklı olmalıdır.")
        self.control.plan = cleaned
        self.control.set_state("planning")
        self.control.emit(AgentEventType.PLAN_UPDATED, "Görev planı güncellendi.", steps=cleaned)
        return {"steps": cleaned}

    def record_verification(self, criterion: str, status: str, evidence_id: str = "", detail: str = "") -> dict[str, Any]:
        if status not in {"passed", "failed", "unverified"}:
            raise ValueError("Doğrulama durumu passed, failed veya unverified olmalıdır.")
        evidence = next((v for v in self.control.verifications if v.get("evidence_id") == evidence_id), None)
        if status == "passed" and (evidence_id not in self._verified_evidence or not evidence or not evidence.get("passed")):
            raise ValueError("Başarılı kriter gerçek başarılı araç kanıtı gerektirir.")
        result = {"criterion": safe_text(criterion), "status": status, "evidence_id": evidence_id,
                  "detail": safe_text(detail), "kind": "criterion"}
        self.control.verifications.append(result)
        self.control.emit(AgentEventType.VERIFICATION, "Kabul kriteri kaydedildi.", **result)
        return result
