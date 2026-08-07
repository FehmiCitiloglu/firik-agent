"""Shared, framework-neutral contracts for Firik Agent development tools."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class SideEffect(StrEnum):
    """The strongest side effect a tool can perform."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Serializable result returned by every deterministic tool."""

    ok: bool
    data: Any = None
    error: str | None = None

    @classmethod
    def success(cls, data: Any = None) -> ToolResult:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str, data: Any = None) -> ToolResult:
        return cls(ok=False, data=data, error=error)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ToolHandler = Callable[..., ToolResult]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Definition consumed by both direct callers and model adapters."""

    name: str
    description: str
    inputs: dict[str, dict[str, Any]]
    side_effect: SideEffect
    handler: ToolHandler


class ToolError(RuntimeError):
    """Raised when a tool request violates a deterministic policy."""
