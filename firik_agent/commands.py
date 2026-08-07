"""Constrained process execution and recursive project verification."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .contracts import ToolError
from .workspace import DEFAULT_IGNORES, Workspace

DEFAULT_EXECUTABLES = frozenset(
    {
        "black",
        "cargo",
        "cmake",
        "go",
        "gofmt",
        "make",
        "mypy",
        "npm",
        "npx",
        "pnpm",
        "pyright",
        "pytest",
        "python",
        "python3",
        "ruff",
        "rustc",
        "rustfmt",
        "uv",
        "yarn",
    }
)

SHELL_TOKENS = frozenset({";", "&&", "||", "|", ">", ">>", "<", "<<", "&"})
PYTHON_MODULES = frozenset({"build", "compileall", "mypy", "pytest", "ruff", "unittest"})


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


@dataclass(frozen=True, slots=True)
class QualityGate:
    name: str
    argv: tuple[str, ...]
    cwd: str = "."
    timeout_seconds: int = 300
    require_empty_stdout: bool = False


class CommandRunner:
    """Runs bounded, non-shell commands under the workspace root."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        allowed_executables: set[str] | None = None,
        max_output_chars: int = 100_000,
        max_timeout_seconds: int = 900,
        _allow_git: bool = False,
    ) -> None:
        self.workspace = workspace
        self.allowed_executables = DEFAULT_EXECUTABLES | frozenset(allowed_executables or set())
        self.max_output_chars = max_output_chars
        self.max_timeout_seconds = max_timeout_seconds
        self._allow_git = _allow_git

    def run(
        self,
        command: str | Sequence[str],
        *,
        cwd: str = ".",
        timeout_seconds: int = 120,
    ) -> CommandResult:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if argv and argv[0] in {"python", "python3"}:
            argv[0] = sys.executable
        if not 1 <= timeout_seconds <= self.max_timeout_seconds:
            raise ToolError(f"timeout_seconds must be between 1 and {self.max_timeout_seconds}")
        resolved_cwd = self.workspace.resolve(cwd, must_exist=True)
        if not resolved_cwd.is_dir():
            raise ToolError(f"Command cwd is not a directory: {cwd}")
        self._validate(argv, resolved_cwd)

        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", ""),
            "TERM": os.environ.get("TERM", "dumb"),
            "NO_COLOR": "1",
            "CI": "1",
        }
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=resolved_cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
            stdout, stdout_cut = self._truncate(completed.stdout)
            stderr, stderr_cut = self._truncate(completed.stderr)
            return CommandResult(
                argv=argv,
                cwd=self.workspace.relative(resolved_cwd),
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=round(time.monotonic() - started, 3),
                truncated=stdout_cut or stderr_cut,
            )
        except subprocess.TimeoutExpired as exc:
            stdout, stdout_cut = self._truncate(_as_text(exc.stdout))
            stderr, stderr_cut = self._truncate(_as_text(exc.stderr))
            return CommandResult(
                argv=argv,
                cwd=self.workspace.relative(resolved_cwd),
                exit_code=None,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=round(time.monotonic() - started, 3),
                timed_out=True,
                truncated=stdout_cut or stderr_cut,
            )
        except FileNotFoundError as exc:
            raise ToolError(f"Executable is not installed: {argv[0]}") from exc

    def git(self, subcommand: str, *arguments: str) -> CommandResult:
        """Run one of the explicitly read-only Git operations."""
        if subcommand not in {"status", "diff", "log", "show", "rev-parse", "ls-files"}:
            raise ToolError(f"Git subcommand is not read-only: {subcommand}")
        git_runner = CommandRunner(
            self.workspace,
            allowed_executables=set(self.allowed_executables) | {"git"},
            max_output_chars=self.max_output_chars,
            max_timeout_seconds=self.max_timeout_seconds,
            _allow_git=True,
        )
        return git_runner.run(["git", subcommand, *arguments], timeout_seconds=60)

    def _validate(self, argv: list[str], cwd: Path) -> None:
        if not argv:
            raise ToolError("Command must not be empty")
        executable = Path(argv[0]).name
        if executable == "git" and not self._allow_git:
            raise ToolError("Use the dedicated read-only Git tools")
        is_python = executable == "python" or executable.startswith("python3")
        if executable not in self.allowed_executables and not is_python:
            raise ToolError(f"Executable is not allowed: {executable}")
        if any(token in SHELL_TOKENS for token in argv):
            raise ToolError("Shell control operators are not allowed")
        if any("\x00" in token or "\n" in token for token in argv):
            raise ToolError("Command arguments must be single-line text")
        if is_python:
            self._validate_python(argv, cwd)

    def _validate_python(self, argv: list[str], cwd: Path) -> None:
        if len(argv) < 2:
            raise ToolError("Interactive Python is not allowed")
        entry = argv[1]
        if entry == "-m":
            if len(argv) < 3 or argv[2] not in PYTHON_MODULES:
                raise ToolError("Python -m is limited to approved development modules")
            return
        if entry in {"--version", "-V"}:
            return
        if entry.startswith("-"):
            raise ToolError("Python flags and inline code are not allowed")
        script = Path(entry)
        if not script.is_absolute():
            script = cwd / script
        resolved = self.workspace.resolve(script, must_exist=True)
        if not resolved.is_file() or resolved.suffix != ".py":
            raise ToolError("Python may execute only .py files inside the workspace")

    def _truncate(self, text: str) -> tuple[str, bool]:
        if len(text) <= self.max_output_chars:
            return text, False
        marker = "\n...[output truncated]...\n"
        half = (self.max_output_chars - len(marker)) // 2
        return f"{text[:half]}{marker}{text[-half:]}", True


