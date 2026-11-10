"""Python JSON-RPC proxy to the Electron Main BrowserHost / Playwright Worker."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from firik_agent.browser.artifacts import BrowserArtifacts
from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity


def _checked(identity: BrowserIdentity) -> BrowserIdentity:
    if not isinstance(identity, BrowserIdentity):
        raise BrowserError("ownership_denied", "Browser identity is host-owned.")
    return identity


class RemoteBrowserManager:
    """Drop-in replacement for BrowserManager that delegates to the Electron BrowserHost over stdio RPC."""

    def __init__(self, server, config: BrowserConfig | None = None):
        self.server = server
        self.config = config or BrowserConfig()
        self.artifacts = BrowserArtifacts()
        self._config_lock = asyncio.Lock()
        self._sent_config: dict | None = None
        self._grant_providers: dict[BrowserIdentity, Callable[[], BrowserGrant]] = {}
        self._cached_sessions: dict[BrowserIdentity, list[dict[str, Any]]] = {}

    async def _ensure_config(self) -> None:
        # Source configuration is authoritative, including tighter resource
        # limits and strict-network-isolation rejection. Never silently use
        # the worker's defaults for a user's configured policy.
        async with self._config_lock:
            config = self.config.model_dump()
            if config != self._sent_config:
                await self.server.request_host("browser.configureExecutor", {"config": config})
                self._sent_config = config

    async def _request_host(self, method: str, params: dict, **options) -> dict:
        await self._ensure_config()
        return await self.server.request_host(method, params, **options)

    def bind_grant_provider(self, identity: BrowserIdentity, provider: Callable[[], BrowserGrant]) -> None:
        checked = _checked(identity)
        if not callable(provider):
            raise ValueError("Browser grant provider must be callable.")
        self._grant_providers[checked] = provider

    def _live_grant(self, identity: BrowserIdentity, fallback: BrowserGrant) -> BrowserGrant:
        # Grants are validated by BrowserGrant.__post_init__; a failing provider
        # revokes authority instead of falling back to a stale grant.
        provider = self._grant_providers.get(identity)
        if provider is None:
            current = fallback
        else:
            try:
                current = provider()
            except Exception:
                return BrowserGrant()
        if not isinstance(current, BrowserGrant):
            return BrowserGrant()
        return current

    def _enabled(self, grant: BrowserGrant) -> None:
        if self.config.strict_network_isolation:
            raise BrowserError("network_isolation_unavailable", "Strict network isolation requires an enforceable egress boundary.")
        if not self.config.enabled or not grant.enabled:
            raise BrowserError(
                "permission_denied",
                "Browser automation is disabled until granted by the host session.",
            )

    @staticmethod
    def _identity_wire(identity: BrowserIdentity) -> dict[str, Any]:
        checked = _checked(identity)
        return {
            "conversation_id": checked.conversation_id,
            "project_id": checked.project_id,
            "run_id": checked.run_id,
            "actor_id": checked.actor_id,
            "parent_agent_id": checked.parent_agent_id,
        }

    def _grant_wire(self, identity: BrowserIdentity, grant: BrowserGrant) -> dict[str, Any]:
        checked = _checked(identity)
        live = self._live_grant(checked, grant)
        return {
            "enabled": bool(live.enabled and self.config.enabled),
            "origins": list(live.origins),
            "allow_private_origins": list(live.allow_private_origins),
            "allow_screenshots": bool(live.allow_screenshots),
            "allow_uploads": bool(live.allow_uploads),
            "allow_downloads": bool(live.allow_downloads),
            "allow_public_https": bool(live.allow_public_https),
        }

    def _update_cache(self, identity: BrowserIdentity, operation: str, payload: dict[str, Any]) -> None:
        checked = _checked(identity)
        clean_op = operation.removeprefix("browser_")
        state = payload.get("state")
        if clean_op == "close" or state in {"closed", "closing"}:
            self._cached_sessions.pop(checked, None)
            return
        if clean_op == "uninitialized" or state == "uninitialized":
            self._cached_sessions.pop(checked, None)
            return
        existing = (
            dict(self._cached_sessions[checked][0])
            if self._cached_sessions.get(checked)
            else {
                "session_id": payload.get("session_id", f"browser_{checked.conversation_id}"),
                "conversation_id": checked.conversation_id,
                "actor_id": checked.actor_id,
                "state": "ready",
            }
        )
        if isinstance(state, str) and state:
            existing["state"] = state
        if clean_op == "request_handoff":
            existing["state"] = "manual_control"
        elif clean_op == "resume" or payload.get("resumed_by_host"):
            existing["state"] = "ready"
        for key in ("active_tab_id", "tab_count", "revision", "tabs", "url", "title"):
            if key in payload:
                existing[key] = payload[key]
        if isinstance(payload.get("tabs"), list):
            active = next((tab for tab in payload["tabs"] if isinstance(tab, dict) and tab.get("active")), None)
            if active:
                existing["url"], existing["title"] = active.get("url", ""), active.get("title", "")
        self._cached_sessions[checked] = [existing]

    async def prepare(
        self,
        identity: BrowserIdentity,
        grant: BrowserGrant,
        operation: str,
        raw_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checked = _checked(identity)
        live = self._live_grant(checked, grant)
        self._enabled(live)
        result = await self._request_host(
            "browser.prepare",
            {
                "identity": self._identity_wire(checked),
                "grant": self._grant_wire(checked, live),
                "operation": operation,
                "arguments": dict(raw_args or {}),
            },
            timeout=self.config.action_timeout_ms / 1000 + 10,
        )
        return dict(result.get("payload") or {})

    async def dispatch(
        self,
        identity: BrowserIdentity,
        grant: BrowserGrant,
        operation: str,
        raw_args: dict[str, Any] | None = None,
        *,
        approved_fingerprint: str | None = None,
        host_control: bool = False,
    ) -> dict[str, Any]:
        checked = _checked(identity)
        live = self._live_grant(checked, grant)
        self._enabled(live)
        result = await self._execute_host(
            "browser.execute",
            {
                "execution_id": f"exec_{uuid4().hex}",
                "identity": self._identity_wire(checked),
                "grant": self._grant_wire(checked, live),
                "operation": operation,
                "arguments": dict(raw_args or {}),
                "approved_fingerprint": approved_fingerprint,
                "host_control": bool(host_control),
            },
        )
        payload = dict(result.get("payload") or {})
        if operation.removeprefix("browser_") == "screenshot":
            # Import the private worker artifact into the existing Python
            # ownership registry. Never expose the worker's filesystem path
            # to the model, transcript, or renderer.
            data = await self._read_ephemeral_artifact(payload)
            current = self._live_grant(checked, live)
            if not current.enabled or not current.allow_screenshots or not self.config.enabled:
                raise BrowserError("permission_denied", "Screenshot permission was revoked during capture.")
            artifact = self.artifacts.save(checked, data)
            payload.pop("artifact_path", None)
            payload.update(artifact_id=artifact.artifact_id, size_bytes=artifact.size)
        self._update_cache(checked, operation, payload)
        return payload

    async def _execute_host(self, method: str, params: dict) -> dict:
        """A lost transport acknowledgement never proves a mutation did not run."""
        await self._ensure_config()
        try:
            timeout = max(self.config.action_timeout_ms, self.config.navigation_timeout_ms,
                          self.config.launch_timeout_ms) / 1000 + 15
            return await self.server.request_host(method, params, timeout=timeout)
        except (TimeoutError, ConnectionError, ValueError) as error:
            effect = params.get("operation", "").removeprefix("browser_") in {
                "navigate", "back", "forward", "reload", "click", "fill", "type", "press",
                "select", "check", "hover", "scroll", "host_input",
            }
            raise BrowserError(
                "tool_outcome_unknown" if effect else "browser_operation_failed",
                "Browser yanıtı alınamadı; işlem otomatik tekrarlanmadı.",
                dispatched=effect, requires_reconciliation=effect,
            ) from error

    async def host_command(
        self,
        identity: BrowserIdentity,
        grant: BrowserGrant,
        operation: str,
        raw_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.dispatch(
            identity,
            grant,
            operation,
            raw_args,
            host_control=True,
        )

    async def host_input(
        self,
        identity: BrowserIdentity,
        grant: BrowserGrant,
        kind: str,
        raw_args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checked = _checked(identity)
        live = self._live_grant(checked, grant)
        self._enabled(live)
        result = await self._execute_host(
            "browser.execute",
            {
                "execution_id": f"exec_{uuid4().hex}",
                "identity": self._identity_wire(checked),
                "grant": self._grant_wire(checked, live),
                "operation": "host_input",
                "arguments": {"kind": kind, "arguments": dict(raw_args or {})},
                "host_control": True,
            },
        )
        payload = dict(result.get("payload") or {})
        self._update_cache(checked, "host_input", payload)
        return payload

    async def resume(self, identity: BrowserIdentity, grant: BrowserGrant) -> dict[str, Any]:
        checked = _checked(identity)
        live = self._live_grant(checked, grant)
        self._enabled(live)
        result = await self._request_host(
            "browser.execute",
            {
                "execution_id": f"exec_{uuid4().hex}",
                "identity": self._identity_wire(checked),
                "grant": self._grant_wire(checked, live),
                "operation": "resume",
                "arguments": {},
                "host_control": True,
            },
        )
        payload = dict(result.get("payload") or {})
        self._update_cache(checked, "resume", payload)
        return payload

    async def _read_ephemeral_artifact(self, payload: dict[str, Any]) -> bytes:
        artifact_path = payload.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            raise BrowserError("browser_operation_failed", "Preview artifact was not produced.")
        path = Path(artifact_path)
        try:
            with path.open("rb") as stream:
                data = stream.read(self.config.max_screenshot_bytes + 1)
            if len(data) > self.config.max_screenshot_bytes:
                raise BrowserError("artifact_limit", "Screenshot exceeds the configured size limit.")
            return data
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    async def preview(
        self,
        identity: BrowserIdentity,
        grant: BrowserGrant,
        *,
        full_page: bool = False,
    ) -> bytes:
        checked = _checked(identity)
        live = self._live_grant(checked, grant)
        self._enabled(live)
        result = await self._request_host(
            "browser.execute",
            {
                "execution_id": f"exec_{uuid4().hex}",
                "identity": self._identity_wire(checked),
                "grant": self._grant_wire(checked, live),
                "operation": "preview",
                "arguments": {"full_page": bool(full_page)},
                "host_control": False,
            },
        )
        return await self._read_ephemeral_artifact(dict(result.get("payload") or {}))

    async def host_preview(self, identity: BrowserIdentity, grant: BrowserGrant) -> bytes:
        checked = _checked(identity)
        live = self._live_grant(checked, grant)
        self._enabled(live)
        result = await self._request_host(
            "browser.execute",
            {
                "execution_id": f"exec_{uuid4().hex}",
                "identity": self._identity_wire(checked),
                "grant": self._grant_wire(checked, live),
                "operation": "host_preview",
                "arguments": {},
                "host_control": True,
            },
        )
        return await self._read_ephemeral_artifact(dict(result.get("payload") or {}))

    def sessions(self, identity: BrowserIdentity) -> list[dict[str, Any]]:
        checked = _checked(identity)
        return [dict(item) for item in self._cached_sessions.get(checked, [])]

    def artifact_path(self, identity: BrowserIdentity, artifact_id: str) -> Path:
        return self.artifacts.path(_checked(identity), artifact_id)

    async def sync_grant(self, identity: BrowserIdentity, grant: BrowserGrant) -> None:
        checked = _checked(identity)
        if self.server._closing:
            return
        await self._request_host(
            "browser.revoke",
            {
                "identity": self._identity_wire(checked),
                "grant": self._grant_wire(checked, grant),
                "close_owner": not bool(grant.enabled and self.config.enabled),
                "close_all": False,
            },
        )
        if not grant.enabled or not self.config.enabled:
            self._cached_sessions.pop(checked, None)

    async def close_owner(self, identity: BrowserIdentity) -> None:
        checked = _checked(identity)
        self.artifacts.remove_owner(checked)
        for session in self._cached_sessions.get(checked, []):
            session["state"] = "closing"
        try:
            if self.server._closing:
                raise ConnectionError("Desktop transport already closed.")
            await self._request_host(
                "browser.revoke",
                {"identity": self._identity_wire(checked), "grant": {"enabled": False},
                 "close_owner": True, "close_all": False},
                timeout=8.0,
            )
        except Exception as error:
            for session in self._cached_sessions.get(checked, []):
                session.update(state="failed", requires_reconciliation=True)
            raise BrowserError("cleanup_unresolved", "Browser closure could not be confirmed.",
                               requires_reconciliation=True) from error
        self._cached_sessions.pop(checked, None)
        self._grant_providers.pop(checked, None)

    async def close_all(self) -> None:
        had_owners = bool(self._cached_sessions or self._grant_providers)
        self.artifacts.close()
        if not had_owners:
            return
        if self.server._closing:
            raise BrowserError("cleanup_unresolved", "Browser cleanup could not be confirmed after transport closure.")
        await self._request_host("browser.revoke", {"close_all": True}, timeout=5.0)
        self._cached_sessions.clear()
        self._grant_providers.clear()
