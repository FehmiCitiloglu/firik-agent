"""Host selection and passive UI/CLI paths must not dispatch model work."""
from types import SimpleNamespace
import json
from pathlib import Path
import sys

import pytest

from firik_agent.harness.config import resolve_harness_configuration, select_backend
from firik_agent.harness import cli
from firik_agent.ui.coding_harness import choose_project_backend, execution_trace


def test_legacy_configuration_keeps_native_and_external_disabled():
    settings = resolve_harness_configuration({"providers": {"irrelevant": {}}, "agent": {"harness_profile": "minimal"}})
    assert settings.default_backend == "native" and settings.profile == "minimal"
    assert settings.backends["native"].enabled
    assert not settings.backends["pi"].enabled and not settings.backends["codex"].enabled
    assert not settings.automatic_backend_switching


def test_project_configuration_narrows_enabled_backend_and_preserves_root_fields():
    original = {"harness": {"default_backend": "pi", "backends": {"pi": {"enabled": True, "expected_version": "1.4.0"}}}}
    settings = resolve_harness_configuration(original, {"harness": {"default_backend": "native"}, "harness_profile": "research"})
    assert settings.default_backend == "native" and settings.profile == "research"
    assert settings.pi.expected_version == "1.4.0"
    assert original["harness"]["default_backend"] == "pi"
    assert resolve_harness_configuration({"harness": settings.to_dict()}) == settings


@pytest.mark.parametrize("value", [
    {"default_backend": "unknown"}, {"default_backend": "pi"}, {"backends": {"other": {}}},
    {"backends": {"pi": {"enabled": "true"}}}, {"backends": {"pi": {"inherit_credentials": True}}},
    {"backends": {"pi": {"executable": "pi\nsh"}}}, {"backends": {"pi": {"expected_version": False}}},
    {"optimization": {"automatic_backend_switching": True}}, {"profile": "invented"},
    {"optimization": {"benchmark_based_suggestions": 1}}, {"allow_unsafe_shell": True},
])
def test_invalid_or_unsafe_configuration_fails_before_execution(value):
    with pytest.raises(ValueError):
        resolve_harness_configuration({"harness": value})


def test_active_backend_switch_and_disabled_backend_fail():
    settings = resolve_harness_configuration({"harness": {"backends": {"pi": {"enabled": True}}}})
    with pytest.raises(ValueError, match="active task"):
        select_backend(settings, "pi", active_backend="native")
    with pytest.raises(ValueError, match="disabled"):
        select_backend(settings, "codex")


