"""Host-owned browser bridge on the desktop's existing AsyncRuntime."""
from __future__ import annotations

import asyncio
from collections import deque
import threading
from urllib.parse import urlsplit


def browser_origins(values):
    """Validate exact origins supplied by a person, including localhost ports."""
    if not isinstance(values, (list, tuple)) or len(values) > 32:
        raise ValueError("En fazla 32 tam tarayıcı kökeni girin.")
    clean = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Tarayıcı kökenleri metin olmalıdır.")
        value = value.strip()
        if any(ord(char) <= 32 for char in value) or "\\" in value:
            raise ValueError("Tarayıcı kökeninde boşluk veya denetim karakteri kullanmayın.")
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("Geçersiz tarayıcı kökeni bağlantı noktası.") from None
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Geçersiz tarayıcı kökeni bağlantı noktası.")
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("Tam http(s) kökeni girin; yol, sorgu ve kullanıcı bilgisi kullanmayın.")
        host = parsed.hostname.lower()
        host = f"[{host}]" if ":" in host else host.encode("idna").decode("ascii")
        authority = host + (f":{port}" if port and port != {"http": 80, "https": 443}[parsed.scheme] else "")
        origin = f"{parsed.scheme}://{authority}"
        if origin not in clean:
            clean.append(origin)
    return tuple(clean)


