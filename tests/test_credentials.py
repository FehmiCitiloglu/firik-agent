import getpass
import sys
import warnings

import httpx
import pytest

from evren_agent import credentials
from evren_agent.api.cli import run
from evren_agent.providers.registry import ProviderRegistry


KEY = "test-only-secret-key"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for env, _ in credentials.DEFAULTS.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("EVREN_BASE_URL", raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda *a, **kw: KEY)


def test_first_run_saved_and_reused_in_another_directory(credential_store, monkeypatch, tmp_path, capsys):
    assert credentials.ensure_api_key("evren", {}) == KEY
    assert list(credential_store.values.values()) == [KEY]
    folder = tmp_path / "another-project"
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda *a, **kw: pytest.fail("Prompted twice"))
    assert credentials.ensure_api_key("evren", {}) == KEY
    assert not (tmp_path / ".env").exists()
    assert not (folder / ".env").exists()
    output = capsys.readouterr()
    assert KEY not in output.out + output.err
    assert "kaydedildi" in output.err


def test_environment_precedence_and_placeholders(monkeypatch):
    credentials.ensure_api_key("evren", {})
    monkeypatch.setenv("EVREN_API_KEY", "environment-key")
    assert credentials.get_api_key("evren", {}) == "environment-key"
    monkeypatch.setenv("EVREN_API_KEY", "evren_llm_your_key_here")
    assert credentials.get_api_key("evren", {}) == KEY


def test_provider_endpoint_and_env_isolation(monkeypatch):
    credentials.ensure_api_key("evren", {})
    assert credentials.get_api_key("llmtr", {}) == ""
    assert credentials.get_api_key("evren", {}, base_url="https://other.example/v1") == ""
    assert credentials.get_api_key("evren", {"api_key_env": "CUSTOM_EVREN_KEY"}) == ""
    assert credentials.get_api_key("evren", {}, base_url="https://evren-llmapi.ssyz.org.tr/v1/") == KEY
    monkeypatch.setenv("CUSTOM_EVREN_KEY", "custom-key")
    assert credentials.get_api_key("evren", {"api_key_env": "CUSTOM_EVREN_KEY"}) == "custom-key"