def test_settings_command_never_reads_default_credentials(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "firik_agent.credentials", SimpleNamespace(ensure_provider_key=lambda *a: pytest.fail("credential access")))
    assert cli.main(["settings"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["default_backend"] == "native"


def test_config_output_excludes_provider_key(tmp_path, capsys):
    path = tmp_path / "settings.yaml"
    path.write_text("providers:\n  example:\n    api_key: secret-provider-value\nharness:\n  default_backend: native\n")
    assert cli.main(["settings", "--config", str(path)]) == 0
    assert "secret-provider-value" not in capsys.readouterr().out


def test_new_routes_dispatch_before_existing_benchmark_parser(monkeypatch):
    from firik_agent.benchmarks import cli as lab
    calls = []
    monkeypatch.setattr(cli, "main", lambda argv: calls.append(argv) or 7)
    assert lab.main(["backends"]) == 7 and calls == [["backends"]]


def test_invalid_json_and_large_reports_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[]")
    with pytest.raises(ValueError, match="JSON object"):
        cli.read_document(path)
    path.write_bytes(b" " * (cli.MAX_REPORT_BYTES + 1))
    with pytest.raises(ValueError, match="64 MiB"):
        cli.read_document(path)


def test_ui_selection_cannot_mutate_active_or_external_coding():
    state = {"busy": True, "preferences": {}}
    calls = []
    view = SimpleNamespace(service=SimpleNamespace(config={}), _coding_state=lambda: state,
                           _save_coding_preferences=lambda value: calls.append(value))
    with pytest.raises(ValueError, match="active task"):
        choose_project_backend(view, "native")
    state["busy"] = False
    view.service.config = {"harness": {"backends": {"pi": {"enabled": True}}}}
    with pytest.raises(ValueError, match="permission policy"):
        choose_project_backend(view, "pi")
    assert calls == []
    choose_project_backend(view, "native")
    assert calls[0]["harness"]["default_backend"] == "native"


def test_trace_is_bounded_and_masks_credentials():
    state = {"session": SimpleNamespace(model="example"), "last_prompt": "task", "busy": False,
             "activities": [{"api_key": "secret-value", "type": "error"}] * 300}
    trace = execution_trace(state)
    assert trace["backend"] == "native" and trace["model"] == "example"
    assert len(trace["events"]) == 200 and "secret-value" not in json.dumps(trace)


def test_offline_experiment_cli_and_passive_analysis_integrate(tmp_path, capsys):
    from firik_agent.benchmarks.coding import coding_suite
    from firik_agent.harness.comparison import ComparisonPlan, ComparativeConfiguration
    from firik_agent.ui.coding_harness import load_comparison_analysis
    suite = coding_suite()
    suite = suite.model_copy(update={"tasks": suite.tasks[:1]})
    plan = ComparisonPlan(suite=suite, repetitions=1, configurations=[ComparativeConfiguration(
        id="native-offline", provider="replay", model="scripted-v1", model_revision="scripted-v1", local_only=True)])
    path, output = tmp_path / "plan.json", tmp_path / "report.json"
    path.write_text(plan.model_dump_json())
    assert cli.main(["experiment", "--plan", str(path), "--output", str(output)]) == 0
    analysis = load_comparison_analysis(output)
    assert analysis["report"]["summary"][0]["verified_successes"] == 1
    assert "recommendation" in analysis
    assert cli.main(["recommend", "--report", str(output)]) == 0
    assert cli.main(["experiment", "--plan", str(path), "--output", str(output)]) == 2


def test_failed_experiment_cli_exports_actual_failure_and_nonzero_exit(tmp_path):
    from firik_agent.benchmarks.coding import coding_suite
    from firik_agent.harness.comparison import ComparisonPlan, ComparativeConfiguration
    suite = coding_suite()
    suite = suite.model_copy(update={"tasks": suite.tasks[:1]})
    plan = ComparisonPlan(suite=suite, repetitions=1, configurations=[ComparativeConfiguration(
        id="unavailable-codex", backend="codex", provider="replay", model="scripted-v1", model_revision="scripted-v1")])
    path, output = tmp_path / "plan.json", tmp_path / "failure.json"
    path.write_text(plan.model_dump_json())
    assert cli.main(["experiment", "--plan", str(path), "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["rows"][0]["status"] == "failed" and report["rows"][0]["errors"]
    assert report["rows"][0]["usage"] == {}


def _prompt_arguments():
    return ["prompt", "--backend", "pi", "--provider", "openai", "--model", "example-model",
            "--prompt", "Explain a pure function", "--credential-env", "EXPLICIT_TEST_KEY"]


def test_tool_free_prompt_requires_live_opt_in_before_any_credential_read(monkeypatch, capsys):
    class NoCredentials:
        def get(self, name):
            pytest.fail("An unauthorized credential was read")
    monkeypatch.setattr(cli, "os", SimpleNamespace(environ=NoCredentials()))
    assert cli.main(_prompt_arguments()) == 2
    assert "--allow-live" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["disabled", "unavailable", "invalid_env", "invalid_timeout"])
def test_tool_free_prompt_setup_fails_before_credential_lookup(kind, monkeypatch):
    from firik_agent.harness import registry
    class NoCredentials:
        def get(self, name):
            pytest.fail("A credential was read before setup validation")
    monkeypatch.setattr(cli, "os", SimpleNamespace(environ=NoCredentials()))
    if kind != "disabled":
        monkeypatch.setattr(registry, "create_backend", lambda *a, **k: SimpleNamespace(
            get_metadata=lambda: SimpleNamespace(available=False, reason="Unavailable test installation")))
    args = _prompt_arguments() + ["--allow-live"]
    if kind == "invalid_env":
        args[args.index("EXPLICIT_TEST_KEY")] = "$HOME"
    if kind == "invalid_timeout":
        args += ["--timeout", "nan"]
    assert cli.main(args) == 2


@pytest.mark.parametrize("crash", [False, True])
def test_tool_free_prompt_reads_only_explicit_key_and_always_tears_down(crash, monkeypatch, capsys):
    from firik_agent.harness import registry
    from firik_agent.harness.models import HarnessResult, HarnessSession, HarnessEvent
    reads, calls = [], []
    key = "plain-secret-unique-value"
    class Credentials:
        def get(self, name):
            reads.append(name)
            assert name == "EXPLICIT_TEST_KEY"
            return key
    class Backend:
        def get_metadata(self):
            return SimpleNamespace(available=True)
        async def initialize_session(self, *, policy, provider):
            assert policy.workspace is None and policy.tool_mode == "none" and policy.allow_network
            assert provider == {"name": "openai", "model": "example-model", "api_key": key, "allow_live": True}
            calls.append("initialize")
            return HarnessSession("s", "pi")
        async def execute_task(self, session, task):
            assert task.prompt == "Explain a pure function" and task.model == "example-model"
            calls.append("execute")
            return "r"
        async def stream_events(self, session, run_id):
            yield HarnessEvent("text", "s", "r", content=key)
            if crash:
                raise RuntimeError("Backend disconnected: " + key)
        async def inspect_run(self, session, run_id):
            return HarnessResult("r", "s", "pi", status="completed", text="answer " + key, model="example-model")
        async def request_cancellation(self, session, run_id):
            calls.append("cancel")
        async def close_session(self, session):
            calls.append("close")
    monkeypatch.setattr(cli, "os", SimpleNamespace(environ=Credentials()))
    monkeypatch.setattr(registry, "create_backend", lambda *a, **k: Backend())
    assert cli.main(_prompt_arguments() + ["--allow-live"]) == (2 if crash else 0)
    captured = capsys.readouterr()
    assert reads == ["EXPLICIT_TEST_KEY"] and calls[-1] == "close"
    assert ("cancel" in calls) is crash
    assert key not in captured.out + captured.err
    if not crash:
        assert json.loads(captured.out)["usage"]["input_tokens"] is None