class BrowserBridge:
    def _init_browser_bridge(self):
        from firik_agent.browser.manager import BrowserManager
        from firik_agent.config import load_browser_config
        self.browser_manager = BrowserManager(load_browser_config(self.config))
        self._browser_activity = {}
        self._browser_shutdown_cleanup = None

    async def shutdown_browser_resources(self):
        """Close supervised execution resources before stopping shared Chromium."""
        filters = {}
        for session_id, tool_filter in list(getattr(self, "_coding_filters", {}).items()):
            entry = filters.setdefault(id(tool_filter), {"filter": tool_filter, "sessions": []})
            entry["sessions"].append(session_id)
        entries = list(filters.values())
        async def close_filter(entry):
            return await asyncio.wait_for(entry["filter"].close_execution(), timeout=20)
        tasks = [asyncio.create_task(close_filter(entry)) for entry in entries]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            # Runtime shutdown may cancel this helper after its caller's bound.
            # Preserve that uncertainty and still attempt shared process closure.
            results = [task.exception() or task.result() if task.done() and not task.cancelled()
                       else asyncio.CancelledError() for task in tasks]
        report = {"retained": False, "coding": [], "browser": {"retained": False}}
        for entry, result in zip(entries, results):
            if isinstance(result, BaseException):
                result = {"retained": True, "error_type": type(result).__name__}
            elif not isinstance(result, dict):
                result = {"retained": False}
            report["coding"].append({"sessions": entry["sessions"], "result": result})
            report["retained"] |= bool(result.get("retained") or result.get("requires_reconciliation"))
        try:
            await asyncio.wait_for(self.browser_manager.close_all(), timeout=8)
        except (Exception, asyncio.CancelledError) as error:
            report["browser"] = {"retained": True, "error_type": type(error).__name__}
            report["retained"] = True
        self._browser_shutdown_cleanup = report
        return report

    def set_session_browser(self, session_id, *, enabled, origins=(), private_origins=(), screenshots=False):
        """Explicit desktop grant; never callable by a model-facing tool."""
        session = self.get_or_create_session(session_id)
        origins, private = browser_origins(origins), browser_origins(private_origins)
        if not set(private).issubset(origins):
            raise ValueError("Yerel/özel kökenler ayrıca izin verilen kökenlerde bulunmalıdır.")
        session.browser_enabled = enabled is True
        session.browser_origins = origins
        session.browser_private_origins = private
        session.browser_screenshots = screenshots is True
        session.browser_approval_epoch += 1
        session.requires_external_tool_approval()
        if session.browser_enabled:
            # An explicit per-chat opt-in also activates this service instance.
            # Other conversations still require their own grants.
            self.browser_manager.config.enabled = True
        else:
            from firik_agent.browser.models import BrowserIdentity
            self.runtime.submit(self.browser_manager.close_owner(BrowserIdentity(session_id)))
        return self.session_browser_settings(session_id)

    def session_browser_settings(self, session_id):
        session = self.get_or_create_session(session_id)
        return {"enabled": session.browser_enabled, "origins": list(session.browser_origins),
                "private_origins": list(session.browser_private_origins), "screenshots": session.browser_screenshots}

    def _browser_grant(self, session):
        from firik_agent.browser.models import BrowserGrant
        return BrowserGrant(enabled=session.browser_enabled, origins=session.browser_origins,
            allow_private_origins=session.browser_private_origins, allow_screenshots=session.browser_screenshots)

    def _chat_browser_adapter(self, session, *, cancel_event=None, on_approval=None):
        from firik_agent.browser.models import BrowserIdentity
        from firik_agent.browser.tool_adapter import BrowserToolAdapter
        cancel_event = cancel_event or threading.Event()
        async def authorize(action, name, preview):
            if action in {"browser.observe", "browser.navigate", "browser.debug"}:
                return
            payload = {"session_id": session.session_id, "tool_name": name,
                       "arguments": preview, "reason": "Tarayıcı işlemini ve hedefi inceleyin."}
            try:
                allowed = await asyncio.wait_for(self._voice_tool_approval(payload, on_approval, cancel_event), timeout=120)
            except asyncio.TimeoutError:
                allowed = False
            if not allowed:
                raise PermissionError("Tarayıcı işlemi onaylanmadı veya onay süresi doldu.")
        return BrowserToolAdapter(self.browser_manager, BrowserIdentity(session.session_id),
            lambda: self._browser_grant(session), authorize=authorize, cancelled=cancel_event)

    def _owned_browser_adapter(self, session_id, *, on_approval=None):
        coding = getattr(self, "_coding_filters", {}).get(session_id)
        if coding is not None and getattr(coding, "browser_adapter", None) is not None:
            if session_id not in getattr(self, "_streams", {}):
                raise PermissionError("Kodlama tarayıcısı yalnız etkin çalışma sırasında kullanılabilir.")
            return coding.browser_adapter
        if session_id in getattr(self, "_coding_session_projects", {}):
            raise PermissionError("Proje tarayıcısı yetkili bir kodlama çalışması sırasında kullanılabilir.")
        return self._chat_browser_adapter(self.get_or_create_session(session_id), on_approval=on_approval)

    def browser_command_async(self, session_id, operation, arguments=None, *, on_done=None, on_error=None, on_approval=None):
        async def run():
            from firik_agent.core.types import ToolCall, ToolCallFunction
            import json
            import uuid
            adapter = self._owned_browser_adapter(session_id, on_approval=on_approval)
            coding = getattr(self, "_coding_filters", {}).get(session_id)
            if operation == "browser_resume_after_handoff":
                if coding is not None:
                    await coding.control.authorize("browser.authenticated", operation,
                        {"session_id": session_id}, "Kullanıcı tarayıcı kontrolünü ajana geri veriyor.")
                result = await adapter.host_resume()
            else:
                call = ToolCall(id=uuid.uuid4().hex,
                    function=ToolCallFunction(name=operation, arguments=json.dumps(arguments or {})))
                result = (await coding.execute(call, self.get_or_create_session(session_id))
                          if coding is not None else await adapter.execute(call))
            self._browser_activity.setdefault(session_id, deque(maxlen=80)).append({
                "operation": operation, "is_error": bool(getattr(result, "is_error", False))})
            return result
        return self.runtime.submit(run(),
            on_done=(lambda result: self._dispatch(on_done, result)) if on_done else None,
            on_error=(lambda error: self._dispatch(on_error, str(error))) if on_error else None)

    def browser_preview_async(self, session_id, *, on_done, on_error):
        async def run():
            adapter = self._owned_browser_adapter(session_id)
            return await self.browser_manager.preview(adapter.identity, adapter.grant_provider())
        return self.runtime.submit(run(), on_done=lambda data: self._dispatch(on_done, data),
            on_error=lambda error: self._dispatch(on_error, str(error)))

    async def _await_browser_handoff(self, session_id, cancel_event):
        """Suspend the native event generator until an explicit host resume."""
        adapter = self._owned_browser_adapter(session_id)
        while True:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            if not adapter.is_tool_allowed("browser_status"):
                raise PermissionError("Tarayıcı izni elle kontrol sırasında kaldırıldı.")
            sessions = self.browser_manager.sessions(adapter.identity)
            if not sessions or sessions[0]["state"] in {"closing", "closed", "failed"}:
                raise PermissionError("Elle kontrol sırasında tarayıcı kapatıldı.")
            if sessions[0]["state"] != "manual_control":
                return
            await asyncio.sleep(.1)

    def close_browser_async(self, session_id):
        from firik_agent.browser.models import BrowserIdentity
        coding = getattr(self, "_coding_filters", {}).get(session_id)
        adapter = getattr(coding, "browser_adapter", None)
        identity = adapter.identity if adapter is not None else BrowserIdentity(session_id)
        return self.runtime.submit(self.browser_manager.close_owner(identity))

    def stop_browser_async(self, session_id):
        """Revoke this owner's live authority before closing pending actions."""
        coding = getattr(self, "_coding_filters", {}).get(session_id)
        if coding is not None:
            coding.control.permissions["allow_browser"] = False
        session = getattr(self, "sessions", {}).get(session_id)
        if session is not None:
            session.browser_enabled = False
        return self.close_browser_async(session_id)
