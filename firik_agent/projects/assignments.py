"""Immutable, bounded delegation data; task text never grants capabilities."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True)
class AgentBudget:
    token_budget: int
    cost_budget: float | None = None
    timeout_seconds: float = 120

    def __post_init__(self):
        if type(self.token_budget) is not int or not 1 <= self.token_budget <= 2_000_000:
            raise ValueError("Invalid delegated token budget.")
        for name in ("cost_budget", "timeout_seconds"):
            value = getattr(self, name)
            if value is None and name == "cost_budget":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid delegated budget: " + name)
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("Delegated timeout exceeds the hard limit.")


def validated_paths(paths: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for raw in paths:
        if not isinstance(raw, str) or not raw or "\x00" in raw or "\\" in raw:
            raise ValueError("Delegated paths must be relative project paths.")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts or ":" in raw or raw.startswith("-"):
            raise ValueError("Delegated paths must stay inside the project.")
        if any(part in {".git", ".ssh", ".aws", ".codex", ".agents"} for part in path.parts):
            raise ValueError("Delegated path names a protected directory.")
        normalized.append(path.as_posix())
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True)
class AgentAssignment:
    task: str
    role: str
    allowed_paths: tuple[str, ...]
    permitted_tools: frozenset[str]
    required_outputs: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    dependencies: tuple[str, ...]
    budget: AgentBudget
    execution_backend: str

    def __post_init__(self):
        if not isinstance(self.task, str) or not self.task.strip() or len(self.task) > 6000:
            raise ValueError("Delegation requires a bounded task.")
        if self.role not in {"researcher", "worker", "reviewer", "tester"}:
            raise ValueError("Unsupported delegated role.")
        if self.execution_backend not in {"host", "docker"}:
            raise ValueError("Unsupported delegated execution backend.")
        if not isinstance(self.budget, AgentBudget):
            raise ValueError("Delegation requires a validated budget.")
        for name in ("allowed_paths", "required_outputs", "acceptance_criteria", "dependencies", "permitted_tools"):
            values = getattr(self, name)
            if isinstance(values, str) or not isinstance(values, (list, tuple, set, frozenset)):
                raise ValueError("Delegation lists must be structured sequences.")
            object.__setattr__(self, name, frozenset(values) if name == "permitted_tools" else tuple(values))
        object.__setattr__(self, "allowed_paths", validated_paths(self.allowed_paths))
        if self.role == "worker" and not self.allowed_paths:
            raise ValueError("Editing agents require explicit allowed paths.")
        for values in (self.required_outputs, self.acceptance_criteria, self.dependencies, self.permitted_tools):
            if len(values) > 64 or any(not isinstance(value, str) or not value or len(value) > 2000 for value in values):
                raise ValueError("Delegation lists must contain bounded strings.")

    def to_dict(self):
        value = asdict(self)
        value["permitted_tools"] = sorted(self.permitted_tools)
        for name in ("allowed_paths", "required_outputs", "acceptance_criteria", "dependencies"):
            value[name] = list(getattr(self, name))
        return value
