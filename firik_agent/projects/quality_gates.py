"""Manifest-backed quality commands; discovery never installs or executes tools."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from firik_agent.projects.indexer import read_project_text, safe_project_path


@dataclass(frozen=True)
class QualityGateSpec:
    id: str
    kind: str
    command: str
    source: str = "configured"
    required: bool = True
    enabled: bool = True
    timeout: float = 120

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_gates(values: Iterable[Mapping[str, Any] | QualityGateSpec]) -> list[QualityGateSpec]:
    gates = []
    for value in values:
        gate = value if isinstance(value, QualityGateSpec) else QualityGateSpec(**dict(value))
        if (not gate.id or gate.kind not in {"tests", "lint", "typecheck", "build", "security"}
                or not isinstance(gate.command, str) or not gate.command.strip()
                or len(gate.command) > 8000 or not isinstance(gate.required, bool)
                or not isinstance(gate.enabled, bool) or isinstance(gate.timeout, bool)
                or not isinstance(gate.timeout, (int, float)) or not 0 < gate.timeout <= 300):
            raise ValueError("Kalite kapısı türü, komutu veya zaman aşımı geçersiz.")
        gates.append(gate)
    if len(gates) > 20 or len({gate.id for gate in gates}) != len(gates):
        raise ValueError("Kalite kapıları en fazla 20 benzersiz kimlik içermelidir.")
    return gates


def discover_quality_gates(root: str | Path, *, excluded_paths: Iterable[str] = ()) -> list[QualityGateSpec]:
    """Use commands declared by the checkout or conventional manifest tools.

    Commands remain subject to the runtime's approval boundary. A manifest is
    untrusted project data and cannot grant permission to run a script.
    """
    root = Path(root).resolve()
    gates: dict[str, QualityGateSpec] = {}
    excluded = []
    for raw in excluded_paths:
        path = Path(raw)
        path = path.resolve() if path.is_absolute() else (root / path).resolve()
        if path.is_relative_to(root):
            excluded.append(path)

    def allowed(name: str) -> bool:
        return not any((root / name).is_relative_to(path) for path in excluded)

    def text(name: str) -> str:
        if not allowed(name):
            return ""
        try:
            return read_project_text(root, name, max_bytes=64 * 1024, sanitize=False)
        except (OSError, ValueError):
            return ""

    def exists(name: str) -> bool:
        if not allowed(name):
            return False
        try:
            return safe_project_path(root, name).exists()
        except (OSError, ValueError):
            return False

    def add(kind: str, command: str, source: str) -> None:
        identifier = f"{source.split('.', 1)[0]}_{kind}"
        gates.setdefault(kind, QualityGateSpec(identifier, kind, command, source))

    package = text("package.json")
    if package:
        try:
            scripts = json.loads(package).get("scripts", {})
        except (ValueError, TypeError, AttributeError):
            scripts = {}
        manager = "pnpm" if exists("pnpm-lock.yaml") else "yarn" if exists("yarn.lock") else "npm"
        if isinstance(scripts, dict):
            for kind, names in {"tests": ("test", "test:ci"), "lint": ("lint",),
                                "typecheck": ("typecheck", "type-check", "check:types"), "build": ("build",)}.items():
                name = next((name for name in names if isinstance(scripts.get(name), str) and scripts[name].strip()), None)
                if name:
                    add(kind, f"{manager} run {name}", "package.json")

    pyproject = text("pyproject.toml")
    if pyproject or exists("pytest.ini") or exists("setup.cfg"):
        if ("pytest" in pyproject or exists("pytest.ini") or exists("tests")):
            add("tests", "python -m pytest", "pyproject.toml" if pyproject else "pytest.ini")
        if re.search(r"(?:\[tool\.ruff(?:\.|\])|[\"']ruff(?:[<=>\[])?)", pyproject):
            add("lint", "python -m ruff check .", "pyproject.toml")
        if re.search(r"(?:\[tool\.mypy\]|[\"']mypy(?:[<=>\[])?)", pyproject):
            add("typecheck", "python -m mypy .", "pyproject.toml")
        if "[build-system]" in pyproject:
            add("build", "python -m build", "pyproject.toml")
    if exists("Cargo.toml"):
        add("tests", "cargo test", "Cargo.toml")
        add("lint", "cargo clippy --all-targets -- -D warnings", "Cargo.toml")
        add("build", "cargo build", "Cargo.toml")
    if exists("go.mod"):
        add("tests", "go test ./...", "go.mod")
        add("lint", "go vet ./...", "go.mod")
        add("build", "go build ./...", "go.mod")
    makefile = text("Makefile")
    for kind, target in (("tests", "test"), ("lint", "lint"), ("typecheck", "typecheck"), ("build", "build")):
        if re.search(rf"(?m)^{target}\s*:", makefile):
            add(kind, f"make {target}", "Makefile")
    return list(gates.values())
