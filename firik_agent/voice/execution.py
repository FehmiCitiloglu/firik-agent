"""Request-local voice approval around the existing tool executor.

The filter never installs tools, creates sessions, changes tool permissions or
executes handlers itself. Unproven effects require one trusted UI decision for
the exact tool/arguments, in addition to the delegate's existing permission gate.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from uuid import uuid4

from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.types import ToolCall, ToolResult
from firik_agent.mcp.models import redact_secrets

MAX_APPROVAL_BYTES = 64 * 1024

# These names belong to fixed application handlers. An MCP/plugin named
# read_file is still unknown: source provenance is part of the allowlist.
_READ_ONLY = {
    "meta:agent": frozenset({"read_file", "list_skills", "list_plugins", "list_mcp_servers",
                              "get_environment_info"}),
    "builtin:coding": frozenset({"read_file", "list_files", "search_project", "search_symbols",
                                 "git_status", "git_diff", "inspect_execution",
                                 "collect_execution_artifacts", "discover_tools"}),
}


def _safe_arguments(value):
    if isinstance(value, dict):
        return {key: "<REDACTED>" if re.search(r"(?i)(password|secret|token|credential|api.?key)", str(key))
                else _safe_arguments(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_arguments(item) for item in value]
    return redact_secrets(value) if isinstance(value, str) else value


def voice_metadata(request, *, session_id=None, project_id=None, target=None):
    """Validate a router-created scope without generating any application ID."""
    from firik_agent.voice.requests import VoiceConfirmation, VoiceRequest
    if not isinstance(request, VoiceRequest) or request.origin != "voice":
        raise ValueError("Geçerli bir tamamlanmış ses isteği gerekli.")
    guard = request._dispatch_guard
    if getattr(guard, "issued_request", None) is not request:
        raise ValueError("Ses isteği yönlendirici tarafından oluşturulmalı; değiştirilmiş istek kabul edilmez.")
    if request.requires_confirmation and (request.confirmation != VoiceConfirmation.CONFIRMED
            or not guard.ui_confirmed or getattr(guard, "confirmed_request", None) is not request):
        raise ValueError("Bu ses isteği önce güvenilir transkript ekranında incelenmelidir.")
    if session_id is not None and request.session_id != session_id:
        raise ValueError("Ses isteği başka bir sohbete ait.")
    if project_id is not None and request.project_id != project_id:
        raise ValueError("Ses isteği başka bir projeye ait.")
    if target is not None and request.intended_target != target:
        raise ValueError("Ses isteğinin hedefi değiştirilemez.")
    if request.cancellation_token.is_set():
        raise asyncio.CancelledError
    return request.to_metadata()


class VoiceToolFilter:
    """Narrow a session's actual filter; never grant tools or delegate approval."""
    def __init__(self, delegate, request, approve=None, cancel_event=None, on_event=None):
        self.delegate, self.request = delegate, request
        self.approve, self.on_event = approve, on_event
        self.cancel_event = cancel_event or threading.Event()
        voice_metadata(request)

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def get_schemas(self, session):
        if session.session_id != self.request.session_id:
            return []
        return self.delegate.get_schemas(session)

    def is_tool_allowed(self, name, session):
        return session.session_id == self.request.session_id and self.delegate.is_tool_allowed(name, session)

    def _cancelled(self):
        return self.cancel_event.is_set() or self.request.cancellation_token.is_set()

    def _emit(self, event_type, *, payload, allowed=None):
        if self.on_event is not None:
            metadata = {**self.request.to_metadata(), "request_id": payload["request_id"],
                        "tool_name": payload["tool_name"],
                        "arguments": _safe_arguments(payload["arguments"])}
            if allowed is not None:
                metadata["allowed"] = allowed
            self.on_event(AgentEvent(event_type, self.request.session_id,
                project_id=self.request.project_id,
                tool_name=payload["tool_name"],
                content="Ses isteğinin önerdiği tam araç işlemi için ekran onayı gerekiyor."
                    if allowed is None else "İşlem onaylandı." if allowed else "İşlem reddedildi.",
                metadata=metadata))

    def _registry(self, name):
        finder = getattr(self.delegate, "_registry_for", None)
        if finder:
            return finder(name)
        finder = getattr(self.delegate, "get_tool_registry", None)
        if finder:
            return finder(name)
        for attribute in ("tools", "tool_registry", "mcp_registry"):
            registry = getattr(self.delegate, attribute, None)
            if registry is not None and registry.get_tool(name) is not None:
                return registry
        return None

    def _definition(self, name):
        registry = self._registry(name)
        return registry.get_tool(name) if registry is not None else None

    def _handler(self, name):
        registry = self._registry(name)
        return getattr(registry, "_handlers", {}).get(name)

    def _error(self, call, message):
        return ToolResult(tool_call_id=call.id, name=call.function.name,
            content=message, is_error=True,
            metadata={"failure_kind": "permission_denied", "request_phase": "tool_not_dispatched",
                      **self.request.to_metadata()})

    async def _ask(self, payload):
        if self.approve is None:
            return False
        result = self.approve(payload)
        if not inspect.isawaitable(result):
            # UI decisions cross the runtime asynchronously. A boolean from a
            # transcript handler is not an approval implementation.
            return False
        pending = asyncio.ensure_future(result)
        try:
            while not pending.done():
                if self._cancelled():
                    raise asyncio.CancelledError
                await asyncio.wait({pending}, timeout=.025)
            return pending.result() is True
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def execute(self, tool_call: ToolCall, session):
        voice_metadata(self.request, session_id=session.session_id)
        if self._cancelled():
            raise asyncio.CancelledError
        tool_call = tool_call.model_copy(deep=True)
        name = tool_call.function.name
        if not self.is_tool_allowed(name, session):
            return self._error(tool_call, "Bu araç seçili sohbetin izinleri kapsamında değil.")
        try:
            raw = tool_call.function.arguments
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_APPROVAL_BYTES:
                return self._error(tool_call, "İncelenecek araç işlemi çok büyük veya geçersiz.")
            arguments = json.loads(raw or "{}", parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(arguments, dict):
                return self._error(tool_call, "Araç parametreleri tam bir JSON nesnesi olmalıdır.")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return self._error(tool_call, "Araç parametreleri geçersiz; işlem başlatılmadı.")
        definition = self._definition(name)
        handler = self._handler(name)
        impact = getattr(self.request.impact, "value", self.request.impact)
        readonly = bool(impact != "high" and definition and name in _READ_ONLY.get(definition.source, ()))
        if not readonly:
            payload = {"request_id": "voice_approval_" + uuid4().hex, "tool_name": name,
                       "arguments": arguments, "reason": "Ses isteği: önerilen bu işlemi ekrandan inceleyip onaylayın.",
                       **self.request.to_metadata()}
            self._emit(AgentEventType.APPROVAL_REQUIRED, payload=payload)
            allowed = False
            exact = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            try:
                allowed = await self._ask(payload)
                if (payload["tool_name"] != name
                        or json.dumps(payload["arguments"], ensure_ascii=False, sort_keys=True) != exact):
                    allowed = False
            except asyncio.CancelledError:
                raise
            except Exception:
                allowed = False
            finally:
                self._emit(AgentEventType.APPROVAL_DECIDED, payload=payload, allowed=allowed)
            if not allowed:
                return self._error(tool_call, "Bu ses kaynaklı araç işlemi ekran üzerinden onaylanmadı.")
        if self._cancelled():
            raise asyncio.CancelledError
        # MCP toggles and tool lists can change while the approval is visible.
        if not self.is_tool_allowed(name, session):
            return self._error(tool_call, "İşlem onay beklerken sohbet izinleri değişti.")
        if self._definition(name) is not definition or self._handler(name) is not handler:
            return self._error(tool_call, "İşlem onay beklerken araç tanımı değişti.")
        return await self.delegate.execute(tool_call, session)
