"""Host learning commands stay provider-free and bind reviewed revisions."""
import json
import sys
from types import ModuleType

import httpx
import pytest

from firik_agent.self_improvement.cli import main


@pytest.fixture
def coordinator_stub(monkeypatch):
    calls = []

    class Coordinator:
        def __init__(self, database, config=None, **kwargs):
            calls.append(("init", str(database.db_path), config))

        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                return {"action": name, "args": args, "kwargs": kwargs}
            return call

        def close(self):
            calls.append(("close",))

    module = ModuleType("firik_agent.self_improvement.coordinator")
    module.ImprovementCoordinator = Coordinator
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return calls


def test_early_dispatch_never_builds_agent_reads_credentials_or_dotenv(tmp_path, monkeypatch, capsys, coordinator_stub):
    from firik_agent import cli, config, credentials
    def forbidden(*args, **kwargs):
        pytest.fail("Learning inspection accessed an Agent, credentials, dotenv or network")
    monkeypatch.setattr(cli, "Agent", forbidden)
    monkeypatch.setattr(config, "load_dotenv", forbidden)
    monkeypatch.setattr(credentials, "get_api_key", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(sys, "argv", ["firik-agent", "improve", "status", "--db", str(tmp_path / "projects.db")])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 0
    assert json.loads(capsys.readouterr().out)["action"] == "status"
    assert coordinator_stub[-1] == ("close",)


@pytest.mark.parametrize("action", ["approve", "reject", "rollback"])
def test_decision_requires_exact_revision(action, tmp_path, coordinator_stub, capsys):
    database = str(tmp_path / "projects.db")
    with pytest.raises(SystemExit) as raised:
        main(["--db", database, action, "proposal_1"])
    assert raised.value.code == 2
    assert not coordinator_stub
    assert main(["--db", database, action, "proposal_1", "--revision", "7"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["kwargs"] == {"expected_revision": 7, "actor": "user"}


def test_memory_correction_and_scoped_context_use_existing_service_contract(tmp_path, coordinator_stub, capsys):
    database = str(tmp_path / "projects.db")
    assert main(["remember", "Use pytest -q", "--db", database, "--project", "project_1",
                 "--supersedes", "memory_1", "--kind", "project"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "propose_memory"
    assert result["kwargs"]["project_id"] == "project_1"
    assert result["kwargs"]["supersedes_id"] == "memory_1"
    assert main(["--db", database, "retrieve", "validation", "--project", "project_2", "--token-budget", "80"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "retrieve"
    assert result["kwargs"] == {"project_id": "project_2", "token_budget": 80}


@pytest.mark.parametrize("text", ["[secret", "[1, 2]"])
def test_invalid_configuration_is_bounded_and_not_echoed(text, tmp_path, coordinator_stub, capsys):
    config = tmp_path / "settings.yaml"
    config.write_text(text)
    assert main(["--config", str(config), "status"]) == 2
    assert not coordinator_stub
    assert "secret" not in capsys.readouterr().err


def test_invalid_settings_json_never_calls_configure(tmp_path, coordinator_stub, capsys):
    assert main(["--db", str(tmp_path / "projects.db"), "settings", "--set", "[1]"]) == 2
    assert not any(row[0] == "configure" for row in coordinator_stub)
    assert coordinator_stub[-1] == ("close",)


def test_oversize_workflow_is_never_read_or_staged(tmp_path, coordinator_stub, capsys):
    source = tmp_path / "workflow.md"
    source.write_text("x" * (64 * 1024 + 1))
    assert main(["--db", str(tmp_path / "projects.db"), "learn-workflow", "workflow",
                 "--description", "Run validation", "--instructions", str(source)]) == 2
    assert not any(row[0] == "propose_skill" for row in coordinator_stub)


def test_unknown_model_management_operation_is_not_a_command(tmp_path, coordinator_stub):
    with pytest.raises(SystemExit):
        main(["--db", str(tmp_path / "projects.db"), "run-generated-plugin", "unreviewed.py"])
    assert not coordinator_stub


@pytest.mark.parametrize("action,expected", [("propose-tool", "propose_tool"), ("propose-harness", "propose_harness")])
def test_typed_proposal_files_are_only_staged(action, expected, tmp_path, coordinator_stub, capsys):
    payload = tmp_path / "proposal.json"
    payload.write_text(json.dumps({"problem": "Repeated structured inspection failure", "schema": {"type": "object"}}))
    assert main(["--db", str(tmp_path / "projects.db"), action, "--file", str(payload),
                 "--project", "project_1", "--evidence", "experience_1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == expected
    assert result["args"][0]["problem"] == "Repeated structured inspection failure"
    assert result["kwargs"] == {"project_id": "project_1", "evidence_ids": ["experience_1"]}


def test_source_patch_and_allowed_paths_reach_host_staging(tmp_path, coordinator_stub, capsys):
    patch = tmp_path / "candidate.diff"
    patch.write_text("diff --git a/firik_agent/example.py b/firik_agent/example.py\n")
    assert main(["stage-code", "--db", str(tmp_path / "projects.db"), "--repository", str(tmp_path),
                 "--allowed-file", "firik_agent/example.py", "--allowed-file", "firik_agent/other.py",
                 "--patch", str(patch)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "stage_code"
    assert result["args"] == [str(tmp_path), ["firik_agent/example.py", "firik_agent/other.py"], patch.read_text()]


def test_experiment_requires_explicit_host_fixture_or_gate_file(tmp_path, coordinator_stub, capsys):
    with pytest.raises(SystemExit):
        main(["experiment", "p_1"])
    assert not coordinator_stub
    fixture = tmp_path / "fixtures.json"
    fixture.write_text('[{"input":{},"expected":{}}]')
    assert main(["--db", str(tmp_path / "projects.db"), "experiment", "p_1", "--fixtures", str(fixture)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "experiment"
    assert result["kwargs"] == {"fixtures": [{"input": {}, "expected": {}}], "gates": None, "cases": None}


def test_revision_and_disable_commands_require_reviewed_versions(tmp_path, coordinator_stub, capsys):
    payload = tmp_path / "payload.json"
    payload.write_text('{"instructions":"Use focused verification"}')
    assert main(["--db", str(tmp_path / "projects.db"), "revise", "p_1", "--revision", "4", "--payload", str(payload)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "revise" and result["kwargs"] == {"expected_revision": 4}
    assert main(["disable-skill", "p_2", "--revision", "3", "--db", str(tmp_path / "projects.db")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "disable_skill"
    assert result["kwargs"] == {"expected_revision": 3, "actor": "user"}


def test_paired_experiment_and_monitor_use_explicit_host_cases(tmp_path, coordinator_stub, capsys):
    cases = tmp_path / "cases.json"
    cases.write_text('[{"query":"pytest","expected_memory_ids":["m_1"]}]')
    base = ["--db", str(tmp_path / "projects.db")]
    assert main(base + ["experiment", "p_1", "--cases", str(cases)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "experiment"
    assert result["kwargs"] == {"fixtures": None, "gates": None,
                                "cases": [{"query": "pytest", "expected_memory_ids": ["m_1"]}]}
    assert main(base + ["monitor", "p_1", "--cases", str(cases)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["action"] == "monitor"
    assert result["args"] == ["p_1", [{"query": "pytest", "expected_memory_ids": ["m_1"]}]]
    assert not any(row[0] in {"approve", "rollback"} for row in coordinator_stub)


def test_generation_requires_digest_consent_before_any_agent(tmp_path, coordinator_stub, monkeypatch):
    monkeypatch.setattr("firik_agent.core.agent.Agent", lambda **kwargs: pytest.fail("No digest consent"))
    with pytest.raises(SystemExit) as raised:
        main(["generate-code", "--repository", str(tmp_path), "--allowed-file", "example.py", "--task", "Fix parser"])
    assert raised.value.code == 2 and not coordinator_stub


def test_generation_requires_explicit_config_and_experiment_mode_before_startup(tmp_path, coordinator_stub, monkeypatch, capsys):
    monkeypatch.setattr("firik_agent.core.agent.Agent", lambda **kwargs: pytest.fail("Disabled generation constructed Agent"))
    base = ["--db", str(tmp_path / "projects.db"), "generate-code", "--repository", str(tmp_path),
            "--allowed-file", "example.py", "--task", "Fix parser", "--allow-model-digest"]
    assert main(base) == 2
    assert "explicit --config" in capsys.readouterr().err
    config = tmp_path / "config.yaml"
    config.write_text("self_improvement:\n  mode: observe\n")
    coordinator_class = sys.modules["firik_agent.self_improvement.coordinator"].ImprovementCoordinator
    monkeypatch.setattr(coordinator_class, "settings", lambda self: {"mode": "observe"}, raising=False)
    assert main(base + ["--config", str(config)]) == 2
    assert "experiment mode" in capsys.readouterr().err


def test_generation_uses_configured_agent_and_closes_it_without_switching_model(tmp_path, coordinator_stub, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("self_improvement:\n  mode: experiment\nproviders:\n  chosen:\n    default_model: pinned-model\n")
    lifecycle = []
    class Agent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model = "pinned-model"
            self.provider = "chosen"
            self._is_initialized = False
            lifecycle.append(("created", self))
        async def initialize(self):
            self._is_initialized = True
            lifecycle.append(("initialized", self))
        async def close(self):
            lifecycle.append(("closed", self))
    monkeypatch.setattr("firik_agent.core.agent.Agent", Agent)
    coordinator_class = sys.modules["firik_agent.self_improvement.coordinator"].ImprovementCoordinator
    monkeypatch.setattr(coordinator_class, "settings", lambda self: {"mode": "experiment"}, raising=False)
    async def generate(self, agent, repository, allowed_paths, task, **kwargs):
        assert agent._is_initialized
        assert agent.model == "pinned-model" and agent.provider == "chosen"
        assert str(agent._learning_database.db_path) == str(tmp_path / "projects.db")
        assert agent.kwargs["config_path"] == str(config)
        assert kwargs == {"project_id": "project_1", "evidence_ids": ["exp_1"], "allow_model_digest": True}
        lifecycle.append(("generated", agent))
        return {"status": "draft", "paths": allowed_paths, "task": task}
    monkeypatch.setattr(coordinator_class, "generate_code", generate, raising=False)
    assert main(["--config", str(config), "generate-code", "--repository", str(tmp_path),
                 "--allowed-file", "example.py", "--task", "Fix parser", "--project", "project_1",
                 "--evidence", "exp_1", "--allow-model-digest"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "draft", "paths": ["example.py"], "task": "Fix parser"}
    assert [name for name, _ in lifecycle] == ["created", "initialized", "generated", "closed"]


def test_failed_generation_still_closes_existing_agent(tmp_path, coordinator_stub, monkeypatch, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("self_improvement:\n  mode: experiment\n")
    closed = []
    class Agent:
        def __init__(self, **kwargs):
            pass
        async def initialize(self):
            raise RuntimeError("Provider unavailable")
        async def close(self):
            closed.append(True)
    monkeypatch.setattr("firik_agent.core.agent.Agent", Agent)
    coordinator_class = sys.modules["firik_agent.self_improvement.coordinator"].ImprovementCoordinator
    monkeypatch.setattr(coordinator_class, "settings", lambda self: {"mode": "experiment"}, raising=False)
    assert main(["--config", str(config), "generate-code", "--repository", str(tmp_path),
                 "--allowed-file", "example.py", "--task", "Fix parser", "--allow-model-digest"]) == 2
    assert closed == [True]
    assert "Provider unavailable" in capsys.readouterr().err
