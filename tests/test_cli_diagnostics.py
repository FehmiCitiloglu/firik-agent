"""CLI diagnostics exercise real parsing and HTTP boundaries without live keys."""
import json
from io import StringIO
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from prompt_toolkit.document import Document
from rich.console import Console

from firik_agent import cli, credentials, diagnostics
from firik_agent.activity import ActivityLog
from firik_agent.core.failures import FailureKind, RequestPhase, RuntimeFailure
from firik_agent.providers.openai_provider import OpenAIProvider


def config_file(tmp_path, text):
    path = tmp_path / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_offline_doctor_dispatch_does_not_construct_agent_read_credentials_or_connect(tmp_path, monkeypatch, capsys):
    path = config_file(tmp_path, "agent:\n  default_provider: openai\nproviders:\n  openai:\n    default_model: test-model\n    api_key: TOP-SECRET\n")
    def forbidden(*args, **kwargs):
        pytest.fail("Offline diagnostics initialized an agent, accessed credentials or used the network")
    monkeypatch.setattr(cli, "Agent", forbidden)
    monkeypatch.setattr(credentials, "get_api_key", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr("firik_agent.config.load_dotenv", forbidden)
    monkeypatch.setattr(sys, "argv", ["firik-agent", "doctor", "--config", str(path), "--json"])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["model"] == "test-model" and report["ready"]
    assert report["credentials"] == "not_checked" and report["completion_requests"] == 0
    assert report["context"]["context_window"] is None and report["warnings"]
    assert "TOP-SECRET" not in output


@pytest.mark.parametrize("text", [
    "[TOP-SECRET", "[TOP-SECRET]", "agent: []", "providers: []",
    "providers:\n  firik: []", "agent:\n  recovery:\n    max_requests: -1",
    "agent:\n  temperature: .nan", "agent:\n  harness_profile: TOP-SECRET",
    "execution:\n  backend: invalid", "providers:\n  firik:\n    base_url: https://user:TOP-SECRET@example.test/v1",
    "agent:\n  context_compaction:\n    enabled: TOP-SECRET", "agent:\n  context_compaction:\n    trigger_ratio: .nan",
])
def test_malformed_config_has_nonzero_status_and_never_echoes_yaml(text, tmp_path, capsys):
    path = config_file(tmp_path, text)
    assert diagnostics.main(["--config", str(path), "--json"]) == 2
    output = capsys.readouterr().out
    assert not json.loads(output)["ready"] and "TOP-SECRET" not in output


def test_missing_explicit_config_is_a_failure(tmp_path, capsys):
    assert diagnostics.main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert "Configuration file does not exist" in capsys.readouterr().out


def test_known_context_and_explicit_harness_limit_match_runtime_budget(tmp_path, capsys):
    path = config_file(tmp_path, """agent:
  harness_profile: coding
  harness_overrides:
    context_tokens: 12000
providers:
  firik:
    default_model: mimo-v2.6-pro
    model_capabilities:
      mimo-v2.6-pro:
        source: verified_config
        context_window: 1048576
""")
    assert diagnostics.main(["--config", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["context"]["context_window"] == 1048576
    assert report["context"]["input_budget"] == 12000
    assert report["context"]["output_reservation"] == 2048
    assert report["resources"]["max_requests"] == 100


def test_unknown_context_uses_configured_compactor_budget_before_prompt_overhead(tmp_path, capsys):
    path = config_file(tmp_path, "agent:\n  harness_profile: coding\n  context_compaction:\n    fallback_input_tokens: 512\n")
    assert diagnostics.main(["--config", str(path), "--model", "unknown-model", "--json"]) == 0
    context = json.loads(capsys.readouterr().out)["context"]
    assert context["input_budget"] == 512 and "overhead" in context["budget_basis"]


@pytest.mark.parametrize("field", ["max_tokens", "max_cost", "max_elapsed"])
def test_zero_resource_budget_is_not_ready(field, tmp_path, capsys):
    path = config_file(tmp_path, f"agent:\n  recovery:\n    {field}: 0\n")
    assert diagnostics.main(["--config", str(path), "--json"]) == 2
    assert any("cannot admit" in warning for warning in json.loads(capsys.readouterr().out)["warnings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("row", [None, {"id": "mimo-v2.6-pro"}, {"id": "mimo-v2.6-pro", "verified": False, "context_length": 1048576},
                                   {"id": "mimo-v2.6-pro", "context_length": 1024}])
async def test_probe_recomputes_capacity_with_fresh_evidence_without_mutating_active_provider(row, monkeypatch):
    from firik_agent.providers.firik import FirikProvider
    provider = FirikProvider(default_model="mimo-v2.6-pro")
    before = dict(provider.model_metadata)
    report = diagnostics.inspect_configuration({}, provider)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [] if row is None else [row]})), **kwargs))
    await diagnostics.probe_provider(report, provider)
    assert provider.model_metadata == before
    if row and row.get("context_length") == 1024:
        assert report["context"]["input_budget"] == 0 and not report["ready"]
    else:
        assert report["context"]["context_window"] is None
        assert report["context"]["input_budget"] == 6000
        assert report["warnings"]
    if row is None:
        assert not report["ready"]


