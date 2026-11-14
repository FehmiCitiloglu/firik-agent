"""Host-owned identities and browser state, never authority from tool arguments."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import time
from typing import Any
from uuid import uuid4


class BrowserError(Exception):
    def __init__(self, code: str, message: str, *, dispatched: bool = False,
                 requires_reconciliation: bool = False):
        super().__init__(message)
        self.code = code
        self.dispatched = dispatched
        self.requires_reconciliation = requires_reconciliation


@dataclass(frozen=True)
class BrowserIdentity:
    conversation_id: str
    project_id: str = ""
    run_id: str = ""
    actor_id: str = "parent"
    parent_agent_id: str = ""

    def __post_init__(self):
        for field_name in ("conversation_id", "project_id", "run_id", "actor_id", "parent_agent_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or len(value) > 256 or any(ord(character) < 32 for character in value):
                raise ValueError("Browser identity fields must be bounded text without control characters.")
        if not self.conversation_id or not self.actor_id:
            raise ValueError("Browser identity requires a conversation and actor.")


@dataclass(frozen=True)
class BrowserGrant:
    enabled: bool = False
    origins: tuple[str, ...] = ()
    allow_private_origins: tuple[str, ...] = ()
    allow_screenshots: bool = False
    allow_uploads: bool = False
    allow_downloads: bool = False
    # Desktop browsing does not require a site-by-site allowlist. Scoped CLI
    # and delegated grants can leave this off to retain exact-origin access.
    allow_public_https: bool = False

    def __post_init__(self):
        from urllib.parse import urlsplit
        from firik_agent.browser.security import origin_of
        for name in ("enabled", "allow_screenshots", "allow_uploads", "allow_downloads", "allow_public_https"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("Browser grant flags must be booleans.")
        for name in ("origins", "allow_private_origins"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) > 32:
                raise ValueError("Browser origins require a tuple with at most 32 entries.")
            normalized = []
            for value in values:
                if not isinstance(value, str) or len(value) > 2048:
                    raise ValueError("Browser origins must be bounded URL strings.")
                try:
                    parsed = urlsplit(value)
                    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                        raise ValueError("Browser grants require origins, without paths, queries or fragments.")
                    canonical = origin_of(value)
                except BrowserError as error:
                    raise ValueError("Invalid browser origin grant.") from error
                if canonical not in normalized:
                    normalized.append(canonical)
            object.__setattr__(self, name, tuple(normalized))
        if not set(self.allow_private_origins).issubset(self.origins):
            raise ValueError("Private browser origins must be a subset of authorized origins.")


class DiagnosticBuffer(deque):
    """A bounded buffer that makes lost diagnostic evidence explicit."""
    def __init__(self, maxlen=100):
        super().__init__(maxlen=maxlen)
        self.dropped = 0

    def append(self, value):
        if len(self) == self.maxlen:
            self.dropped += 1
        super().append(value)

    def clear(self):
        super().clear()
        self.dropped = 0


@dataclass
class BrowserTab:
    page: Any
    tab_id: str = field(default_factory=lambda: "tab_" + uuid4().hex[:16])
    revision: int = 0
    observed_digest: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    console: DiagnosticBuffer = field(default_factory=DiagnosticBuffer)
    page_errors: DiagnosticBuffer = field(default_factory=DiagnosticBuffer)
    network_errors: DiagnosticBuffer = field(default_factory=DiagnosticBuffer)


@dataclass
class BrowserSession:
    identity: BrowserIdentity
    context: Any
    grant: BrowserGrant
    session_id: str = field(default_factory=lambda: "browser_" + uuid4().hex[:16])
    context_id: str = field(default_factory=lambda: "context_" + uuid4().hex[:16])
    state: str = "starting"
    tabs: dict[str, BrowserTab] = field(default_factory=dict)
    active_tab_id: str = ""
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    actions: int = 0
    cancelled: bool = False
    headless: bool = True
    viewport: tuple[int, int] = (1280, 800)
    pending: set = field(default_factory=set)
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
