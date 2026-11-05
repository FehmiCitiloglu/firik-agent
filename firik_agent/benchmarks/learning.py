"""Reproducible offline paired retrieval fixture using the real learning service.

Run: python -m firik_agent.benchmarks.learning --output /tmp/learning-report.json
This measures retrieval on five fixed cases, never model answer quality.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project
from firik_agent.self_improvement.coordinator import ImprovementCoordinator


def run_learning_fixture() -> dict:
    with tempfile.TemporaryDirectory(prefix="firik-learning-benchmark-") as directory:
        root = Path(directory).resolve()
        database = ProjectDatabase(root / "projects.db")
        database.create_project(Project(id="fixture", name="Learning fixture", local_path=str(root)))
        coordinator = ImprovementCoordinator(database, config={"mode": "experiment", "memory_token_budget": 200})
        try:
            memories = []
            for content in ("Packaging validation uses the project-confirmed wheelcheck command.",
                            "Frontend validation uses the project-confirmed componentcheck command.",
                            "Database validation uses the project-confirmed schemacheck command."):
                proposal = coordinator.propose_memory(content, project_id="fixture")
                evaluated = coordinator.evaluate(proposal["id"])
                applied = coordinator.approve(evaluated["id"], evaluated["revision"])
                memories.append(applied["applied"]["memory_id"])
            cases = [{"query": "wheelcheck", "expected_memory_ids": [memories[0]]},
                     {"query": "componentcheck", "expected_memory_ids": [memories[1]]},
                     {"query": "schemacheck", "expected_memory_ids": [memories[2]]},
                     {"query": "astronomy", "expected_memory_ids": []},
                     {"query": "seafaring", "expected_memory_ids": []}]
            proposal = coordinator.propose_harness({"hypothesis": "A larger approved memory budget recovers relevant records excluded by a 200-token conservative limit.",
                "changes": {"memory_token_budget": 1600}, "primary_metric": "exact_retrieval_correctness"}, project_id="fixture")
            candidate = coordinator.experiment(proposal["id"], cases=cases)
            evaluated = coordinator.evaluate(candidate["id"])
            if not evaluated["evaluation"]["passed"]:
                raise RuntimeError("Paired fixture failed: " + str(evaluated["evaluation"]))
            applied = coordinator.approve(evaluated["id"], evaluated["revision"])
            monitoring = coordinator.monitor(applied["id"], cases)
            rolled_back = coordinator.rollback(applied["id"], applied["revision"])
            return {"schema_version": 1, "suite": "learning-retrieval-v1", "method": "real_coordinator_offline_fixture",
                    "comparison": evaluated["evaluation"], "monitoring": monitoring,
                    "applied_status": applied["status"], "rollback_status": rolled_back["status"],
                    "restored_memory_token_budget": coordinator.settings()["memory_token_budget"],
                    "provider_requests": 0, "limitations": ["Five deterministic fixture cases; no general model quality claim",
                    "Conservative byte token estimates; provider token/cost usage unknown", "Temporary fixture data only; no user database was used"]}
        finally:
            coordinator.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_learning_fixture()
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
