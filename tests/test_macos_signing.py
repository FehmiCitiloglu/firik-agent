"""Credential validation and runner isolation; no real Apple credentials used."""
import base64
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import setup_macos_signing as setup
from scripts import sign_macos as signing


def test_ticket_service_recovers_after_extended_network_outage(monkeypatch):
    calls, delays = [], []
    def run(*args):
        calls.append(args)
        if len(calls) <= 5:
            raise subprocess.CalledProcessError(68, args)
    monkeypatch.setattr(signing, "run", run)
    monkeypatch.setattr(signing.time, "sleep", delays.append)
    signing.stapler("validate", Path("test.dmg"))
    assert len(calls) == 6 and delays == [10, 20, 40, 60, 60]


@pytest.mark.parametrize("code,attempts", [(68, 7), (65, 1)])
def test_ticket_validation_remains_mandatory_and_bounded(monkeypatch, code, attempts):
    calls, delays = [], []
    def run(*args):
        calls.append(args)
        raise subprocess.CalledProcessError(code, args)
    monkeypatch.setattr(signing, "run", run)
    monkeypatch.setattr(signing.time, "sleep", delays.append)
    with pytest.raises(subprocess.CalledProcessError):
        signing.stapler("validate", Path("test.dmg"))
    assert len(calls) == attempts and len(delays) == attempts - 1


def configure(monkeypatch, tmp_path):
    values = {
        "MACOS_CERTIFICATE_BASE64": base64.b64encode(b"fake-p12").decode(),
        "MACOS_CERTIFICATE_PASSWORD": "fake-export-password",
        "APPLE_ID": "test@example.invalid",
        "APPLE_TEAM_ID": "TESTTEAM00",
        "APPLE_APP_SPECIFIC_PASSWORD": "fake-notary-password",
        "GITHUB_ACTIONS": "true", "RUNNER_TEMP": str(tmp_path),
        "GITHUB_ENV": str(tmp_path / "github-env"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(setup, "sys", SimpleNamespace(platform="darwin"))
    return values


def test_missing_secret_stops_before_keychain_changes(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    monkeypatch.delenv("APPLE_APP_SPECIFIC_PASSWORD")
    monkeypatch.setattr(setup, "run", lambda *args: pytest.fail("Must validate secrets first"))
    with pytest.raises(SystemExit, match="APPLE_APP_SPECIFIC_PASSWORD"):
        setup.main()
    assert not list(tmp_path.iterdir())


def test_setup_preserves_search_list_and_isolates_notary_credentials(monkeypatch, tmp_path, capsys):
    values = configure(monkeypatch, tmp_path)
    calls = []
    fingerprint = "A" * 40
    keychain = tmp_path / "firik-signing.keychain-db"

    def run(*args):
        calls.append(args)
        if args[:3] == ("security", "list-keychains", "-d") and "-s" not in args:
            return '    "/old/login.keychain-db"\n'
        if args[:2] == ("security", "create-keychain"):
            keychain.touch()
        if args[:2] == ("security", "import"):
            certificate = Path(args[2])
            assert certificate.read_bytes() == b"fake-p12"
            if os.name == "posix":
                assert certificate.stat().st_mode & 0o777 == 0o600
        if args[:2] == ("security", "find-identity"):
            return f'1) {fingerprint} "Developer ID Application: Test (TESTTEAM00)"\n'
        return ""

    monkeypatch.setattr(setup, "run", run)
    setup.main()
    assert not (tmp_path / "firik-signing.p12").exists()
    assert ("security", "list-keychains", "-d", "user", "-s", keychain, "/old/login.keychain-db") in calls
    notary_call = next(call for call in calls if call[:2] == ("xcrun", "notarytool"))
    assert notary_call[-2:] == ("--keychain", keychain)
    environment = (tmp_path / "github-env").read_text()
    assert f"FIRIK_CODESIGN_IDENTITY={fingerprint}\n" in environment
    assert f"FIRIK_NOTARY_KEYCHAIN={keychain}\n" in environment
    output = capsys.readouterr().out
    for name in ("MACOS_CERTIFICATE_PASSWORD", "APPLE_APP_SPECIFIC_PASSWORD"):
        assert values[name] not in environment + output


def test_failed_command_does_not_disclose_password(monkeypatch):
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="fake-secret-password", stderr="fake-secret-password",
    ))
    with pytest.raises(RuntimeError) as error:
        setup.run("security", "import", "fake.p12", "-P", "fake-secret-password")
    assert "fake-secret-password" not in str(error.value)


def test_temporary_notary_keychain_is_used_for_all_requests(monkeypatch):
    monkeypatch.setenv("FIRIK_NOTARY_KEYCHAIN", "/tmp/test-signing.keychain-db")
    assert signing.notary_credentials("test-profile") == [
        "--keychain-profile", "test-profile", "--keychain", "/tmp/test-signing.keychain-db",
    ]
    monkeypatch.delenv("FIRIK_NOTARY_KEYCHAIN")
    assert signing.notary_credentials("test-profile") == ["--keychain-profile", "test-profile"]
