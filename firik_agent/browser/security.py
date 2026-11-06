"""Host URL policy and bounded redaction, not an OS network sandbox.

DNS is rechecked for each intercepted request. Chromium still resolves its own
connections: this is not a defense against DNS time-of-check/time-of-use races.
Strict isolation is deliberately unavailable until an egress boundary exists.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

from firik_agent.browser.models import BrowserError, BrowserGrant
from firik_agent.projects.indexer import sanitize_secrets


def origin_of(url: str) -> str:
    try:
        if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 for c in url) or "\\" in url:
            raise ValueError()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if "%" in host or not host:
            raise ValueError()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
        display = f"[{host}]" if ":" in host else host
        suffix = "" if port == (443 if parsed.scheme == "https" else 80) else f":{port}"
        return f"{parsed.scheme}://{display}{suffix}"
    except (ValueError, UnicodeError):
        raise BrowserError("invalid_url", "Only HTTP(S) URLs without credentials are permitted.") from None


def sanitized_url(url: str) -> str:
    try:
        origin = origin_of(url)
        parsed = urlsplit(url)
        # Query values and fragments routinely contain tokens and are omitted.
        return redact(origin + parsed.path)[:2000]
    except BrowserError:
        return "[blocked URL]" if url != "about:blank" else url


def redact(value: str, limit: int = 2000) -> str:
    text = sanitize_secrets(str(value))
    text = re.sub(r"https?://[^\s<>\"']+", lambda m: _strip_query(m.group()), text)
    return text[:limit]


def _strip_query(value: str) -> str:
    try:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))
    except ValueError:
        return "[URL]"


class BrowserSecurityPolicy:
    async def validate(self, url: str, grant: BrowserGrant) -> str:
        if not grant.enabled:
            raise BrowserError("permission_denied", "Browser capability is disabled.")
        origin = origin_of(url)
        allowed = {origin_of(item) for item in grant.origins}
        private_allowed = {origin_of(item) for item in grant.allow_private_origins}
        if origin not in allowed:
            raise BrowserError("origin_denied", "The destination origin has not been authorized.")
        hostname = urlsplit(origin).hostname
        try:
            addresses = [ipaddress.ip_address(hostname)]
        except ValueError:
            try:
                loop = asyncio.get_running_loop()
                answers = await asyncio.wait_for(loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM), timeout=5)
                addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
            except (OSError, ValueError, asyncio.TimeoutError):
                raise BrowserError("dns_failed", "Could not safely resolve the destination.") from None
        if not addresses:
            raise BrowserError("dns_failed", "Destination has no resolved addresses.")
        if any(not address.is_global for address in addresses) and origin not in private_allowed:
            raise BrowserError("private_network_denied", "Private network access requires an exact origin grant.")
        return origin
