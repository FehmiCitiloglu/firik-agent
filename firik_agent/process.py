"""Gated development lifecycle and persistent task evidence."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .contracts import ToolError
from .workspace import Workspace


class DevelopmentPhase(StrEnum):
    DISCOVERY = "discovery"
    ARCHITECTURE = "architecture"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    REVIEW = "review"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass(slots=True)
class ArchitectureDecision:
    summary: str
    components: list[str]
    interfaces: list[str]
    constraints: list[str]
    risks: list[str]
    acceptance_evidence: list[str]

    def validate(self) -> None:
        fields = asdict(self)
        missing = [name for name, value in fields.items() if not value]
        if missing:
            raise ToolError(f"Architecture is incomplete: {', '.join(missing)}")


@dataclass(slots=True)
class PlanItem:
    id: str
    description: str
    acceptance_criteria: str
    status: str = "pending"

    def validate(self) -> None:
        if not self.id or not self.description or not self.acceptance_criteria:
            raise ToolError("Every plan item needs id, description, and acceptance_criteria")
        if self.status not in {"pending", "in_progress", "complete", "blocked"}:
            raise ToolError(f"Invalid plan status: {self.status}")


@dataclass(slots=True)
class DevelopmentRecord:
    task_id: str
    objective: str
    workspace: str
    phase: DevelopmentPhase = DevelopmentPhase.DISCOVERY
    architecture: ArchitectureDecision | None = None
    plan: list[PlanItem] = field(default_factory=list)
    verification_attempts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["phase"] = self.phase.value
        return data


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DevelopmentProcess:
    """Enforces architecture, planning, verification, and completion gates."""

    def __init__(self, workspace: Workspace, *, max_verification_attempts: int = 3) -> None:
        if max_verification_attempts < 1:
            raise ValueError("max_verification_attempts must be positive")
        self.workspace = workspace
        self.max_verification_attempts = max_verification_attempts
        self._records = workspace.root / ".firik-agent" / "tasks"
        self._records.mkdir(parents=True, exist_ok=True)
        self.record: DevelopmentRecord | None = None

    def start(self, objective: str, task_id: str | None = None) -> DevelopmentRecord:
        if not objective.strip():
            raise ToolError("Objective must not be empty")
        identifier = task_id or self._new_id(objective)
        path = self._record_path(identifier)
        if path.exists():
            raise ToolError(f"Task already exists: {identifier}")
        self.record = DevelopmentRecord(
            task_id=identifier,
            objective=objective.strip(),
            workspace=str(self.workspace.root),
        )
        self._event("task_started", {"objective": objective.strip()})
        self._save()
        return self.record

    def load(self, task_id: str) -> DevelopmentRecord:
        payload = json.loads(self._record_path(task_id).read_text(encoding="utf-8"))
        architecture = payload.get("architecture")
        self.record = DevelopmentRecord(
            task_id=payload["task_id"],
            objective=payload["objective"],
            workspace=payload["workspace"],
            phase=DevelopmentPhase(payload["phase"]),
            architecture=ArchitectureDecision(**architecture) if architecture else None,
            plan=[PlanItem(**item) for item in payload.get("plan", [])],
            verification_attempts=payload.get("verification_attempts", []),
            events=payload.get("events", []),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )
        return self.record

    def begin_architecture(self) -> None:
        self._require_phase(DevelopmentPhase.DISCOVERY)
        self._transition(DevelopmentPhase.ARCHITECTURE, "discovery_completed")

    def set_architecture(self, decision: ArchitectureDecision) -> None:
        self._require_phase(DevelopmentPhase.DISCOVERY, DevelopmentPhase.ARCHITECTURE)
        decision.validate()
        self._require_record().architecture = decision
        self._transition(DevelopmentPhase.PLANNING, "architecture_accepted")

    def set_plan(self, items: list[PlanItem]) -> None:
        self._require_phase(DevelopmentPhase.PLANNING, DevelopmentPhase.IMPLEMENTATION)
        if not items:
            raise ToolError("Plan must contain at least one item")
        for item in items:
            item.validate()
        identifiers = [item.id for item in items]
        if len(set(identifiers)) != len(identifiers):
            raise ToolError("Plan item ids must be unique")
        self._require_record().plan = items
        self._transition(DevelopmentPhase.IMPLEMENTATION, "plan_accepted")

    def authorize_mutation(self) -> None:
        self._require_phase(DevelopmentPhase.IMPLEMENTATION)
        record = self._require_record()
        if record.architecture is None or not record.plan:
            raise ToolError("Architecture and plan are required before implementation")

    def update_plan_item(self, item_id: str, status: str) -> None:
        self._require_phase(DevelopmentPhase.IMPLEMENTATION)
        for item in self._require_record().plan:
            if item.id == item_id:
                item.status = status
                item.validate()
                self._event("plan_updated", {"id": item_id, "status": status})
                self._save()
                return
        raise ToolError(f"Unknown plan item: {item_id}")

    def begin_verification(self) -> None:
        self._require_phase(DevelopmentPhase.IMPLEMENTATION)
        self._transition(DevelopmentPhase.VERIFICATION, "verification_started")

    def record_verification(self, gates: list[dict[str, Any]]) -> bool:
        self._require_phase(DevelopmentPhase.VERIFICATION)
        if not gates:
            raise ToolError("At least one verification gate is required")
        passed = all(bool(gate.get("ok")) for gate in gates)
        attempt = {
            "number": len(self._require_record().verification_attempts) + 1,
            "passed": passed,
            "gates": gates,
            "at": _now(),
        }
        self._require_record().verification_attempts.append(attempt)
        if passed:
            self._transition(DevelopmentPhase.REVIEW, "verification_passed")
        elif len(self._require_record().verification_attempts) >= self.max_verification_attempts:
            self._transition(DevelopmentPhase.BLOCKED, "verification_retry_limit_reached")
        else:
            self._transition(DevelopmentPhase.IMPLEMENTATION, "verification_failed")
        return passed

    def complete(self) -> None:
        self._require_phase(DevelopmentPhase.REVIEW)
        record = self._require_record()
        if not record.verification_attempts or not record.verification_attempts[-1]["passed"]:
            raise ToolError("A successful latest verification is required")
        incomplete = [item.id for item in record.plan if item.status != "complete"]
        if incomplete:
            raise ToolError(f"Plan items are incomplete: {', '.join(incomplete)}")
        self._transition(DevelopmentPhase.COMPLETE, "review_completed")

    def status(self) -> dict[str, Any]:
        return self._require_record().to_dict()

    def _transition(self, target: DevelopmentPhase, event: str) -> None:
        record = self._require_record()
        previous = record.phase
        record.phase = target
        self._event(event, {"from": previous.value, "to": target.value})
        self._save()

    def _event(self, name: str, details: dict[str, Any]) -> None:
        record = self._require_record()
        record.events.append({"event": name, "details": details, "at": _now()})

    def _save(self) -> None:
        record = self._require_record()
        record.updated_at = _now()
        target = self._record_path(record.task_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)

    def _record_path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", task_id):
            raise ToolError("Invalid task id")
        return self._records / f"{task_id}.json"

    def _require_record(self) -> DevelopmentRecord:
        if self.record is None:
            raise ToolError("No active development task")
        return self.record

    def _require_phase(self, *phases: DevelopmentPhase) -> None:
        actual = self._require_record().phase
        if actual not in phases:
            expected = ", ".join(phase.value for phase in phases)
            raise ToolError(f"Action requires phase [{expected}], current phase is {actual.value}")

    @staticmethod
    def _new_id(objective: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", objective.lower()).strip("-")[:40] or "task"
        return f"{slug}-{uuid.uuid4().hex[:8]}"
