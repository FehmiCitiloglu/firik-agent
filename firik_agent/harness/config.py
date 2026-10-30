"""Host-owned harness selection, independent of model and execution providers.

Old configuration has no ``harness`` section and continues to select native.
Resolving settings never probes a process, reads credentials, or starts a run.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from firik_agent.projects.harness_profiles import PROFILES

BACKEND_NAMES = ("native", "pi", "codex")


@dataclass(frozen=True)
class BackendSettings:
    enabled: bool = False
    executable: str | None = None
    expected_version: str | None = None


@dataclass(frozen=True)
class HarnessSettings:
    default_backend: str = "native"
    profile: str = "coding"
    native: BackendSettings = BackendSettings(enabled=True)
    pi: BackendSettings = BackendSettings()
    codex: BackendSettings = BackendSettings()
    benchmark_based_suggestions: bool = True
    automatic_backend_switching: bool = False

    @property
    def backends(self) -> dict[str, BackendSettings]:
        return {name: self.backend(name) for name in BACKEND_NAMES}

    def backend(self, name: str) -> BackendSettings:
        if name not in BACKEND_NAMES:
            raise ValueError("Unknown harness backend: " + str(name))
        return getattr(self, name)

    def to_dict(self) -> dict[str, Any]:
        return {"default_backend": self.default_backend, "profile": self.profile,
                "backends": {name: asdict(self.backend(name)) for name in BACKEND_NAMES},
                "optimization": {"benchmark_based_suggestions": self.benchmark_based_suggestions,
                                 "automatic_backend_switching": self.automatic_backend_switching}}


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(path + " must be a mapping.")
    return dict(value)


def _keys(value: Mapping, allowed: set[str], path: str) -> None:
    if set(value) - allowed:
        raise ValueError(path + " contains unsupported fields: " + ", ".join(sorted(map(str, set(value) - allowed))))


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ValueError(path + " must be true or false.")
    return value


def _merge(base: dict, override: Mapping) -> dict:
    result = dict(base)
    for key, value in override.items():
        result[key] = _merge(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, Mapping) else value
    return result


def resolve_harness_configuration(user_config: Mapping[str, Any] | None = None,
                                  project_preferences: Mapping[str, Any] | None = None) -> HarnessSettings:
    """Resolve root config then project preferences; fail on unsafe typos.

    Both inputs are existing full configuration mappings. Only their ``harness``
    sections are consumed. Existing ``harness_profile`` continues to work; the
    project profile takes precedence over the root profile.
    """
    config = _mapping(user_config or {}, "configuration")
    prefs = _mapping(project_preferences or {}, "project preferences")
    root = _mapping(config.get("harness", {}), "harness")
    local = _mapping(prefs.get("harness", {}), "project harness")
    data = _merge(root, local)
    _keys(data, {"default_backend", "profile", "backends", "optimization"}, "harness")
    selected = data.get("default_backend", "native")
    if selected not in BACKEND_NAMES:
        raise ValueError("Unknown harness backend: " + str(selected))
    agent = _mapping(config.get("agent", {}), "agent")
    profile = prefs.get("harness_profile", data.get("profile", agent.get("harness_profile", "coding")))
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError("Unknown harness profile: " + str(profile))
    configured = _mapping(data.get("backends", {}), "harness.backends")
    _keys(configured, set(BACKEND_NAMES), "harness.backends")
    backends = {}
    for name in BACKEND_NAMES:
        options = _mapping(configured.get(name, {}), "harness.backends." + name)
        _keys(options, {"enabled", "executable", "expected_version"}, "harness.backends." + name)
        enabled = _boolean(options.get("enabled", name == "native"), name + ".enabled")
        for key in ("executable", "expected_version"):
            value = options.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 4096 or any(char in value for char in "\x00\n\r")):
                raise ValueError(name + "." + key + " must be a bounded non-empty string.")
        backends[name] = BackendSettings(enabled=enabled, executable=options.get("executable"),
                                         expected_version=options.get("expected_version"))
    if not backends[selected].enabled:
        raise ValueError("Selected harness backend is disabled: " + selected)
    optimization = _mapping(data.get("optimization", {}), "harness.optimization")
    _keys(optimization, {"benchmark_based_suggestions", "automatic_backend_switching"}, "harness.optimization")
    suggestions = _boolean(optimization.get("benchmark_based_suggestions", True), "benchmark_based_suggestions")
    automatic = _boolean(optimization.get("automatic_backend_switching", False), "automatic_backend_switching")
    if automatic:
        raise ValueError("Automatic backend switching is unavailable; select a backend explicitly before a new task.")
    return HarnessSettings(default_backend=selected, profile=profile, **backends,
                           benchmark_based_suggestions=suggestions, automatic_backend_switching=False)


def select_backend(settings: HarnessSettings, name: str, *, active_backend: str | None = None) -> str:
    """Explicit selection cannot transfer an active execution's state."""
    options = settings.backend(name)
    if not options.enabled:
        raise ValueError("Harness backend is disabled: " + name)
    if active_backend is not None and active_backend != name:
        raise ValueError("An active task cannot switch harness backend without a verified state-transfer implementation.")
    return name
