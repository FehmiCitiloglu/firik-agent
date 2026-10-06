import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location("installer", Path(__file__).parents[1] / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "user's home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return path


def make_commands(venv, windows=False):
    scripts = venv / ("Scripts" if windows else "bin")
    scripts.mkdir(parents=True)
    for name in installer.COMMANDS:
        path = scripts / (name + (".exe" if windows else ""))
        path.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        path.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="Executes POSIX /bin/sh profiles")
@pytest.mark.parametrize("shell, profiles", [
    ("zsh", (".zshrc", ".zprofile")),
    ("bash", (".bashrc", ".profile")),
    ("sh", (".profile",)),
])
def test_commands_without_activation_and_idempotent_path(home, monkeypatch, shell, profiles):
    monkeypatch.setenv("SHELL", f"/bin/{shell}")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    venv = home / "project" / ".venv"
    bin_dir = home / ".local" / "bin"
    make_commands(venv)
    profile = home / profiles[0]
    profile.write_text("# existing settings\n")
    for _ in range(2):
        installer.install_commands(venv, bin_dir, False)
        installer.configure_path(bin_dir, False)
    for name in profiles:
        assert (home / name).read_text().count("# EVREN CLI") == 1
    assert profile.read_text().startswith("# existing settings\n")
    result = subprocess.run(
        ["/bin/sh", "-c", f'. {shlex.quote(str(profile))}; . {shlex.quote(str(profile))}; '
         'evren "argument with spaces"; firik-agent --commands; printf "%s" "$PATH"'],
        cwd=home, env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        text=True, capture_output=True, check=True,
    )
    assert result.stdout.startswith("argument with spaces\n--commands\n")
    assert result.stdout.count(str(bin_dir)) == 1


def test_unrelated_command_is_preserved(tmp_path):
    venv, bin_dir = tmp_path / ".venv", tmp_path / "bin"
    make_commands(venv)
    bin_dir.mkdir()
    existing = bin_dir / "firik-agent"
    existing.write_text("user command")
    with pytest.raises(RuntimeError, match="Cannot replace"):
        installer.install_commands(venv, bin_dir, False)
    assert existing.read_text() == "user command"
    assert not (bin_dir / "evren").exists()


def test_custom_shell_configuration_paths(home, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("ZDOTDIR", str(home / "custom-zsh"))
    installer.configure_path(home / ".local/bin", False)
    assert (home / "custom-zsh/.zshrc").is_file()
    assert not (home / ".zshrc").exists()
    monkeypatch.setenv("SHELL", "/bin/bash")
    (home / ".bash_profile").write_text("# login settings\n")
    installer.configure_path(home / ".local/bin", False)
    assert "# login settings" in (home / ".bash_profile").read_text()
    assert "# EVREN CLI" in (home / ".bash_profile").read_text()
    assert not (home / ".profile").exists()


def test_fish_path_configuration(home, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    for _ in range(2):
        refresh = installer.configure_path(home / ".local/bin", False)
    contents = (home / "config/fish/conf.d/evren.fish").read_text()
    assert contents.count("# EVREN CLI") == 1
    assert refresh[0] in contents
    assert "export PATH" not in contents


def test_windows_launchers_are_copied(tmp_path):
    venv, bin_dir = tmp_path / ".venv", tmp_path / "EVREN/bin"
    make_commands(venv, windows=True)
    for _ in range(2):
        installer.install_commands(venv, bin_dir, True)
    for command in installer.COMMANDS:
        assert (bin_dir / f"{command}.exe").read_bytes() == (venv / "Scripts" / f"{command}.exe").read_bytes()


def test_uv_explicitly_targets_project_venv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(installer, "__file__", str(tmp_path / "install.py"))
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/tools/uv")
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(installer, "install_commands", lambda *args: None)
    monkeypatch.setattr(installer, "configure_path", lambda *args: [])
    monkeypatch.setenv("VIRTUAL_ENV", "/unrelated/environment")
    (tmp_path / ".env.example").write_text("EVREN_API_KEY=placeholder\n")
    installer.main()
    assert not (tmp_path / ".env").exists()
    python = tmp_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    assert calls[0] == ["/tools/uv", "venv", "--python", sys.executable, str(tmp_path / ".venv")]
    assert calls[1] == ["/tools/uv", "pip", "install", "--python", str(python), "-e", ".[dev]"]
    (tmp_path / ".env").write_text("existing configuration\n")
    installer.main()
    assert (tmp_path / ".env").read_text() == "existing configuration\n"
