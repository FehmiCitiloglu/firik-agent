from __future__ import annotations

from pathlib import Path

import pytest

from firik_agent.contracts import ToolError
from firik_agent.process import (
    ArchitectureDecision,
    DevelopmentPhase,
    DevelopmentProcess,
    PlanItem,
)
from firik_agent.workspace import Workspace


def architecture() -> ArchitectureDecision:
    return ArchitectureDecision(
        summary="A small layered change",
        components=["core"],
        interfaces=["public API"],
        constraints=["backward compatible"],
        risks=["regression"],
        acceptance_evidence=["tests pass"],
    )


def test_process_enforces_architecture_plan_verification_and_completion(tmp_path: Path) -> None:
    process = DevelopmentProcess(Workspace(tmp_path))
    record = process.start("Implement feature", task_id="feature-1")

    with pytest.raises(ToolError, match="implementation"):
        process.authorize_mutation()

    process.begin_architecture()
    process.set_architecture(architecture())
    process.set_plan([PlanItem("P1", "Implement", "Tests pass")])
    process.authorize_mutation()
    process.update_plan_item("P1", "complete")
    process.begin_verification()

    assert process.record_verification([{"name": "tests", "ok": True}]) is True
    process.complete()

    assert process.record.phase is DevelopmentPhase.COMPLETE
    loaded = DevelopmentProcess(Workspace(tmp_path)).load(record.task_id)
    assert loaded.phase is DevelopmentPhase.COMPLETE
    assert loaded.architecture == architecture()


def test_process_blocks_after_bounded_verification_failures(tmp_path: Path) -> None:
    process = DevelopmentProcess(Workspace(tmp_path), max_verification_attempts=2)
    process.start("Implement feature")
    process.set_architecture(architecture())
    process.set_plan([PlanItem("P1", "Implement", "Tests pass")])

    process.begin_verification()
    assert process.record_verification([{"name": "tests", "ok": False}]) is False
    assert process.record.phase is DevelopmentPhase.IMPLEMENTATION

    process.begin_verification()
    assert process.record_verification([{"name": "tests", "ok": False}]) is False
    assert process.record.phase is DevelopmentPhase.BLOCKED


def test_process_rejects_incomplete_architecture_and_plan(tmp_path: Path) -> None:
    process = DevelopmentProcess(Workspace(tmp_path))
    process.start("Implement feature")

    with pytest.raises(ToolError, match="incomplete"):
        process.set_architecture(
            ArchitectureDecision("summary", [], ["api"], ["safe"], ["risk"], ["test"])
        )

    process.set_architecture(architecture())
    with pytest.raises(ToolError, match="at least one"):
        process.set_plan([])