def test_noninteractive_requires_existing_key(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(credentials.getpass, "getpass", lambda *a, **kw: pytest.fail("Unexpected prompt"))
    with pytest.raises(ValueError, match="evren login"):
        credentials.ensure_api_key("evren", {})
    monkeypatch.setenv("EVREN_API_KEY", KEY)
    assert credentials.ensure_api_key("evren", {}) == KEY


@pytest.mark.parametrize("value", ["", "   ", "evren_llm_your_key_here", "bad key", "bad\nkey"])
def test_invalid_input_never_saved(value, monkeypatch, credential_store):
    monkeypatch.setattr(credentials.getpass, "getpass", lambda *a, **kw: value)
    with pytest.raises(ValueError, match="Geçerli"):
        credentials.ensure_api_key("evren", {})
    assert not credential_store.values


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_cancellation_preserves_existing_key(error, monkeypatch, credential_store):
    credentials.ensure_api_key("evren", {})
    def cancel(*args, **kwargs):
        raise error()
    monkeypatch.setattr(credentials.getpass, "getpass", cancel)
    with pytest.raises(ValueError, match="iptal"):
        credentials.ensure_api_key("evren", {}, replace=True)
    assert list(credential_store.values.values()) == [KEY]


def test_no_visible_getpass_fallback(monkeypatch, credential_store):
    def fallback(*args, **kwargs):
        warnings.warn("Cannot hide input", getpass.GetPassWarning)
        pytest.fail("Echoing fallback must not run")
    monkeypatch.setattr(credentials.getpass, "getpass", fallback)
    with pytest.raises(ValueError, match="Gizli giriş"):
        credentials.ensure_api_key("evren", {})
    assert not credential_store.values


def test_storage_failure_uses_session_only_and_redacts_error(monkeypatch, capsys):
    def unavailable():
        raise RuntimeError(f"backend leaked {KEY}")
    monkeypatch.setattr(credentials, "_backend", unavailable)
    assert credentials.ensure_api_key("evren", {}) == KEY
    assert credentials.get_api_key("evren", {}) == ""
    with pytest.raises(RuntimeError, match="kaydedilemedi") as error:
        credentials.ensure_api_key("evren", {}, replace=True)
    assert KEY not in str(error.value)
    output = capsys.readouterr()
    assert "yalnızca bu çalıştırmada" in output.err
    assert KEY not in output.out + output.err


def test_only_native_backends_allowed(monkeypatch, credential_store):
    native = type("Keyring", (), {"__module__": "keyring.backends.macOS"})()
    plaintext = type("Keyring", (), {"__module__": "keyrings.alt.file"})()
    monkeypatch.setattr(credentials.keyring, "get_keyring", lambda: plaintext)
    with pytest.raises(RuntimeError):
        credential_store.native_lookup()
    chain = type("ChainerBackend", (), {"__module__": "keyring.backends.chainer", "backends": [plaintext, native]})()
    monkeypatch.setattr(credentials.keyring, "get_keyring", lambda: chain)
    assert credential_store.native_lookup() is native


def test_registry_uses_saved_keys():
    credentials.ensure_api_key("llmtr", {})
    registry = ProviderRegistry.from_config({"agent": {"default_provider": "llmtr"}})
    assert registry.get_active().api_key == KEY
    assert registry.get("evren").api_key == ""


@pytest.mark.asyncio
async def test_cli_first_request_then_saved_key(monkeypatch, capsys):
    def handler(request):
        assert request.headers["X-API-Key"] == KEY
        return httpx.Response(200, json={"data": []})
    transport = httpx.MockTransport(handler)
    assert await run(["models"], transport=transport) == 0
    monkeypatch.setattr(credentials.getpass, "getpass", lambda *a, **kw: pytest.fail("Prompted twice"))
    assert await run(["models"], transport=transport) == 0
    output = capsys.readouterr()
    assert output.out.count('"data": []') == 2
    assert KEY not in output.out + output.err


@pytest.mark.asyncio
async def test_login_replaces_key_with_no_network(monkeypatch, capsys):
    credentials.ensure_api_key("evren", {})
    monkeypatch.setenv("EVREN_API_KEY", "old-environment-key")
    monkeypatch.setattr(credentials.getpass, "getpass", lambda *a, **kw: "replacement-key")
    transport = httpx.MockTransport(lambda _: pytest.fail("Login should not call the API"))
    assert await run(["login"], transport=transport) == 0
    assert "önceliklidir" in capsys.readouterr().err
    monkeypatch.delenv("EVREN_API_KEY")
    assert credentials.get_api_key("evren", {}) == "replacement-key"


@pytest.mark.asyncio
async def test_login_honors_provider_config_and_url(tmp_path):
    config = tmp_path / "custom.yaml"
    config.write_text("providers:\n  llmtr:\n    api_key_env: CUSTOM_KEY\n")
    assert await run(["login", "--provider", "llmtr", "--config", str(config),
                      "--base-url", "https://custom.example/v1"]) == 0
    assert credentials.get_api_key("llmtr", {"api_key_env": "CUSTOM_KEY"},
                                   base_url="https://custom.example/v1") == KEY
    assert credentials.get_api_key("llmtr", {}) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("argv", [["health"], ["api-docs"], ["request", "GET", "/healthz"]])
async def test_public_commands_do_not_touch_credentials(argv, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Public command must not touch credential store or prompt")
    monkeypatch.setattr(credentials, "_backend", unexpected)
    monkeypatch.setattr(credentials.getpass, "getpass", unexpected)
    assert await run(argv, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))) == 0


@pytest.mark.asyncio
async def test_bare_cli_onboards_then_shows_help(capsys):
    assert await run([]) == 0
    assert credentials.get_api_key("evren", {}) == KEY
    assert "login" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_piped_payload_is_not_consumed_for_key(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdin, "read", lambda: pytest.fail("Consumed payload"))
    transport = httpx.MockTransport(lambda _: pytest.fail("Unexpected network request"))
    assert await run(["chat", "--prompt", "-"], transport=transport) == 1
    assert "evren login" in capsys.readouterr().err


def test_agent_first_launch(monkeypatch):
    from evren_agent import cli
    from evren_agent.core.agent import Agent
    agent = Agent(config={"agent": {"default_provider": "llmtr"}})
    monkeypatch.setattr(cli, "Agent", lambda **kwargs: agent)
    monkeypatch.setattr(sys, "argv", ["firik-agent"])
    async def repl(actual):
        assert actual.providers.get_active().api_key == KEY
    monkeypatch.setattr(cli, "run_repl", repl)
    cli.main()
    assert credentials.get_api_key("llmtr", {}) == KEY


@pytest.mark.asyncio
async def test_provider_switch_prompts_before_switching(monkeypatch):
    from evren_agent import cli
    from evren_agent.core.agent import Agent
    agent = Agent(config={"agent": {"default_provider": "evren"}})
    await cli.handle_slash_command(agent, "/provider llmtr")
    assert agent.providers.active_name == "llmtr"
    assert agent.providers.get_active().api_key == KEY
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    await cli.handle_slash_command(agent, "/provider openai")
    assert agent.providers.active_name == "llmtr"
