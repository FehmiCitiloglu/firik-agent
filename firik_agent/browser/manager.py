"""One async owner for ephemeral Chromium sessions and validated commands.

The service executes commands; it has no model/provider or reasoning loop.
Network interception is defense in depth, explicitly not process containment.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import urljoin

from firik_agent.browser.artifacts import BrowserArtifacts
from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity, BrowserSession, BrowserTab, DiagnosticBuffer
from firik_agent.browser.security import BrowserSecurityPolicy, origin_of, redact, sanitized_url


INTERACTIONS = frozenset({"click", "fill", "type", "press", "select", "check", "hover", "scroll"})
NAVIGATIONS = frozenset({"navigate", "back", "forward", "reload"})
READS = frozenset({"status", "list_tabs", "snapshot", "screenshot", "extract_text", "get_links", "get_forms",
                  "get_console", "get_page_errors", "get_network_errors", "wait_for"})
_FIELDS = {
    "start": {"headless", "width", "height"}, "status": set(), "close": set(), "list_tabs": set(),
    "new_tab": {"url"}, "select_tab": {"tab_id"}, "close_tab": {"tab_id"},
    "navigate": {"url", "tab_id"}, "back": {"tab_id"}, "forward": {"tab_id"}, "reload": {"tab_id"},
    "wait_for": {"tab_id", "target", "state", "timeout_ms"},
    "snapshot": {"tab_id"}, "screenshot": {"tab_id", "full_page"}, "extract_text": {"tab_id"},
    "get_links": {"tab_id"}, "get_forms": {"tab_id"}, "get_console": {"tab_id"},
    "get_page_errors": {"tab_id"}, "get_network_errors": {"tab_id"},
    "click": {"tab_id", "target", "revision"}, "fill": {"tab_id", "target", "revision", "value"},
    "type": {"tab_id", "target", "revision", "value"}, "press": {"tab_id", "target", "revision", "key"},
    "select": {"tab_id", "target", "revision", "values"}, "check": {"tab_id", "target", "revision", "checked"},
    "hover": {"tab_id", "target", "revision"}, "scroll": {"tab_id", "revision", "x", "y"},
    "request_handoff": set(), "resume_after_handoff": set(), "upload": set(), "download": set(),
}
_TARGET_KEYS = {"ref", "role", "name", "label", "test_id", "text", "frame"}
_SENSITIVE = re.compile(r"password|passwd|one.?time|otp|mfa|verification.?code|security.?code|credit.?card|cc-", re.I)

# Fixed host-authored inspection only; JavaScript is never accepted from a tool.
_TARGET_STATE = """el => {
 const sensitive = e => /password|passwd|one.?time|otp|mfa|verification.?code|security.?code|credit.?card|cc-/i.test(
  [e.type,e.name,e.id,e.autocomplete,e.getAttribute('aria-label'),...Array.from(e.labels||[]).map(l=>l.textContent)].join(' '));
 const allFields = Array.from(el.closest('form')?.elements || []);
 const fields = allFields.slice(0, 100).map(e => ({
  name: String(e.name || e.id || e.getAttribute('aria-label') || '').slice(0,1000), type: e.type || e.tagName,
  value: sensitive(e) ? '[REDACTED]' : String(e.value||'').slice(0,10000), sensitive: sensitive(e),
  valueLength: String(e.value||'').length,
  selected: Array.from(e.selectedOptions||[]).slice(0,100).map(o=>String(o.value).slice(0,1000)),
  selectedCount: e.selectedOptions?.length || 0, checked: !!e.checked }));
 return {tag: el.tagName, html: el.outerHTML.slice(0,16000), type: el.type || '',
  name: String(el.name || '').slice(0,1000), autocomplete: el.autocomplete || '', label: String(el.getAttribute('aria-label') || '').slice(0,1000),
  sensitive: sensitive(el), value: sensitive(el) ? '[REDACTED]' : String(el.value||'').slice(0,10000),
  valueLength: String(el.value||'').length, formFieldCount: allFields.length,
  selected: Array.from(el.selectedOptions||[]).slice(0,100).map(o=>String(o.value).slice(0,1000)),
  selectedCount: el.selectedOptions?.length || 0,
  form: fields, formAction: el.closest('form')?.action || '', href: el.href || ''};
}"""
_FORM_OBSERVATION = """forms => forms.slice(0,50).map(f => ({
 method: f.method, action: f.action.slice(0,8192), fieldCount: f.elements.length,
 fields: Array.from(f.elements).slice(0,100).map(e=>({tag:e.tagName,type:e.type||'',
 name:(e.name||'').slice(0,256),label:(e.getAttribute('aria-label')||'').slice(0,512),required:!!e.required,disabled:!!e.disabled}))
}))"""


@dataclass
class _Prepared:
    identity: BrowserIdentity
    operation: str
    args_digest: str
    state_digest: str
    tab_id: str
    expires: float


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class BrowserManager:
    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self.policy = BrowserSecurityPolicy()
        self.artifacts = BrowserArtifacts()
        self._sessions: dict[BrowserIdentity, BrowserSession] = {}
        self._browsers: dict[bool, Any] = {}
        self._playwright = None
        self._startup_lock = asyncio.Lock()
        self._prepared: dict[str, _Prepared] = {}
        self._grant_providers: dict[BrowserIdentity, Any] = {}
        self._redirect_tasks: dict[str, asyncio.Task] = {}
        self._redirect_counts: dict[str, int] = {}
        # A routed policy refusal otherwise becomes Playwright's generic
        # ERR_FAILED. Keep the host's reason for the affected main-frame load.
        self._navigation_errors: dict[str, BrowserError] = {}
        self.metrics = {"calls": 0, "startup_seconds": 0.0, "action_seconds": 0.0, "crashes": 0}

    def bind_grant_provider(self, identity: BrowserIdentity, provider) -> None:
        """Bind trusted synchronous host policy for autonomous page requests."""
        if not callable(provider):
            raise TypeError("Browser grant provider must be callable.")
        self._grant_providers[identity] = provider

    def _live_grant(self, identity: BrowserIdentity, fallback: BrowserGrant) -> BrowserGrant:
        provider = self._grant_providers.get(identity)
        if provider is None:
            return fallback
        try:
            grant = provider()
            return grant if isinstance(grant, BrowserGrant) else BrowserGrant()
        except Exception:
            return BrowserGrant()

    def _enabled(self, grant: BrowserGrant) -> None:
        if not self.config.enabled or not grant.enabled:
            raise BrowserError("permission_denied", "Browser is disabled for this host or task.")
        if self.config.strict_network_isolation:
            raise BrowserError("network_isolation_unavailable", "Strict network isolation requires an enforceable egress boundary; host Chromium is unavailable in this mode.")

    def _arguments(self, operation: str, arguments: dict | None) -> tuple[str, dict]:
        operation = operation.removeprefix("browser_")
        if operation not in _FIELDS:
            raise BrowserError("unsupported_capability", "Unknown browser operation.")
        args = arguments if arguments is not None else {}
        if not isinstance(args, dict) or set(args) - _FIELDS[operation]:
            raise BrowserError("invalid_tool_arguments", "Browser arguments contain unsupported fields.")
        if "tab_id" in args and (not isinstance(args["tab_id"], str) or len(args["tab_id"]) > 100):
            raise BrowserError("invalid_tool_arguments", "Invalid tab identifier.")
        if operation in {"navigate"} and not isinstance(args.get("url"), str):
            raise BrowserError("invalid_tool_arguments", "A URL is required.")
        if operation == "new_tab" and "url" in args and not isinstance(args["url"], str):
            raise BrowserError("invalid_tool_arguments", "Invalid URL.")
        if operation in INTERACTIONS:
            if type(args.get("revision")) is not int or args["revision"] < 1:
                raise BrowserError("invalid_tool_arguments", "Interactions require a current snapshot revision.")
            if operation != "scroll" and not isinstance(args.get("target"), dict):
                raise BrowserError("invalid_tool_arguments", "An explicit semantic target is required.")
        if "target" in args:
            target = args["target"]
            if not isinstance(target, dict) or set(target) - _TARGET_KEYS or not target:
                raise BrowserError("invalid_tool_arguments", "Invalid semantic target.")
            if any(not isinstance(v, str) or not v or len(v) > 1000 for v in target.values()):
                raise BrowserError("invalid_tool_arguments", "Target fields must be bounded text.")
            selectors = {k for k in target if k in {"ref", "role", "label", "test_id", "text"}}
            if len(selectors) != 1 or ("name" in target and "role" not in target):
                raise BrowserError("invalid_tool_arguments", "Specify exactly one semantic locator strategy.")
        if operation in {"fill", "type"} and (not isinstance(args.get("value"), str) or len(args["value"]) > 10000):
            raise BrowserError("invalid_tool_arguments", "Input value must be text of at most 10000 characters.")
        if operation == "press" and (not isinstance(args.get("key"), str) or len(args["key"]) > 80):
            raise BrowserError("invalid_tool_arguments", "Invalid key.")
        if operation == "select" and (not isinstance(args.get("values"), list) or not 1 <= len(args["values"]) <= 50
                                      or any(not isinstance(v, str) or len(v) > 1000 for v in args["values"])):
            raise BrowserError("invalid_tool_arguments", "Select requires a bounded list of values.")
        for field in ("headless", "full_page", "checked"):
            if field in args and type(args[field]) is not bool:
                raise BrowserError("invalid_tool_arguments", f"{field} must be boolean.")
        for field, minimum, maximum in (("width", 320, 2560), ("height", 240, 1440), ("x", -2000, 2000), ("y", -2000, 2000), ("timeout_ms", 100, self.config.action_timeout_ms)):
            if field in args and (type(args[field]) is not int or not minimum <= args[field] <= maximum):
                raise BrowserError("invalid_tool_arguments", f"{field} is outside the supported bounds.")
        if "state" in args and args["state"] not in {"visible", "hidden", "attached", "detached"}:
            raise BrowserError("invalid_tool_arguments", "Unsupported wait state.")
        return operation, dict(args)

    def _session(self, identity: BrowserIdentity, grant: BrowserGrant) -> BrowserSession:
        grant = self._live_grant(identity, grant)
        self._enabled(grant)
        session = self._sessions.get(identity)
        if session is None:
            raise BrowserError("session_missing", "Start an owned browser session first.")
        if session.identity != identity:
            raise BrowserError("ownership_denied", "Browser ownership does not match.")
        session.grant = grant  # live host policy is rechecked before every dispatch
        if session.cancelled or session.state in {"closed", "closing", "failed"}:
            raise BrowserError("session_unavailable", "The browser session is closed, cancelled or failed; state was not recreated.")
        return session

    def _tab(self, session: BrowserSession, args: dict) -> BrowserTab:
        tab_id = args.get("tab_id") or session.active_tab_id
        tab = session.tabs.get(tab_id)
        if tab is None or tab.page.is_closed():
            raise BrowserError("ownership_denied", "The tab is closed or does not belong to this execution identity.")
        return tab

    async def _browser(self, headless: bool):
        browser = self._browsers.get(headless)
        if browser is not None:
            if not browser.is_connected():
                # An explicit new start may recover the engine after the old
                # owned context was closed. Never pretend to recover its page.
                self._browsers.pop(headless, None)
            else:
                return browser
        started = time.monotonic()
        try:
            if self._playwright is None:
                from firik_agent.browser.config import configure_frozen_browser_cache
                configure_frozen_browser_cache()
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
            browser = await self._playwright.chromium.launch(headless=headless, chromium_sandbox=True,
                                                             timeout=self.config.launch_timeout_ms)
            self._browsers[headless] = browser
            browser.on("disconnected", lambda: self._disconnected(headless))
            self.metrics["startup_seconds"] += time.monotonic() - started
            return browser
        except ImportError:
            raise BrowserError("browser_not_installed", "Install Firik's browser extra and explicitly install Chromium with firik-agent browser install.") from None
        except BaseException as exc:
            # A cancelled launch can have spawned a driver/browser before it
            # returns its handle. Closing the driver owns that partial process
            # tree; incomplete launches must never escape service ownership.
            if self._playwright is not None:
                try:
                    await asyncio.shield(self._playwright.stop())
                finally:
                    self._playwright = None
                    self._browsers.clear()
                    for active in self._sessions.values():
                        active.state = "failed"
                        active.cancelled = True
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise BrowserError("browser_launch_failed", "Chromium could not start. Run firik-agent browser doctor; install its matching browser binary if missing.") from None

    def _disconnected(self, headless: bool):
        affected = False
        for session in self._sessions.values():
            if session.headless == headless and session.state not in {"closing", "closed"}:
                affected = True
                session.state = "failed"
                session.cancelled = True
        if affected:
            self.metrics["crashes"] += 1

    async def _start(self, identity: BrowserIdentity, grant: BrowserGrant, args: dict) -> dict:
        grant = self._live_grant(identity, grant)
        self._enabled(grant)
        async with self._startup_lock:
            if identity in self._sessions:
                previous = self._sessions[identity]
                if previous.cancelled or previous.state in {"closed", "failed"}:
                    provider = self._grant_providers.get(identity)
                    await self.close_owner(identity)
                    if provider is not None:
                        self._grant_providers[identity] = provider
                else:
                    session = self._session(identity, grant)
                    return self._status(session)
            if len(self._sessions) >= self.config.max_sessions:
                raise BrowserError("browser_limit", "Maximum number of concurrent browser sessions reached.")
            headless = args.get("headless", self.config.headless)
            browser = await self._browser(headless)
            context = None
            try:
                context = await browser.new_context(
                    viewport={"width": args.get("width", self.config.viewport_width), "height": args.get("height", self.config.viewport_height)},
                    locale=self.config.locale, timezone_id=self.config.timezone_id,
                    accept_downloads=False, service_workers="block", bypass_csp=False,
                )
                context.set_default_timeout(self.config.action_timeout_ms)
                context.set_default_navigation_timeout(self.config.navigation_timeout_ms)
                session = BrowserSession(identity, context, grant, headless=headless)
                await context.route("**/*", lambda route: self._route(session, route))
                # WebSockets cannot bypass HTTP routing. This core blocks all of them.
                if not hasattr(context, "route_web_socket"):
                    raise BrowserError("unsupported_capability", "Installed Playwright cannot enforce WebSocket restrictions.")
                await context.route_web_socket("**/*", lambda ws: ws.close(code=1008, reason="Browser WebSockets disabled by host policy"))
                context.on("page", lambda page: self._track_page(session, page))
                page = await context.new_page()
                if not session.tabs:
                    self._track_page(session, page)
                session.state = "ready"
                self._sessions[identity] = session
                return self._status(session)
            except BaseException:
                if context is not None:
                    await context.close()
                raise

    async def _route(self, session: BrowserSession, route):
        response = None
        try:
            if session.cancelled or session.state in {"closing", "closed", "failed"}:
                raise BrowserError("cancelled", "Browser no longer active.")
            session.grant = self._live_grant(session.identity, session.grant)
            self._enabled(session.grant)
            checked_grant = session.grant
            await self.policy.validate(route.request.url, checked_grant)
            current_grant = self._live_grant(session.identity, session.grant)
            self._enabled(current_grant)
            if current_grant != checked_grant:
                raise BrowserError("permission_changed", "Browser authority changed during destination validation.")
            session.grant = current_grant
            # Playwright routes only the first URL in an HTTP redirect chain.
            # Disable automatic following and validate every destination here.
            response = await route.fetch(max_redirects=0, timeout=self.config.navigation_timeout_ms)
            current_grant = self._live_grant(session.identity, session.grant)
            self._enabled(current_grant)
            if current_grant != checked_grant:
                raise BrowserError("permission_changed", "Browser authority changed during the request; its response was discarded.")
            redirects = 0
            destination = route.request.url
            while response.status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                destination = urljoin(destination, response.headers["location"])
                await self.policy.validate(destination, checked_grant)
                current_grant = self._live_grant(session.identity, session.grant)
                self._enabled(current_grant)
                if current_grant != checked_grant:
                    raise BrowserError("permission_changed", "Browser authority changed during redirect validation.")
                safe_get_transition = route.request.method == "POST" and response.status in {302, 303}
                if route.request.method not in {"GET", "HEAD"} and not safe_get_transition:
                    raise BrowserError("redirect_blocked", "A submitted request is not replayed automatically after a redirect.")
                redirects += 1
                if redirects > 10:
                    raise BrowserError("redirect_limit", "The website redirected too many times.")
                page = route.request.frame.page
                tab = next((item for item in session.tabs.values() if item.page is page), None)
                if route.request.is_navigation_request() and route.request.frame is page.main_frame and tab is not None:
                    # A new GET navigation keeps Chromium's real URL/origin
                    # and relative links correct. POST 302/303 follow browser
                    # semantics with a GET; no submitted body is replayed.
                    count = self._redirect_counts.get(tab.tab_id, 0) + 1
                    self._redirect_counts[tab.tab_id] = count
                    if count > 10:
                        raise BrowserError("redirect_limit", "The website redirected too many times.")
                    ready = asyncio.Event()
                    task = asyncio.create_task(self._follow_redirect(session, tab, destination, ready))
                    self._redirect_tasks[tab.tab_id] = task
                    session.pending.add(task)
                    task.add_done_callback(session.pending.discard)
                    # Retrieve an exception even when a passive script initiated
                    # navigation and no foreground command is waiting for it.
                    task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
                    try:
                        await route.abort("aborted")
                    finally:
                        ready.set()
                    return
                if safe_get_transition:
                    raise BrowserError("redirect_blocked", "A submitted resource redirect requires native browser origin handling.")
                if origin_of(destination) != origin_of(route.request.url):
                    # Fulfilling a cross-origin response under the original URL
                    # would bypass Chromium's CORS/origin checks and could also
                    # forward the source request's Authorization header. Only
                    # a real top-level navigation can cross origins safely.
                    raise BrowserError("cross_origin_redirect_blocked", "A redirected resource changed origin and could not be loaded safely.")
                await response.dispose()
                response = await route.fetch(url=destination, max_redirects=0,
                                             timeout=self.config.navigation_timeout_ms)
            current_grant = self._live_grant(session.identity, session.grant)
            self._enabled(current_grant)
            if current_grant != checked_grant:
                raise BrowserError("permission_changed", "Browser authority changed while loading the response.")
            await route.fulfill(response=response)
        except BrowserError as exc:
            for tab in session.tabs.values():
                tab.network_errors.append({"url": sanitized_url(route.request.url), "method": route.request.method,
                                           "resource_type": route.request.resource_type, "failure": exc.code})
            request = route.request
            page = request.frame.page
            main_navigation = request.is_navigation_request() and request.frame is page.main_frame
            if main_navigation:
                tab = next((item for item in session.tabs.values() if item.page is page), None)
                if tab is not None:
                    # A POST may already have reached its server before a
                    # redirect was denied. Never downgrade that uncertainty.
                    submitted = response is not None and request.method not in {"GET", "HEAD"}
                    self._navigation_errors[tab.tab_id] = BrowserError(
                        exc.code, str(exc), dispatched=exc.dispatched or submitted,
                        requires_reconciliation=exc.requires_reconciliation or submitted)
            # ERR_BLOCKED_BY_CLIENT schedules an internal chrome-error page
            # after goto() has already failed. That late commit can interrupt
            # the next independent site. Abort the main load as a cancellation
            # instead, preserving the previous document and the exact refusal.
            await route.abort("aborted" if main_navigation else "blockedbyclient")
        except Exception:
            try:
                await route.abort("blockedbyclient")
            except Exception:
                pass
        finally:
            if response is not None:
                try:
                    await response.dispose()
                except Exception:
                    pass

    async def _follow_redirect(self, session, tab, destination, ready):
        await ready.wait()
        grant = self._live_grant(session.identity, session.grant)
        self._enabled(grant)
        await self.policy.validate(destination, grant)
        try:
            return await tab.page.goto(destination, wait_until="domcontentloaded")
        except Exception:
            following = self._redirect_tasks.get(tab.tab_id)
            if following is not None and following is not asyncio.current_task():
                return await following
            raise

    def _track_page(self, session: BrowserSession, page):
        if any(tab.page is page for tab in session.tabs.values()):
            return
        if len(session.tabs) >= self.config.max_tabs_per_session:
            asyncio.create_task(page.close())
            return
        tab = BrowserTab(page)
        for field in ("console", "page_errors", "network_errors"):
            setattr(tab, field, DiagnosticBuffer(maxlen=self.config.max_diagnostics))
        session.tabs[tab.tab_id] = tab
        if not session.active_tab_id:
            session.active_tab_id = tab.tab_id
        page.on("console", lambda msg: tab.console.append({"type": msg.type, "text": redact(msg.text), "untrusted": True})
                if msg.type in {"error", "warning"} and session.state != "manual_control" else None)
        page.on("pageerror", lambda error: tab.page_errors.append({"text": redact(str(error)), "untrusted": True})
                if session.state != "manual_control" else None)
        page.on("requestfailed", lambda req: tab.network_errors.append({"method": req.method, "url": sanitized_url(req.url),
                "resource_type": req.resource_type, "failure": redact(req.failure or "failed")}) if session.state != "manual_control" else None)
        page.on("response", lambda resp: tab.network_errors.append({"url": sanitized_url(resp.url), "status": resp.status})
                if resp.status >= 400 and session.state != "manual_control" else None)
        page.on("framenavigated", lambda frame: self._invalidate(tab))
        page.on("crash", lambda: self._page_crash(session, tab))
        page.on("close", lambda: self._page_closed(session, tab))
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss())
                if session.state != "manual_control" else None)
        page.on("download", lambda download: asyncio.create_task(download.cancel()))

    def _page_crash(self, session, tab):
        self._invalidate(tab)
        session.state = "failed"
        self.metrics["crashes"] += 1

    def _page_closed(self, session, tab):
        task = self._redirect_tasks.pop(tab.tab_id, None)
        if task is not None and not task.done():
            task.cancel()
        self._redirect_counts.pop(tab.tab_id, None)
        self._navigation_errors.pop(tab.tab_id, None)
        session.tabs.pop(tab.tab_id, None)
        if session.active_tab_id == tab.tab_id:
            session.active_tab_id = next(iter(session.tabs), "")
        self._prepared = {key: value for key, value in self._prepared.items()
                          if value.tab_id != tab.tab_id or value.identity != session.identity}

    def _invalidate(self, tab: BrowserTab):
        tab.revision += 1
        tab.observed_digest = ""

    def _status(self, session: BrowserSession, *, host: bool = False) -> dict:
        result = {"session_id": session.session_id, "context_id": session.context_id, "state": session.state,
                "conversation_id": session.identity.conversation_id, "project_id": session.identity.project_id,
                "run_id": session.identity.run_id, "actor_id": session.identity.actor_id,
                "active_tab_id": session.active_tab_id, "tab_count": len(session.tabs),
                "headless": session.headless, "created_at": session.created_at, "last_activity": session.last_activity,
                "browser_version": self._browsers[session.headless].version if session.headless in self._browsers else "",
                "actions": session.actions, "max_actions": self.config.max_actions_per_task,
                "network_boundary": "host_request_policy; not OS isolation", "websockets": "blocked", "service_workers": "blocked"}
        if host or session.state != "manual_control":
            result["tabs"] = [{"tab_id": tab.tab_id, "url": sanitized_url(tab.page.url),
                               "title": getattr(tab, "title", ""), "active": tab.tab_id == session.active_tab_id}
                              for tab in session.tabs.values() if not tab.page.is_closed()]
        return result

    def _bounded_items(self, items: list, key: str) -> dict:
        selected = []
        used = 0
        for item in items:
            size = len(json.dumps(item, ensure_ascii=False)) + 2
            if used + size > self.config.max_snapshot_chars:
                break
            selected.append(item)
            used += size
        omitted = len(items) - len(selected)
        return {key: selected, "truncated": omitted > 0, "omitted_entries": omitted, "untrusted": True}

    def sessions(self, identity: BrowserIdentity) -> list[dict]:
        session = self._sessions.get(identity)
        return [self._status(session)] if session else []

    async def _digest(self, tab: BrowserTab, frame=None) -> str:
        frames = [frame] if frame is not None else list(tab.page.frames)
        if len(frames) > 32:
            raise BrowserError("frame_limit", "The document exceeds the supported observation frame limit.")
        contents = [{"url": current.url, "content": await current.content()} for current in frames]
        return _hash(contents)

    async def _locator(self, session: BrowserSession, tab: BrowserTab, target: dict, *, require_ready: bool = True):
        frame = tab.page.main_frame
        if target.get("frame"):
            candidates = [f for f in tab.page.frames if f.url == target["frame"]]
            if len(candidates) != 1:
                raise BrowserError("ambiguous_target", "Frame URL must uniquely identify an owned frame.")
            frame = candidates[0]
        if frame.url != "about:blank":
            await self.policy.validate(frame.url, session.grant)
        if "ref" in target:
            if frame is not tab.page.main_frame or not hasattr(tab.page, "get_by_ref"):
                raise BrowserError("unsupported_capability", "Native snapshot references require Playwright 1.64 or newer.")
            locator = tab.page.get_by_ref(target["ref"])
        elif "role" in target:
            options = {"name": target["name"], "exact": True} if "name" in target else {}
            locator = frame.get_by_role(target["role"], **options)
        elif "label" in target:
            locator = frame.get_by_label(target["label"], exact=True)
        elif "test_id" in target:
            locator = frame.get_by_test_id(target["test_id"])
        else:
            locator = frame.get_by_text(target["text"], exact=True)
        if not require_ready:
            return locator
        count = await locator.count()
        if count != 1:
            raise BrowserError("ambiguous_target" if count else "target_missing", f"Semantic target matched {count} elements; refresh the snapshot.")
        if not await locator.is_visible() or not await locator.is_enabled():
            raise BrowserError("target_unavailable", "The target must be visible and enabled.")
        return locator

    async def _validate_revision(self, tab: BrowserTab, revision: int):
        if revision != tab.revision or not tab.observed_digest or await self._digest(tab) != tab.observed_digest:
            self._invalidate(tab)
            raise BrowserError("stale_observation", "The page changed. Capture a fresh snapshot before interacting.")

    async def _action_state(self, session: BrowserSession, tab: BrowserTab, operation: str, args: dict) -> tuple[str, dict]:
        await self._validate_revision(tab, args["revision"])
        if operation == "scroll":
            state = {"url": tab.page.url, "revision": tab.revision, "dom": await self._digest(tab)}
            preview = {"target": "page viewport", "form": []}
        else:
            locator = await self._locator(session, tab, args["target"])
            state = await locator.evaluate(_TARGET_STATE)
            if (state.get("formFieldCount", 0) > 100 or state.get("valueLength", 0) > 10000
                    or state.get("selectedCount", 0) > 100
                    or any(field.get("valueLength", 0) > 10000 or field.get("selectedCount", 0) > 100 for field in state["form"])
                    or sum(len(field["name"]) + len(field["value"]) + sum(map(len, field.get("selected", []))) for field in state["form"]) > self.config.max_snapshot_chars):
                raise BrowserError("form_limit", "The complete form exceeds review limits; use manual browser control.")
            sensitive = " ".join(str(state.get(k, "")) for k in ("type", "name", "autocomplete", "label"))
            if state.get("sensitive") or _SENSITIVE.search(sensitive) or any(field.get("sensitive") for field in state["form"]):
                raise BrowserError("authentication_handoff_required", "Credentials, MFA and payment details require manual browser control.")
            if state.get("href"):
                await self.policy.validate(state["href"], session.grant)
            if state.get("formAction"):
                await self.policy.validate(state["formAction"], session.grant)
            state["dom"] = await self._digest(tab)
            preview = {"target": args["target"], "form": [{"name": redact(f["name"]), "type": f["type"],
                       "value": redact(f["value"], 10000), "checked": f["checked"],
                       "selected": [redact(value) for value in f.get("selected", [])]} for f in state["form"]]}
        return _hash(state), preview

    async def prepare(self, identity: BrowserIdentity, grant: BrowserGrant, operation: str, arguments: dict) -> dict:
        operation, args = self._arguments(operation, arguments)
        if operation not in INTERACTIONS:
            raise BrowserError("unsupported_capability", "Only interactive operations require bound action approval.")
        session = self._session(identity, grant)
        if session.state == "manual_control":
            raise BrowserError("manual_control", "The user currently controls this browser.")
        tab = self._tab(session, args)
        async with session.control_lock, tab.lock:
            digest, preview = await self._action_state(session, tab, operation, args)
            token = secrets.token_urlsafe(32)
            now = time.monotonic()
            self._prepared = {k: v for k, v in self._prepared.items() if v.expires > now}
            if len(self._prepared) >= 128:
                raise BrowserError("browser_limit", "Too many pending browser approvals.")
            self._prepared[token] = _Prepared(identity, operation, _hash(args), digest, tab.tab_id, now + self.config.approval_ttl_seconds)
            return {"fingerprint": token, "action": operation, "origin": origin_of(tab.page.url),
                    "session_id": session.session_id, "tab_id": tab.tab_id, "revision": tab.revision,
                    "expires_in_seconds": self.config.approval_ttl_seconds,
                    "value": redact(args.get("value", args.get("key", str(args.get("values", ""))))),
                    "consequence": "This interaction may change or submit information on the website.", **preview}

    async def dispatch(self, identity: BrowserIdentity, grant: BrowserGrant, operation: str,
                       arguments: dict | None = None, *, approved_fingerprint: str | None = None) -> dict:
        session = self._sessions.get(identity)
        # One command at a time per context also makes tab creation, active-tab
        # changes and handoff atomic. Independent contexts remain concurrent.
        if session is not None and operation.removeprefix("browser_") != "close":
            async with session.control_lock:
                return await self._dispatch(identity, grant, operation, arguments,
                                            approved_fingerprint=approved_fingerprint)
        return await self._dispatch(identity, grant, operation, arguments,
                                    approved_fingerprint=approved_fingerprint)

    async def host_command(self, identity: BrowserIdentity, grant: BrowserGrant,
                           operation: str, arguments: dict | None = None) -> dict:
        """Trusted UI navigation. This entry point is never a model tool."""
        operation = operation.removeprefix("browser_")
        if operation not in NAVIGATIONS | {"start", "status", "list_tabs", "new_tab", "select_tab", "close_tab", "close", "request_handoff"}:
            raise BrowserError("unsupported_capability", "Unsupported host browser control.")
        session = self._sessions.get(identity)
        if session is not None and operation != "close":
            async with session.control_lock:
                return await self._dispatch(identity, grant, operation, arguments, host_control=True)
        return await self._dispatch(identity, grant, operation, arguments, host_control=True)

    async def _dispatch(self, identity: BrowserIdentity, grant: BrowserGrant, operation: str,
                        arguments: dict | None = None, *, approved_fingerprint: str | None = None,
                        host_control: bool = False) -> dict:
        operation, args = self._arguments(operation, arguments)
        grant = self._live_grant(identity, grant)
        self._enabled(grant)
        if operation in {"upload", "download"}:
            raise BrowserError("unsupported_capability", "Browser file transfers are disabled until host file grants and controlled download review are implemented.")
        if operation == "resume_after_handoff":
            raise BrowserError("host_action_required", "Only the host UI or terminal can resume a manual handoff.")
        if operation == "start":
            return await self._start(identity, grant, args)
        if operation == "status" and identity not in self._sessions:
            return {"state": "uninitialized", "tab_count": 0}
        if operation == "close":
            await self.close_owner(identity)
            return {"state": "closed"}
        session = self._session(identity, grant)
        if operation == "status":
            return self._status(session, host=host_control)
        if session.state == "manual_control" and not host_control:
            raise BrowserError("manual_control", "Agent observation and interaction are paused during manual control.")
        if operation == "request_handoff":
            session.state = "manual_control"
            self._prepared = {k: v for k, v in self._prepared.items() if v.identity != identity}
            for tab in session.tabs.values():
                async with tab.lock:
                    self._invalidate(tab)
                    tab.console.clear(); tab.page_errors.clear(); tab.network_errors.clear()
            if not session.headless:
                await self._tab(session, {}).page.bring_to_front()
            return self._status(session, host=host_control)
        if operation == "list_tabs":
            return {"tabs": [{"tab_id": tab.tab_id, "url": sanitized_url(tab.page.url), "title": redact(await tab.page.title()), "active": tab.tab_id == session.active_tab_id}
                             for tab in session.tabs.values() if not tab.page.is_closed()]}
        if operation == "new_tab":
            if len(session.tabs) >= self.config.max_tabs_per_session:
                raise BrowserError("browser_limit", "Maximum tabs per session reached.")
            if args.get("url"):
                await self.policy.validate(args["url"], grant)
            page = await session.context.new_page()
            tab = next(t for t in session.tabs.values() if t.page is page)
            session.active_tab_id = tab.tab_id
            if args.get("url"):
                return await self._dispatch(identity, grant, "navigate", {"url": args["url"], "tab_id": tab.tab_id}, host_control=host_control)
            return {"tab_id": tab.tab_id, "url": "about:blank"}
        tab = self._tab(session, args)
        if operation == "select_tab":
            session.active_tab_id = tab.tab_id
            return {"tab_id": tab.tab_id, "url": sanitized_url(tab.page.url)}
        if operation == "close_tab":
            async with tab.lock:
                await tab.page.close()
                session.tabs.pop(tab.tab_id, None)
                if session.active_tab_id == tab.tab_id:
                    session.active_tab_id = next(iter(session.tabs), "")
                return {"closed_tab_id": tab.tab_id}
        task = asyncio.current_task()
        session.pending.add(task)
        dispatched = False
        started = time.monotonic()
        self.metrics["calls"] += 1
        previous_state = session.state
        try:
            async with tab.lock:
                session = self._session(identity, grant)
                if session.state == "manual_control" and not host_control:
                    raise BrowserError("manual_control", "The user currently controls this browser.")
                if not host_control and session.actions >= self.config.max_actions_per_task:
                    raise BrowserError("browser_limit", "Browser action budget exhausted.")
                # A failed load can leave Chromium on an internal error page
                # or a now-denied origin. A new destination must still be
                # reachable; _execute validates it before any request.
                if operation != "navigate" and tab.page.url != "about:blank":
                    await self.policy.validate(tab.page.url, grant)
                if operation in INTERACTIONS:
                    prepared = self._prepared.pop(approved_fingerprint, None) if approved_fingerprint else None
                    if prepared is None or prepared.identity != identity or prepared.operation != operation or prepared.args_digest != _hash(args) or prepared.tab_id != tab.tab_id:
                        raise BrowserError("approval_required", "This exact browser action requires fresh host approval.")
                    if prepared.expires <= time.monotonic():
                        raise BrowserError("approval_expired", "Browser approval expired before execution.")
                    digest, _ = await self._action_state(session, tab, operation, args)
                    if digest != prepared.state_digest:
                        raise BrowserError("approval_invalidated", "The reviewed target or form changed; obtain fresh approval.")
                if not host_control:
                    session.actions += 1
                session.last_activity = time.time()
                session.state = "busy"
                # Mark before awaited browser calls: remote outcome can be uncertain.
                dispatched = operation in INTERACTIONS | NAVIGATIONS
                result = await asyncio.wait_for(self._execute(session, tab, operation, args),
                                               timeout=(self.config.navigation_timeout_ms if operation in NAVIGATIONS else self.config.action_timeout_ms) / 1000 + 1)
                result.update(session_id=session.session_id, tab_id=tab.tab_id, revision=tab.revision,
                              duration_seconds=round(time.monotonic() - started, 4))
                return result
        except BrowserError:
            raise
        except asyncio.CancelledError:
            session.cancelled = True
            session.state = "closing"
            await self._close_context(session)
            raise BrowserError("tool_outcome_unknown" if dispatched else "cancelled", "Browser action cancelled; inspect remote state before repeating." if dispatched else "Browser action cancelled.",
                               dispatched=dispatched, requires_reconciliation=dispatched) from None
        except Exception:
            raise BrowserError("tool_outcome_unknown" if dispatched else "browser_operation_failed",
                               "Browser action outcome is unknown; inspect the page before repeating." if dispatched else "Browser observation failed or timed out.",
                               dispatched=dispatched, requires_reconciliation=dispatched) from None
        finally:
            session.pending.discard(task)
            if session.state == "busy":
                session.state = "manual_control" if previous_state == "manual_control" else "ready"
            self.metrics["action_seconds"] += time.monotonic() - started

    async def _execute(self, session: BrowserSession, tab: BrowserTab, operation: str, args: dict) -> dict:
        page = tab.page
        if operation in NAVIGATIONS:
            requested = args.get("url", page.url)
            self._redirect_counts[tab.tab_id] = 0
            self._navigation_errors.pop(tab.tab_id, None)
            previous_redirect = self._redirect_tasks.pop(tab.tab_id, None)
            if previous_redirect is not None and not previous_redirect.done():
                await previous_redirect
            try:
                try:
                    if operation == "navigate":
                        await self.policy.validate(requested, session.grant)
                        response = await page.goto(requested, wait_until="domcontentloaded")
                    elif operation == "back":
                        response = await page.go_back(wait_until="domcontentloaded")
                    elif operation == "forward":
                        response = await page.go_forward(wait_until="domcontentloaded")
                    else:
                        response = await page.reload(wait_until="domcontentloaded")
                except Exception:
                    following = self._redirect_tasks.get(tab.tab_id)
                    if following is None:
                        raise
                    response = await following
            except Exception:
                self._invalidate(tab)
                blocked = self._navigation_errors.pop(tab.tab_id, None)
                if blocked is not None:
                    raise blocked from None
                raise
            finally:
                self._navigation_errors.pop(tab.tab_id, None)
            self._invalidate(tab)
            if page.url != "about:blank":
                await self.policy.validate(page.url, session.grant)
            tab.title = redact(await page.title())
            return {"requested_url": sanitized_url(requested), "url": sanitized_url(page.url), "title": tab.title,
                    "http_status": response.status if response else None, "redirected": bool(self._redirect_counts.get(tab.tab_id) or (response and response.request.redirected_from)),
                    "outcome": "navigation_observed", "task_completion_verified": False}
        if operation == "snapshot":
            before = await self._digest(tab)
            if hasattr(page, "aria_snapshot"):
                text = await page.aria_snapshot(mode="ai")
            else:
                text = await page.locator("body").aria_snapshot()
            # Textbox values are private; preserve names and refs, omit values.
            text = re.sub(r'(^\s*- (?:textbox|searchbox|combobox)[^\n]*?)(?:: .*)$', r'\1: [value omitted]', text, flags=re.M)
            text = redact(text, self.config.max_snapshot_chars + 1)
            after = await self._digest(tab)
            if before != after:
                self._invalidate(tab)
                raise BrowserError("stale_observation", "The page changed while its snapshot was captured; take a fresh observation.")
            tab.revision += 1
            tab.observed_digest = after
            return {"url": sanitized_url(page.url), "type": "aria", "captured_at": time.time(),
                    "content": text[:self.config.max_snapshot_chars], "truncated": len(text) > self.config.max_snapshot_chars,
                    "untrusted": True, "frames": [{"url": sanitized_url(f.url)} for f in page.frames if f is not page.main_frame]}
        if operation == "extract_text":
            text = redact(await page.locator("body").inner_text(), self.config.max_snapshot_chars + 1)
            return {"content": text[:self.config.max_snapshot_chars], "truncated": len(text) > self.config.max_snapshot_chars, "untrusted": True}
        if operation == "get_links":
            links_locator = page.get_by_role("link")
            total = await links_locator.count()
            links = await links_locator.evaluate_all("els => els.slice(0,200).map(e=>({text:(e.innerText||e.getAttribute('aria-label')||'').slice(0,300),url:(e.href||'').slice(0,8192)}))")
            result = self._bounded_items([{"text": redact(link["text"]), "url": sanitized_url(link["url"])} for link in links], "links")
            result["truncated"] = result["truncated"] or total > len(links)
            return result
        if operation == "get_forms":
            forms_locator = page.locator("form")
            total = await forms_locator.count()
            forms = await forms_locator.evaluate_all(_FORM_OBSERVATION)
            for form in forms:
                form["action"] = sanitized_url(form["action"])
                for item in form["fields"]:
                    item["name"] = redact(item["name"]); item["label"] = redact(item["label"])
            result = self._bounded_items(forms, "forms")
            result["truncated"] = result["truncated"] or total > len(forms) or any(form["fieldCount"] > len(form["fields"]) for form in forms)
            return result
        if operation in {"get_console", "get_page_errors", "get_network_errors"}:
            buffer = getattr(tab, operation.removeprefix("get_"))
            result = self._bounded_items(list(buffer), "entries")
            result.update(truncated=buffer.dropped > 0 or result["truncated"], dropped_entries=buffer.dropped)
            return result
        if operation == "screenshot":
            data = await self._capture(session, tab, args.get("full_page", False))
            artifact = self.artifacts.save(session.identity, data)
            return {"artifact_id": artifact.artifact_id, "mime_type": "image/png", "size_bytes": artifact.size,
                    "retention": "ephemeral_until_session_close", "model_image": False}
        if operation == "wait_for":
            if args.get("target"):
                locator = await self._locator(session, tab, args["target"], require_ready=False)
                await locator.wait_for(state=args.get("state", "visible"), timeout=args.get("timeout_ms", self.config.action_timeout_ms))
            else:
                await page.wait_for_load_state("domcontentloaded", timeout=args.get("timeout_ms", self.config.action_timeout_ms))
            return {"outcome": "condition_observed"}
        if operation in INTERACTIONS:
            locator = await self._locator(session, tab, args["target"]) if operation != "scroll" else None
            if operation == "click":
                await locator.click()
            elif operation == "fill":
                await locator.fill(args["value"])
            elif operation == "type":
                await locator.press_sequentially(args["value"])
            elif operation == "press":
                await locator.press(args["key"])
            elif operation == "select":
                await locator.select_option(args["values"])
            elif operation == "check":
                await locator.set_checked(args.get("checked", True))
            elif operation == "hover":
                await locator.hover()
            else:
                await page.mouse.wheel(args.get("x", 0), args.get("y", 600))
            self._invalidate(tab)
            return {"outcome": "action_dispatched", "action": operation, "url": sanitized_url(page.url),
                    "task_completion_verified": False, "requires_fresh_snapshot": True}
        raise BrowserError("unsupported_capability", "Browser operation is unavailable.")

    def _capture_permission(self, session: BrowserSession) -> None:
        session.grant = self._live_grant(session.identity, session.grant)
        self._enabled(session.grant)
        if not session.grant.allow_screenshots:
            raise BrowserError("permission_denied", "Screenshot capture requires a separate host privacy grant.")
        if session.state == "manual_control":
            raise BrowserError("manual_control", "Preview is paused during manual authentication.")

    async def _capture(self, session: BrowserSession, tab: BrowserTab, full_page: bool = False) -> bytes:
        self._capture_permission(session)
        if full_page:
            dimensions = await tab.page.evaluate("""() => ({width: Math.max(innerWidth, document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
                height: Math.max(innerHeight, document.documentElement.scrollHeight, document.body?.scrollHeight || 0)})""")
            if dimensions["height"] > 4096 or dimensions["width"] > 2560:
                raise BrowserError("artifact_limit", "Full-page screenshot exceeds the supported 2560 by 4096 CSS pixel bounds.")
        # Mask editable inputs so captured credentials never enter artifacts.
        masks = [frame.locator("input, textarea, [contenteditable]") for frame in tab.page.frames]
        self._capture_permission(session)
        data = await tab.page.screenshot(type="png", full_page=full_page,
                                         mask=masks,
                                         animations="disabled", timeout=self.config.action_timeout_ms)
        self._capture_permission(session)
        if len(data) > self.config.max_screenshot_bytes:
            raise BrowserError("artifact_limit", "Screenshot exceeds the configured artifact size limit.")
        return data

    async def preview(self, identity: BrowserIdentity, grant: BrowserGrant) -> bytes:
        session = self._session(identity, grant)
        tab = self._tab(session, {})
        async with tab.lock:
            return await self._capture(session, tab)

    async def host_preview(self, identity: BrowserIdentity, grant: BrowserGrant) -> bytes:
        """Display the owned page to its user, never persist or send to a model.

        Manual mode still blocks all agent observations. This separate host
        surface is what makes inline manual interaction possible.
        """
        session = self._session(identity, grant)
        tab = self._tab(session, {})
        async with tab.lock:
            session = self._session(identity, grant)
            if not session.grant.allow_screenshots:
                raise BrowserError("permission_denied", "Browser view requires the host preview permission.")
            checked_grant = session.grant
            if tab.page.url != "about:blank":
                await self.policy.validate(tab.page.url, checked_grant)
            current = self._session(identity, grant)
            if current is not session or current.grant != checked_grant:
                raise BrowserError("permission_changed", "Browser preview authority changed during validation.")
            masks = [frame.locator('input[type="password"], input[autocomplete="one-time-code"], '
                                    'input[autocomplete^="cc-"], input[name*="otp" i], input[name*="token" i]')
                     for frame in tab.page.frames]
            data = await tab.page.screenshot(type="png", mask=masks, animations="disabled",
                                             timeout=self.config.action_timeout_ms)
            current = self._session(identity, grant)
            if not current.grant.allow_screenshots:
                raise BrowserError("permission_denied", "Browser preview permission was revoked during capture.")
            if len(data) > self.config.max_screenshot_bytes:
                raise BrowserError("artifact_limit", "Browser view exceeds the configured image limit.")
            return data

    async def host_input(self, identity: BrowserIdentity, grant: BrowserGrant, kind: str,
                         arguments: dict | None = None) -> dict:
        """Bounded direct user input, isolated from the semantic agent tools."""
        fields = {"click": {"x", "y"}, "scroll": {"delta_x", "delta_y"},
                  "key": {"key"}, "text": {"text"}}
        args = arguments if arguments is not None else {}
        if kind not in fields or not isinstance(args, dict) or set(args) - fields[kind]:
            raise BrowserError("invalid_tool_arguments", "Unsupported browser user input.")
        session = self._session(identity, grant)
        async with session.control_lock:
            session = self._session(identity, grant)
            if session.state != "manual_control":
                raise BrowserError("host_action_required", "Take manual control before interacting with the browser view.")
            tab = self._tab(session, {})
            async with tab.lock:
                current = self._session(identity, grant)
                if current is not session or current.state != "manual_control":
                    raise BrowserError("host_action_required", "Manual browser ownership changed while input was waiting.")
                checked_grant = current.grant
                if tab.page.url != "about:blank":
                    await self.policy.validate(tab.page.url, checked_grant)
                current = self._session(identity, grant)
                if current is not session or current.state != "manual_control" or current.grant != checked_grant:
                    raise BrowserError("permission_changed", "Browser input authority changed during validation.")
                self._redirect_counts[tab.tab_id] = 0
                if kind == "click":
                    viewport = tab.page.viewport_size or {"width": self.config.viewport_width, "height": self.config.viewport_height}
                    if any(type(args.get(key)) is not int or not 0 <= args[key] < viewport[bound]
                           for key, bound in (("x", "width"), ("y", "height"))):
                        raise BrowserError("invalid_tool_arguments", "Click is outside the browser view.")
                    await tab.page.mouse.click(args["x"], args["y"])
                elif kind == "scroll":
                    if "delta_y" not in args or any(type(value) is not int or not -2000 <= value <= 2000 for value in args.values()):
                        raise BrowserError("invalid_tool_arguments", "Scroll exceeds the supported bounds.")
                    await tab.page.mouse.wheel(args.get("delta_x", 0), args["delta_y"])
                elif kind == "key":
                    if not isinstance(args.get("key"), str) or not 1 <= len(args["key"]) <= 80:
                        raise BrowserError("invalid_tool_arguments", "Invalid browser key.")
                    await tab.page.keyboard.press(args["key"])
                elif kind == "text":
                    if not isinstance(args.get("text"), str) or len(args["text"]) > 10000:
                        raise BrowserError("invalid_tool_arguments", "Browser input must be bounded text.")
                    await tab.page.keyboard.insert_text(args["text"])
                self._invalidate(tab)
                session.last_activity = time.time()
                return self._status(session, host=True)

    def artifact_path(self, identity: BrowserIdentity, artifact_id: str):
        return self.artifacts.path(identity, artifact_id)

    async def resume(self, identity: BrowserIdentity, grant: BrowserGrant) -> dict:
        session = self._session(identity, grant)
        if session.state != "manual_control":
            raise BrowserError("invalid_state", "The browser is not waiting for manual control.")
        tab = self._tab(session, {})
        async with tab.lock:
            current = self._session(identity, grant)
            if current is not session or current.state != "manual_control":
                raise BrowserError("invalid_state", "Manual browser ownership changed while resuming.")
            checked_grant = current.grant
            if tab.page.url != "about:blank":
                await self.policy.validate(tab.page.url, checked_grant)
            current = self._session(identity, grant)
            if current is not session or current.grant != checked_grant:
                raise BrowserError("permission_changed", "Browser authority changed while resuming.")
            for current in session.tabs.values():
                self._invalidate(current)
                current.console.clear(); current.page_errors.clear(); current.network_errors.clear()
            session.state = "ready"
        for attempt in range(3):
            try:
                result = await self.dispatch(identity, grant, "snapshot")
                break
            except BrowserError as error:
                if error.code != "stale_observation":
                    raise
                if attempt == 2:
                    # Control has resumed even if an animated page cannot yet
                    # yield a stable observation. Never report a false failure
                    # that tempts the user to repeat the transition.
                    result = self._status(self._session(identity, grant))
                    result.update(observation_pending=True, requires_fresh_snapshot=True)
                else:
                    await asyncio.sleep(0.05)
        result["resumed_by_host"] = True
        return result

    async def _close_context(self, session: BrowserSession):
        try:
            await asyncio.wait_for(session.context.close(), timeout=10)
        except Exception:
            session.state = "failed"
            raise BrowserError("cleanup_unresolved", "Chromium context did not confirm closure; cleanup ownership has been retained.", requires_reconciliation=True) from None
        session.state = "closed"
        for tab_id in session.tabs:
            self._navigation_errors.pop(tab_id, None)
        session.tabs.clear()
        self._sessions.pop(session.identity, None)
        self.artifacts.remove_owner(session.identity)

    async def close_owner(self, identity: BrowserIdentity) -> None:
        session = self._sessions.get(identity)
        if session is None:
            self._grant_providers.pop(identity, None)
            return
        session.cancelled = True
        session.state = "closing"
        self._prepared = {k: v for k, v in self._prepared.items() if v.identity != identity}
        current = asyncio.current_task()
        pending = [task for task in session.pending if task is not current and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if session.identity in self._sessions:
            await self._close_context(session)
        self._grant_providers.pop(identity, None)

    async def close_all(self) -> None:
        cleanup_failed = []
        for identity in list(self._sessions):
            try:
                await self.close_owner(identity)
            except BrowserError:
                cleanup_failed.append(identity)
        for browser in list(self._browsers.values()):
            try:
                await browser.close()
            except Exception:
                pass
        self._browsers.clear()
        process_shutdown_verified = False
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
            process_shutdown_verified = True
        if cleanup_failed and not process_shutdown_verified:
            raise BrowserError("cleanup_unresolved", "Browser closure was not confirmed; failed session ownership remains available for cleanup retry.", requires_reconciliation=True)
        for identity in cleanup_failed:
            session = self._sessions.pop(identity, None)
            if session is not None:
                session.state = "closed"
                session.tabs.clear()
        self._prepared.clear()
        self._grant_providers.clear()
        self._navigation_errors.clear()
        self.artifacts.close()
