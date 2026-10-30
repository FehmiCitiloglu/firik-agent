"""Backend-neutral contracts. Unknown measurements remain unknown, never zero."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any
from uuid import uuid4


class HarnessError(RuntimeError):
    def __init__(self, code: str, message: str, *, backend: str = "",
                 details: dict[str, Any] | None = None, retryable: bool = False):
        super().__init__(message)
        self.code, self.backend = code, backend
        self.details, self.retryable = dict(details or {}), retryable

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "backend": self.backend,
                "details": self.details, "retryable": self.retryable}


@dataclass(frozen=True)
class HarnessMetadata:
    name: str
    version: str | None = None
    available: bool = True
    reason: str | None = None
    interface: str = ""
    license: str | None = None
    security_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessCapability:
    name: str
    supported: bool | None
    evidence: str = ""


@dataclass(frozen=True)
class HarnessExecutionPolicy:
    workspace: str | None = None
    project_id: str = ""
    tool_mode: str = "none"
    allow_network: bool = False
    require_sandbox: bool = False
    timeout_seconds: float = 300
    max_events: int = 1024
    max_sessions: int = 4
    token_budget: int | None = None
    permissions: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.tool_mode not in {"none", "native", "sandbox"}:
            raise ValueError("Unknown harness tool mode.")
        if (isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float))
                or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 86400):
            raise ValueError("Harness timeout must be finite and between 0 and 86400 seconds.")
        for name, high in (("max_events", 100000), ("max_sessions", 32)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= high:
                raise ValueError("Invalid harness limit: " + name)
        if self.token_budget is not None and (type(self.token_budget) is not int or self.token_budget <= 0):
            raise ValueError("Token budget must be a positive integer or unknown.")
        if type(self.allow_network) is not bool or type(self.require_sandbox) is not bool:
            raise ValueError("Harness security flags must be boolean.")
        # Detach from mutable caller policy; adapters still validate requested semantics.
        from copy import deepcopy
        object.__setattr__(self, "permissions", deepcopy(self.permissions))


@dataclass(frozen=True)
class HarnessSession:
    id: str
    backend: str
    project_id: str = ""
    workspace: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessTask:
    prompt: str
    id: str = field(default_factory=lambda: "task_" + uuid4().hex)
    model: str | None = None
    profile: str = "coding"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("A harness task requires a nonempty prompt.")


@dataclass(frozen=True)
class HarnessToolInvocation:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    status: str = "requested"
    execution_boundary: str = "unknown"
    approved: bool | None = None
    result: str | None = None


@dataclass(frozen=True)
class HarnessUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    source: str = "unknown"

    def __post_init__(self):
        for name in ("input_tokens", "output_tokens", "cached_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Usage counts must be nonnegative integers or unknown.")
        if self.cost_usd is not None and (isinstance(self.cost_usd, bool)
                or not isinstance(self.cost_usd, (int, float)) or not math.isfinite(self.cost_usd) or self.cost_usd < 0):
            raise ValueError("Cost must be finite and nonnegative or unknown.")


@dataclass(frozen=True)
class HarnessCompletionEvidence:
    verified: bool = False
    evidence: tuple[dict[str, Any], ...] = ()
    classification: str = "unverified"


@dataclass(frozen=True)
class HarnessEvent:
    type: str
    session_id: str
    run_id: str
    sequence: int = 0
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    backend: str = ""
    native_event: Any = field(default=None, repr=False, compare=False)

    def to_dict(self):
        # Do not copy native runtime objects into exports or persistence.
        return {key: getattr(self, key) for key in (
            "type", "session_id", "run_id", "sequence", "content", "metadata", "id", "backend")}


@dataclass(frozen=True)
class HarnessResult:
    run_id: str
    session_id: str
    backend: str
    status: str = "running"
    text: str = ""
    model: str | None = None
    usage: HarnessUsage = field(default_factory=HarnessUsage)
    completion: HarnessCompletionEvidence = field(default_factory=HarnessCompletionEvidence)
    errors: tuple[HarnessError, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {"run_id": self.run_id, "session_id": self.session_id, "backend": self.backend,
                "status": self.status, "text": self.text, "model": self.model,
                "usage": asdict(self.usage), "completion": asdict(self.completion),
                "errors": [error.to_dict() for error in self.errors], "metadata": self.metadata}
