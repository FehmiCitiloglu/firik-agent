from __future__ import annotations

import json
from pathlib import Path

import pytest

from firik_agent import DevelopmentAgent
from firik_agent.process import DevelopmentProcess
from firik_agent.tools import DevelopmentToolbox


def test_smolagents_adapter_exposes_and_dispatches_every_tool(tmp_path: Path) -> None:
    smolagents = pytest.importorskip("smolagents")
    development_agent = DevelopmentAgent(tmp_path, model=smolagents.Model())
    development_agent.process = DevelopmentProcess(development_agent.workspace)
    development_agent.process.start("adapter smoke", task_id="adapter-smoke")
    development_agent.toolbox = DevelopmentToolbox(development_agent.process)

    runtime = development_agent._build_agent(development_agent.model)
    payload = json.loads(runtime.tools["development_status"].forward())

    registered = {spec.name for spec in development_agent.toolbox.registry.specs()}
    assert registered.issubset(runtime.tools)
    assert set(runtime.tools) - registered == {"final_answer"}
    assert payload["ok"] is True
    assert payload["data"]["phase"] == "discovery"
