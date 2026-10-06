from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import logging
import os
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field as PydanticField

from firik_agent.mcp.protocol import MCPToolSchema

logger = logging.getLogger(__name__)


class MCPStatus(str, Enum):
    CONFIGURED = "configured"
    STARTING = "starting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class MCPTransport(str, Enum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class ToolPolicyMode(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class ToolPolicy(BaseModel):
    mode: ToolPolicyMode = ToolPolicyMode.ALLOW
    allowed_tools: List[str] = PydanticField(default_factory=list)
    confirm_tools: List[str] = PydanticField(default_factory=list)


class MCPServerConfig(BaseModel):
    name: str
    transport: MCPTransport = MCPTransport.STDIO
    command: Optional[str] = None
    args: List[str] = PydanticField(default_factory=list)
    cwd: Optional[str] = None
    env: Dict[str, str] = PydanticField(default_factory=dict)
    secret_env: Dict[str, str] = PydanticField(default_factory=dict)
    url: Optional[str] = None
    headers: Dict[str, str] = PydanticField(default_factory=dict)
    autostart: bool = False
    default_for_chat: bool = False
    enabled: bool = True
    tool_policy: ToolPolicy = PydanticField(default_factory=ToolPolicy)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize configuration preserving yaml compatibility."""
        data: Dict[str, Any] = {}
        if self.transport == MCPTransport.STDIO or (self.command and not self.url):
            data["transport"] = "stdio"
            if self.command:
                data["command"] = self.command
            if self.args:
                data["args"] = list(self.args)
            if self.cwd:
                data["cwd"] = self.cwd
        else:
            data["transport"] = self.transport.value
            if self.url:
                data["url"] = self.url
            if self.headers:
                data["headers"] = dict(self.headers)

        if self.env:
            data["env"] = dict(self.env)
        if self.secret_env:
            data["secret_env"] = dict(self.secret_env)
        if self.autostart:
            data["autostart"] = self.autostart
        if self.default_for_chat:
            data["default_for_chat"] = self.default_for_chat
        if not self.enabled:
            data["enabled"] = self.enabled
        if self.tool_policy.mode != ToolPolicyMode.ALLOW or self.tool_policy.allowed_tools or self.tool_policy.confirm_tools:
            data["tool_policy"] = self.tool_policy.model_dump()

        return data

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> MCPServerConfig:
        cmd = data.get("command")
        url = data.get("url")
        raw_transport = data.get("transport")
        if raw_transport:
            try:
                transport = MCPTransport(raw_transport.lower())
            except ValueError:
                transport = MCPTransport.STDIO if cmd else MCPTransport.HTTP
        else:
            transport = MCPTransport.STDIO if cmd else MCPTransport.HTTP

        policy_data = data.get("tool_policy")
        tool_policy = ToolPolicy(**policy_data) if isinstance(policy_data, dict) else ToolPolicy()

        return cls(
            name=name,
            transport=transport,
            command=cmd,
            args=data.get("args") or [],
            cwd=data.get("cwd"),
            env=data.get("env") or {},
            secret_env=data.get("secret_env") or {},
            url=url,
            headers=data.get("headers") or {},
            autostart=bool(data.get("autostart", False)),
            default_for_chat=bool(data.get("default_for_chat", False)),
            enabled=bool(data.get("enabled", True)),
            tool_policy=tool_policy,
        )


@dataclass
class MCPLogEntry:
    timestamp: str
    level: str
    message: str


@dataclass
class MCPTestResult:
    success: bool
    process_started: bool = False
    initialize_succeeded: bool = False
    protocol_version: Optional[str] = None
    server_name: Optional[str] = None
    server_version: Optional[str] = None
    capabilities: Dict[str, Any] = field(default_factory=dict)
    tool_count: int = 0
    tool_names: List[str] = field(default_factory=list)
    tools: List[MCPToolSchema] = field(default_factory=list)
    duration_ms: float = 0.0
    error: Optional[str] = None


# --- Keyring & Secret Management ---

_KEYRING_PREFIX = "keyring://mcp/"
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|auth|bearer)[\s:=]+['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
]

# Fallback in-memory storage for test/headless environments when OS store is unavailable
_IN_MEMORY_SECRETS: Dict[str, str] = {}


def _get_keyring_backend():
    try:
        from firik_agent.credentials import _backend
        return _backend()
    except Exception:
        return None


def make_secret_ref(server_name: str, key: str) -> str:
    """Format a keyring reference URI."""
    return f"{_KEYRING_PREFIX}{server_name}/{key}"


def parse_secret_ref(ref: str) -> Optional[tuple[str, str]]:
    """Parse a keyring://mcp/<server_name>/<key> reference URI."""
    if not isinstance(ref, str) or not ref.startswith(_KEYRING_PREFIX):
        return None
    rest = ref[len(_KEYRING_PREFIX):]
    parts = rest.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None