class ProjectVerifier:
    """Discovers quality gates for all supported projects in a workspace."""

    def __init__(self, workspace: Workspace, runner: CommandRunner) -> None:
        self.workspace = workspace
        self.runner = runner

    def discover(self) -> list[QualityGate]:
        configured = self._configured_gates()
        if configured is not None:
            return configured
        gates: list[QualityGate] = []
        seen: set[tuple[str, str]] = set()
        for manifest in self._manifests():
            relative_dir = self.workspace.relative(manifest.parent)
            key = (manifest.name, relative_dir)
            if key in seen:
                continue
            seen.add(key)
            if manifest.name == "pyproject.toml":
                gates.extend(self._python_gates(relative_dir))
            elif manifest.name == "package.json":
                gates.extend(self._node_gates(manifest, relative_dir))
            elif manifest.name == "Cargo.toml":
                gates.extend(self._rust_gates(relative_dir))
            elif manifest.name == "go.mod":
                gates.extend(self._go_gates(relative_dir))
            elif manifest.name in {"Makefile", "makefile"}:
                gates.append(QualityGate("make-test", ("make", "test"), relative_dir))
        return _deduplicate_gates(gates)

    def run_all(self) -> list[dict[str, Any]]:
        gates = self.discover()
        if not gates:
            raise ToolError("No quality gates were discovered; add .firik-agent.toml")
        results: list[dict[str, Any]] = []
        for gate in gates:
            result = self.runner.run(
                gate.argv,
                cwd=gate.cwd,
                timeout_seconds=gate.timeout_seconds,
            )
            payload = result.to_dict()
            payload["name"] = gate.name
            if gate.require_empty_stdout and result.stdout.strip():
                payload["ok"] = False
                payload["error"] = "Expected no stdout"
            results.append(payload)
        return results

    def format_all(self) -> list[dict[str, Any]]:
        commands: list[QualityGate] = []
        for manifest in self._manifests():
            relative_dir = self.workspace.relative(manifest.parent)
            if manifest.name == "pyproject.toml":
                commands.append(
                    QualityGate(
                        "python-format", _python_module("ruff", "format", "."), relative_dir
                    )
                )
            elif manifest.name == "Cargo.toml":
                commands.append(QualityGate("rust-format", ("cargo", "fmt", "--all"), relative_dir))
            elif manifest.name == "go.mod":
                commands.append(QualityGate("go-format", ("go", "fmt", "./..."), relative_dir))
            elif manifest.name == "package.json":
                scripts = _package_scripts(manifest)
                if "format" in scripts:
                    commands.append(
                        QualityGate("node-format", ("npm", "run", "format"), relative_dir)
                    )
        results = []
        for gate in _deduplicate_gates(commands):
            payload = self.runner.run(gate.argv, cwd=gate.cwd, timeout_seconds=300).to_dict()
            payload["name"] = gate.name
            results.append(payload)
        return results

    def _manifests(self) -> list[Path]:
        names = {"pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Makefile", "makefile"}
        manifests: list[Path] = []
        for path in self.workspace.root.rglob("*"):
            relative = path.relative_to(self.workspace.root)
            if any(
                part in DEFAULT_IGNORES or part.endswith(".egg-info") for part in relative.parts
            ):
                continue
            if path.is_file() and path.name in names:
                manifests.append(path)
        return sorted(manifests)

    def _configured_gates(self) -> list[QualityGate] | None:
        config_path = self.workspace.root / ".firik-agent.toml"
        if not config_path.exists():
            return None
        import tomllib

        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        entries = data.get("verification", {}).get("commands", [])
        if not isinstance(entries, list) or not entries:
            raise ToolError(".firik-agent.toml verification.commands must be a non-empty list")
        gates: list[QualityGate] = []
        for index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict) or "command" not in entry:
                raise ToolError(f"Invalid verification command at index {index}")
            command = entry["command"]
            argv = tuple(shlex.split(command) if isinstance(command, str) else command)
            gates.append(
                QualityGate(
                    name=str(entry.get("name", f"configured-{index}")),
                    argv=argv,
                    cwd=str(entry.get("cwd", ".")),
                    timeout_seconds=int(entry.get("timeout_seconds", 300)),
                )
            )
        return gates

    @staticmethod
    def _python_gates(cwd: str) -> list[QualityGate]:
        return [
            QualityGate(
                "python-format-check", _python_module("ruff", "format", "--check", "."), cwd
            ),
            QualityGate("python-lint", _python_module("ruff", "check", "."), cwd),
            QualityGate("python-types", _python_module("mypy", "firik_agent"), cwd),
            QualityGate("python-tests", _python_module("pytest", "-q"), cwd),
            QualityGate("python-build", _python_module("build"), cwd),
        ]

    @staticmethod
    def _node_gates(manifest: Path, cwd: str) -> list[QualityGate]:
        scripts = _package_scripts(manifest)
        gates = []
        for name in ("format:check", "lint", "typecheck", "test", "build"):
            if name in scripts:
                gates.append(QualityGate(f"node-{name}", ("npm", "run", name), cwd))
        return gates

    @staticmethod
    def _rust_gates(cwd: str) -> list[QualityGate]:
        return [
            QualityGate("rust-format", ("cargo", "fmt", "--all", "--check"), cwd),
            QualityGate(
                "rust-clippy",
                ("cargo", "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"),
                cwd,
            ),
            QualityGate("rust-tests", ("cargo", "test", "--all-features"), cwd),
        ]

    @staticmethod
    def _go_gates(cwd: str) -> list[QualityGate]:
        return [
            QualityGate("go-vet", ("go", "vet", "./..."), cwd),
            QualityGate("go-tests", ("go", "test", "./..."), cwd),
        ]


def _python_module(*arguments: str) -> tuple[str, ...]:
    return (sys.executable, "-m", *arguments)


def _package_scripts(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"Invalid package.json: {path}") from exc
    scripts = data.get("scripts", {})
    return scripts if isinstance(scripts, dict) else {}


def _deduplicate_gates(gates: list[QualityGate]) -> list[QualityGate]:
    unique: dict[tuple[str, tuple[str, ...]], QualityGate] = {}
    for gate in gates:
        unique[(gate.cwd, gate.argv)] = gate
    return list(unique.values())


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
