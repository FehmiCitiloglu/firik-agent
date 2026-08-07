from __future__ import annotations

import json
import sys
from pathlib import Path

from firik_agent.process import DevelopmentProcess
from firik_agent.tools import DevelopmentToolbox
from firik_agent.workspace import Workspace


def test_toolbox_executes_complete_gated_workflow(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    (tmp_path / "smoke.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".firik-agent.toml").write_text(
        f"""
[verification]
commands = [
  {{ name = "smoke", command = ["{sys.executable}", "smoke.py"] }},
]
""",
        encoding="utf-8",
    )
    process = DevelopmentProcess(Workspace(tmp_path))
    process.start("Update README", task_id="readme-task")
    tools = DevelopmentToolbox(process).registry

    assert tools.invoke("write_file", path="README.md", content="early\n").ok is False
    assert tools.invoke("inspect_project").ok is True
    assert (
        tools.invoke(
            "set_architecture",
            architecture_json=json.dumps(
                {
                    "summary": "Update one document",
                    "components": ["README"],
                    "interfaces": ["documentation"],
                    "constraints": ["workspace only"],
                    "risks": ["incorrect text"],
                    "acceptance_evidence": ["smoke gate"],
                }
            ),
        ).ok
        is True
    )
    assert (
        tools.invoke(
            "set_plan",
            plan_json=json.dumps(
                [{"id": "P1", "description": "Edit", "acceptance_criteria": "Gate passes"}]
            ),
        ).ok
        is True
    )
    assert tools.invoke("write_file", path="README.md", content="new\n").ok is True
    assert tools.invoke("update_plan_item", item_id="P1", status="complete").ok is True
    assert tools.invoke("verify_project").ok is True
    assert tools.invoke("complete_task").ok is True

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "new\n"
    assert process.record.phase.value == "complete"


def test_unknown_tool_and_invalid_json_are_structured_failures(tmp_path: Path) -> None:
    process = DevelopmentProcess(Workspace(tmp_path))
    process.start("Task")
    tools = DevelopmentToolbox(process).registry

    assert tools.invoke("does_not_exist").ok is False
    result = tools.invoke("set_architecture", architecture_json="not json")
    assert result.ok is False
    assert result.error is not None
