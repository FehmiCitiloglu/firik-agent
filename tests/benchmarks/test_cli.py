import json

from firik_agent.benchmarks.cli import main


def test_doctor_and_coding_manifest_are_offline(monkeypatch, capsys):
    def forbidden(*_, **__):
        raise AssertionError("Offline command touched live provider credentials")
    monkeypatch.setattr("firik_agent.benchmarks.live.configured_provider", forbidden)
    assert main(["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["offline_suite"]["tasks"] >= 12
    assert main(["list", "--suite", "coding", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["tasks"]) == 40
    assert main(["bench", "--suite", "coding"]) == 2
    assert "allow-live" in capsys.readouterr().err
