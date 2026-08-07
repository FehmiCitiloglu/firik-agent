from __future__ import annotations

import json
from pathlib import Path

import pytest

from firik_agent.agent import SeniorDevelopmentAgent
from firik_agent.process import DevelopmentProcess
from firik_agent.tools import DevelopmentToolbox


def test_smolagents_adapter_exposes_and_dispatches_every_tool(tmp_path: Path) -> None:
    smolagents = pytest.importorskip("smolagents")
    senior = SeniorDevelopmentAgent(tmp_path, model=smolagents.Model())
    senior.process = DevelopmentProcess(senior.workspace)
    senior.process.start("adapter smoke", task_id="adapter-smoke")
    senior.toolbox = DevelopmentToolbox(senior.process)

    runtime = senior._build_agent(senior.model)
    payload = json.loads(runtime.tools["development_status"].forward())

    registered = {spec.name for spec in senior.toolbox.registry.specs()}
    assert registered.issubset(runtime.tools)
    assert set(runtime.tools) - registered == {"final_answer"}
    assert payload["ok"] is True
    assert payload["data"]["phase"] == "discovery"
