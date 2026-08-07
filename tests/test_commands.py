from __future__ import annotations

import sys
from pathlib import Path

import pytest

from firik_agent.commands import CommandRunner, ProjectVerifier
from firik_agent.contracts import ToolError
from firik_agent.workspace import Workspace


def test_command_runner_runs_without_shell_and_captures_evidence(tmp_path: Path) -> None:
    runner = CommandRunner(Workspace(tmp_path))
    (tmp_path / "evidence.py").write_text("print('evidence')\n", encoding="utf-8")

    result = runner.run([sys.executable, "evidence.py"])

    assert result.ok is True
    assert result.stdout == "evidence\n"
    assert result.cwd == "."


@pytest.mark.parametrize(
    "command, message",
    [
        ("rm -rf target", "not allowed"),
        ("python -c pass && echo unsafe", "control operators"),
        ("python -c print(1)", "inline code"),
        ("git reset --hard", "dedicated read-only Git"),
    ],
)
def test_command_runner_rejects_unsafe_commands(tmp_path: Path, command: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        CommandRunner(Workspace(tmp_path)).run(command)


def test_read_only_git_status(tmp_path: Path) -> None:
    runner = CommandRunner(Workspace(tmp_path))
    result = runner.git("status", "--short", "--branch")

    assert result.exit_code != 0
    with pytest.raises(ToolError, match="not read-only"):
        runner.git("reset", "--hard")


def test_configured_verifier_runs_every_gate_even_after_failure(tmp_path: Path) -> None:
    (tmp_path / "fail.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    (tmp_path / "pass.py").write_text("print('ran')\n", encoding="utf-8")
    config = f"""
[verification]
commands = [
  {{ name = "fails", command = ["{sys.executable}", "fail.py"] }},
  {{ name = "passes", command = ["{sys.executable}", "pass.py"] }},
]
"""
    (tmp_path / ".firik-agent.toml").write_text(config, encoding="utf-8")
    verifier = ProjectVerifier(Workspace(tmp_path), CommandRunner(Workspace(tmp_path)))

    results = verifier.run_all()

    assert [result["name"] for result in results] == ["fails", "passes"]
    assert [result["ok"] for result in results] == [False, True]


def test_verifier_recursively_discovers_language_projects(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n", encoding="utf-8")
    rust = tmp_path / "crates" / "demo"
    rust.mkdir(parents=True)
    (rust / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")

    names = [
        gate.name
        for gate in ProjectVerifier(
            Workspace(tmp_path), CommandRunner(Workspace(tmp_path))
        ).discover()
    ]

    assert "python-tests" in names
    assert "python-build" in names
    assert "rust-clippy" in names
    assert "rust-tests" in names
