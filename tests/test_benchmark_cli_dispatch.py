"""Offline dispatch must precede agent configuration and credential prompts."""
import sys
from types import SimpleNamespace

import pytest

from firik_agent.api import cli as api_cli
from firik_agent import cli as agent_cli


def test_agent_benchmark_dispatch_does_not_construct_live_agent(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        pytest.fail("Offline CLI constructed a live agent or requested credentials")
    monkeypatch.setattr(agent_cli, "Agent", forbidden)
    monkeypatch.setattr(agent_cli, "ensure_provider_key", forbidden)
    monkeypatch.setattr(sys, "argv", ["firik-agent", "benchmark", "list", "--json"])
    monkeypatch.setitem(sys.modules, "firik_agent.benchmarks.cli",
                        SimpleNamespace(main=lambda args: calls.append(args) or 0))
    with pytest.raises(SystemExit) as result:
        agent_cli.main()
    assert result.value.code == 0 and calls == [["list", "--json"]]


def test_api_benchmark_dispatch_does_not_create_api_client(monkeypatch):
    calls = []
    async def forbidden(*args, **kwargs):
        pytest.fail("Offline benchmark entered the paid API command path")
    monkeypatch.setattr(api_cli, "run", forbidden)
    monkeypatch.setitem(sys.modules, "firik_agent.benchmarks.cli",
                        SimpleNamespace(main=lambda args: calls.append(args) or 0))
    assert api_cli.main(["benchmark", "--help"]) == 0
    assert calls == [["--help"]]


def test_harness_alias_dispatches_before_credentials(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "firik_agent.benchmarks.cli",
                        SimpleNamespace(main=lambda args: calls.append(args) or 0))
    assert api_cli.main(["harness", "doctor"]) == 0
    monkeypatch.setattr(sys, "argv", ["firik-agent", "harness", "doctor"])
    with pytest.raises(SystemExit) as result:
        agent_cli.main()
    assert result.value.code == 0
    assert calls == [["doctor"], ["doctor"]]