@pytest.mark.parametrize("outcome", ["ok", "missing", "denied", "bad_json"])
def test_probe_only_gets_selected_catalogue_and_preserves_safe_failure_details(outcome, tmp_path, monkeypatch, capsys):
    path = config_file(tmp_path, "providers:\n  openai:\n    base_url: https://catalogue.invalid/v1\n    default_model: selected\n")
    requests = []
    lookups = []
    def respond(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models" and not request.content
        assert request.headers["Authorization"] == "Bearer TOP-SECRET"
        if outcome == "denied":
            return httpx.Response(401, json={"error": {"code": "invalid_api_key", "message": "TOP-SECRET"}}, headers={"X-Request-ID": "req-fixture"})
        if outcome == "bad_json":
            return httpx.Response(200, text="TOP-SECRET")
        return httpx.Response(200, json={"data": [{"id": "other" if outcome == "missing" else "selected", "context_length": 32768}]})
    original = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(credentials, "get_api_key", lambda name, *a, **k: lookups.append(name) or "TOP-SECRET")
    result = diagnostics.main(["--config", str(path), "--provider", "openai", "--probe", "--json"])
    output = capsys.readouterr().out
    report = json.loads(output)
    assert result == (0 if outcome == "ok" else 2)
    assert len(requests) == 1 and lookups == ["openai"]
    assert report["completion_requests"] == 0 and "TOP-SECRET" not in output
    if outcome == "ok":
        assert report["context"]["context_window"] == 32768 and not report["warnings"]
    if outcome == "denied":
        assert "http_status=401" in output and "req-fixture" in output


def test_cli_error_is_literal_safe_and_retained_in_doctor_and_activity(monkeypatch):
    stream = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stream, width=240))
    agent = SimpleNamespace()
    error = RuntimeFailure(FailureKind.UNKNOWN, phase=RequestPhase.NOT_SUBMITTED,
                           source_type="ValueError", provider="fixture")
    error.metadata["provider_code"] = "password=TOP-SECRET"
    cli.report_cli_failure(agent, error)
    assert "error_type=ValueError" in stream.getvalue() and "request_phase=not_submitted" in stream.getvalue()
    assert "TOP-SECRET" not in stream.getvalue() + agent._cli_last_failure
    activity = ActivityLog()
    activity.failure(error)
    assert "error_type=ValueError" in activity.plain_details() and "1 errors" in activity.summary
    assert "TOP-SECRET" not in activity.plain_details()


@pytest.mark.asyncio
async def test_interactive_doctor_uses_current_selection_and_last_failure_without_probe(monkeypatch):
    provider = OpenAIProvider(default_model="current-model")
    agent = SimpleNamespace(config={}, providers=SimpleNamespace(get_active=lambda: provider),
                            _cli_last_failure="failure_kind=unknown error_type=ValueError")
    stream = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stream, width=240))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: pytest.fail("Implicit probe"))
    assert await cli.handle_slash_command(agent, "/doctor --json")
    report = json.loads(stream.getvalue())
    assert report["model"] == "current-model" and "ValueError" in report["last_failure"]
    assert any(item.text == "/doctor" for item in cli.AgentCompleter(agent).get_completions(Document("/doc"), None))
    assert any(item.text == "--probe" for item in cli.AgentCompleter(agent).get_completions(Document("/doctor --p"), None))


def test_single_shot_failure_is_nonzero_safe_and_closes_agent(monkeypatch, capsys):
    error = RuntimeFailure(FailureKind.PROVIDER_OVERLOADED, http_status=503, provider_code="model_capacity_exhausted", source_type="HTTPStatusError")
    agent = SimpleNamespace(providers=SimpleNamespace(get_active=lambda: object()),
                            run=AsyncMock(side_effect=error), close=AsyncMock())
    monkeypatch.setattr(cli, "Agent", lambda **kwargs: agent)
    monkeypatch.setattr(cli, "ensure_provider_key", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["firik-agent", "--prompt", "Synthetic request"])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 1 and agent.close.await_count == 1
    output = capsys.readouterr()
    assert "http_status=503" in output.err and "model_capacity_exhausted" in output.err
    assert "Traceback" not in output.err


def test_startup_failure_is_safe_and_points_to_doctor(monkeypatch, capsys):
    def fail(**kwargs):
        raise ValueError("TOP-SECRET malformed source line")
    monkeypatch.setattr(cli, "Agent", fail)
    monkeypatch.setattr(sys, "argv", ["firik-agent", "--prompt", "Synthetic request"])
    with pytest.raises(SystemExit) as raised:
        cli.main()
    assert raised.value.code == 2
    output = capsys.readouterr().err
    assert "error_type=ValueError" in output and "firik-agent doctor" in output
    assert "TOP-SECRET" not in output and "Traceback" not in output