def store_mcp_secret(server_name: str, key: str, value: str) -> str:
    """
    Store an MCP secret in the OS keyring and return the reference string.
    Falls back to safe in-memory store if native backend is absent.
    """
    clean_val = (value or "").strip()
    service = f"firik:mcp:{server_name}"
    account = key

    backend = _get_keyring_backend()
    if backend is not None:
        try:
            backend.set_password(service, account, clean_val)
        except Exception as e:
            logger.warning("Failed saving secret to keyring for MCP '%s/%s': %s", server_name, key, e)
            _IN_MEMORY_SECRETS[f"{service}:{account}"] = clean_val
    else:
        _IN_MEMORY_SECRETS[f"{service}:{account}"] = clean_val

    return make_secret_ref(server_name, key)


def get_mcp_secret(server_name: str, key: str) -> str:
    """Retrieve an MCP secret from the OS keyring or in-memory store."""
    service = f"firik:mcp:{server_name}"
    account = key

    backend = _get_keyring_backend()
    if backend is not None:
        try:
            val = backend.get_password(service, account)
            if val:
                return val
        except Exception:
            pass

    return _IN_MEMORY_SECRETS.get(f"{service}:{account}", "")


def delete_mcp_secret(server_name: str, key: str) -> bool:
    """Remove an MCP secret from the OS keyring and in-memory store."""
    service = f"firik:mcp:{server_name}"
    account = key
    _IN_MEMORY_SECRETS.pop(f"{service}:{account}", None)

    backend = _get_keyring_backend()
    if backend is not None:
        try:
            if hasattr(backend, "delete_password"):
                backend.delete_password(service, account)
            elif hasattr(backend, "values") and isinstance(backend.values, dict):
                backend.values.pop((service, account), None)
        except Exception:
            pass
    return True


def delete_all_mcp_secrets(server_name: str, keys: Optional[List[str]] = None) -> None:
    """Delete all secret keys associated with an MCP server."""
    if keys:
        for k in keys:
            delete_mcp_secret(server_name, k)
    # Also clean matching memory secrets
    prefix = f"firik:mcp:{server_name}:"
    for mem_key in list(_IN_MEMORY_SECRETS.keys()):
        if mem_key.startswith(prefix):
            _IN_MEMORY_SECRETS.pop(mem_key, None)


def resolve_server_env(config: MCPServerConfig) -> Dict[str, str]:
    """
    Resolve server environment variables combining:
    1. Regular non-secret env vars
    2. OS Keyring / secret_env references
    3. Plaintext env secrets backward-compatibility
    """
    resolved = dict(config.env)

    for k, ref in config.secret_env.items():
        parsed = parse_secret_ref(ref)
        if parsed:
            s_name, s_key = parsed
            secret_val = get_mcp_secret(s_name, s_key)
            if secret_val:
                resolved[k] = secret_val
        elif ref:
            # Direct value provided
            resolved[k] = ref

    return resolved


def redact_secrets(text: str, extra_secrets: Optional[List[str]] = None) -> str:
    """Mask known secret patterns or specific secret strings from logs/previews."""
    if not text:
        return text

    redacted = text
    if extra_secrets:
        for s in extra_secrets:
            if s and len(s) >= 4 and s in redacted:
                redacted = redacted.replace(s, "••••••••")

    for pat in _SECRET_PATTERNS:
        redacted = pat.sub(r"\1=••••••••", redacted)

    return redacted
