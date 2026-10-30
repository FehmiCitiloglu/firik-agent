"""Fixed host-owned backend inventory; discovery never consults credentials."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import shutil

from .config import BACKEND_NAMES, resolve_harness_configuration
from .models import HarnessError


def create_backend(name: str, *, config=None, agent=None, chat_session=None,
                   runtime_options=None):
    if name not in BACKEND_NAMES:
        raise HarnessError("backend_unknown", "Unknown harness backend: " + str(name))
    settings = resolve_harness_configuration(config)
    options = settings.backend(name)
    if not options.enabled:
        raise HarnessError("backend_disabled", "Harness backend is disabled: " + name, backend=name)
    if name == "native":
        from .native import NativeHarnessBackend
        return NativeHarnessBackend(agent=agent, chat_session=chat_session, runtime_options=runtime_options)
    from .external import CodexBackend, PiBackend
    cls = PiBackend if name == "pi" else CodexBackend
    kwargs = {"executable": options.executable}
    if options.expected_version is not None:
        kwargs["expected_version"] = options.expected_version
    return cls(**kwargs)


def backend_inventory(config=None):
    """Report installation separately from opt-in configuration and usability."""
    settings = resolve_harness_configuration(config)
    from .native import NativeHarnessBackend
    from .external import CodexBackend, PiBackend
    inventory = []
    for name, cls in (("native", NativeHarnessBackend), ("pi", PiBackend), ("codex", CodexBackend)):
        options = settings.backend(name)
        kwargs = {} if name == "native" else {"executable": options.executable}
        if name != "native" and options.expected_version is not None:
            kwargs["expected_version"] = options.expected_version
        try:
            backend = cls(**kwargs)
            if name != "native" and not options.enabled:
                executable = options.executable or shutil.which(name)
                metadata = {"name": name, "version": None, "available": False,
                    "installed": bool(executable and Path(executable).is_file()),
                    "reason": "Disabled; executable version and compatibility were not probed.",
                    "interface": "", "license": None, "security_notes": ()}
            else:
                metadata = asdict(backend.get_metadata())
            capabilities = {key: asdict(value) for key, value in backend.get_capabilities().items()}
        except Exception as exc:
            metadata = {"name": name, "version": None, "available": False,
                        "reason": str(exc), "interface": "", "license": None, "security_notes": ()}
            capabilities = {}
        supported = metadata["available"]
        metadata.update(enabled=options.enabled, installed_and_supported=supported,
                        available=bool(options.enabled and supported), capabilities=capabilities)
        if not options.enabled:
            metadata["reason"] = "Disabled by host configuration." + (" " + metadata["reason"] if metadata.get("reason") else "")
        inventory.append(metadata)
    return inventory
