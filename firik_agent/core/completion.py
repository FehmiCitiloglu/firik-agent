"""Evidence-based completion on top of the host's existing quality gates.

This module never executes tests, changes permissions, or accepts model prose as
execution evidence. Tool registries remain the authority for evidence identities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping


class CompletionState(str, Enum):
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    REVIEWING = "reviewing"
    COMPLETED_VERIFIED = "completed_verified"
    COMPLETED_UNVERIFIED = "completed_unverified"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


LEGACY_STATUS = {"completed_verified": "completed", "completed_unverified": "completed",
                 "repairing": "correcting"}
LIFECYCLE_ALIASES = {"understanding": "planning", "applying": "implementing",
                     "correcting": "repairing", "testing": "verifying"}


def legacy_status(state: str) -> str:
    return LEGACY_STATUS.get(state, state)


@dataclass
class CompletionController:
    """Run-local lifecycle and bounded repair memory, persisted by host snapshots."""

    state: str = "planning"
    classification: str | None = None
    max_repair_cycles: int = 3
    repair_cycles: int = 0
    _failed_actions: dict[str, str] = field(default_factory=dict)
    last_result: dict[str, Any] = field(default_factory=dict)

    def transition(self, state: str) -> None:
        candidate = LIFECYCLE_ALIASES.get(state, state)
        if candidate in {value.value for value in CompletionState}:
            self.state = candidate
            if candidate.startswith("completed_"):
                self.classification = candidate

    @staticmethod
    def action_identity(command: str, cwd: str, workspace_hash: str) -> str:
        return hashlib.sha256(json.dumps([command, cwd, workspace_hash], ensure_ascii=False).encode()).hexdigest()

    def permit_verification(self, command: str, cwd: str, workspace_hash: str) -> bool:
        """An identical failed command needs changed source or new host evidence."""
        return (self.repair_cycles < self.max_repair_cycles
                and self.action_identity(command, cwd, workspace_hash) not in self._failed_actions)

    def observe_verification(self, evidence: Mapping[str, Any]) -> None:
        if evidence.get("passed") and evidence.get("fresh"):
            self.transition("reviewing")
            return
        self.repair_cycles += 1
        identity = self.action_identity(str(evidence.get("command", "")), str(evidence.get("cwd", ".")),
                                        str(evidence.get("workspace_hash", "")))
        # Store a digest rather than potentially sensitive command output.
        self._failed_actions[identity] = hashlib.sha256(str(evidence.get("output", "")).encode()).hexdigest()
        self.transition("repairing" if self.repair_cycles < self.max_repair_cycles else "blocked")

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "classification": self.classification,
                "repair_cycles": self.repair_cycles, "max_repair_cycles": self.max_repair_cycles,
                "failed_actions": dict(self._failed_actions), "last_result": dict(self.last_result)}

    def evaluate(self, *, mode: str, plan: Iterable[Mapping[str, Any]],
                 verifications: Iterable[Mapping[str, Any]], quality_gates: Iterable[Mapping[str, Any]],
                 quality_results: Iterable[Mapping[str, Any]], verified_ids: set[str],
                 workspace_revision: int, workspace_hash: str, workspace_complete: bool,
                 changes: bool, state: str = "", unresolved_operations: Iterable[Any] = ()) -> dict[str, Any]:
        reasons: list[str] = []
        evidence = list(verifications)
        plans = list(plan)
        gates = list(quality_gates)
        results = list(quality_results)
        readonly = mode in {"ask", "plan"}
        valid = {str(item["evidence_id"]) for item in evidence
                 if item.get("kind") == "test" and item.get("evidence_id") in verified_ids
                 and item.get("passed") is True and item.get("executed") is True
                 and item.get("fresh") is True and item.get("workspace_revision") == workspace_revision
                 and item.get("workspace_hash") == workspace_hash and workspace_complete
                 and (item.get("exit_code") == 0 or
                      (item.get("execution_backend") == "in_process_ast" and item.get("exit_code") is None))}
        if state in {"blocked", "cancelled", "failed"}:
            reasons.append("Çalışma engellendi, başarısız oldu veya durduruldu.")
        if list(unresolved_operations):
            reasons.append("Sonucu bilinmeyen işlemler uzlaştırılmadan görev tamamlanamaz.")
        if not readonly:
            for step in plans:
                title = str(step.get("title", step.get("id", "")))
                if step.get("status") != "completed":
                    reasons.append(f"Plan adımı tamamlanmadı: {title}")
                elif (not step.get("acceptance_criteria") or not step.get("verification")
                      or not step.get("evidence_ids") or any(value not in valid for value in step["evidence_ids"])):
                    reasons.append(f"Plan adımının güncel doğrulama kanıtı yok: {title}")
            for gate in gates:
                if not gate.get("enabled", True) or not gate.get("required", True):
                    continue
                result = next((value for value in reversed(results) if value.get("gate_id") == gate.get("id")), None)
                if (not result or not result.get("passed") or result.get("evidence_id") not in valid
                        or result.get("command") != gate.get("command")):
                    reasons.append(f"Kalite kapısı başarılı ve güncel değil: {gate.get('kind', gate.get('id', ''))}")
            # A newer successful run of the same command can resolve a previous
            # failure. A different green command cannot erase failed tests.
            latest_tests: dict[tuple[str, str], Mapping[str, Any]] = {}
            latest_criteria: dict[str, Mapping[str, Any]] = {}
            for item in evidence:
                if item.get("kind") == "test":
                    latest_tests[(str(item.get("command", "")), str(item.get("cwd", ".")))] = item
                elif item.get("kind") == "criterion":
                    latest_criteria[str(item.get("criterion", ""))] = item
            if any(item.get("passed") is False or item.get("status") in {"failed", "blocked"}
                   for item in latest_tests.values()):
                reasons.append("Çözümlenmemiş başarısız doğrulama komutu var.")
            for criterion, item in latest_criteria.items():
                if item.get("status") != "passed" or item.get("evidence_id") not in valid:
                    reasons.append(f"Kabul kriteri doğrulanmadı: {criterion}")
            if changes and not valid:
                reasons.append("Dosya değişiklikleri güncel doğrulama gerektiriyor.")
        defined_requirements = bool(plans) or any(gate.get("required", True) and gate.get("enabled", True) for gate in gates)
        classification = ("cancelled" if state == "cancelled" else "failed" if state == "failed" else "blocked") if reasons else (
            "completed_verified" if valid and (not changes or defined_requirements) else "completed_unverified")
        result = {"passed": not reasons, "status": "passed" if not reasons else "blocked", "reasons": list(dict.fromkeys(reasons)),
                  "completion_classification": classification, "lifecycle_state": classification,
                  "legacy_status": legacy_status(classification), "workspace_revision": workspace_revision,
                  "workspace_hash": workspace_hash, "evidence_ids": sorted(valid),
                  "repair_cycles": self.repair_cycles, "max_repair_cycles": self.max_repair_cycles}
        self.last_result = result
        self.classification = classification
        return result
