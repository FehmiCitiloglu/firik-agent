"""Bounded CLI checks. Offline inspection never initializes an agent or tools."""
from __future__ import annotations

import argparse
import asyncio
from copy import copy
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import yaml

from firik_agent.core.compaction import ContextCompactor
from firik_agent.core.failures import format_failure, provider_failure
from firik_agent.core.recovery import RecoveryLimits
from firik_agent.projects.harness_profiles import input_limit, output_limit, resolve_harness_profile
from firik_agent.projects.model_capabilities import model_capabilities


DEFAULT_PROVIDERS = {
    "firik": ("https://evren-llmapi.ssyz.org.tr/v1", "glm-5.3"),
    "llmtr": ("https://llmtr.com/v1", "evren/glm-5.3-fp8"),
    "openai": ("https://api.openai.com/v1", "gpt-4o"),
    "ollama": ("http://127.0.0.1:11434/v1", ""),
    "lmstudio": ("http://127.0.0.1:1234/v1", ""),
}


class DiagnosticConfigurationError(ValueError):
    """Only fixed, source-free validation messages may use this type."""


def _mapping(value, field):
    if not isinstance(value, dict):
        raise DiagnosticConfigurationError(f"{field} must be a mapping")
    return value


def _label(value, field):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,199}", value)
            or re.search(r"(?i)(?:sk[-_]|bearer|password|secret|api.?key)", value)):
        raise DiagnosticConfigurationError(f"Invalid {field}")
    return value


def load_doctor_config(config_path=None):
    """Read YAML only: no dotenv, credentials, plugin loading or writes."""
    from firik_agent.config import _resolve_default_config_path
    path = Path(config_path) if config_path else _resolve_default_config_path()
    if not path.exists():
        if config_path:
            raise DiagnosticConfigurationError("Configuration file does not exist")
        return {}
    if path.stat().st_size > 1024 * 1024:
        raise DiagnosticConfigurationError("Configuration exceeds 1 MiB")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError):
        # YAML parser messages can contain source lines and credentials.
        raise DiagnosticConfigurationError("Configuration is not valid YAML") from None
    return _mapping({} if value is None else value, "configuration")


def _provider(config, selected=None, model=None):
    agent = _mapping(config.get("agent", {}), "agent")
    providers = _mapping(config.get("providers", {}), "providers")
    name = _label(selected or agent.get("default_provider", "firik"), "provider")
    settings = _mapping(providers.get(name, {}), "selected provider")
    if name not in DEFAULT_PROVIDERS and settings.get("type") != "openai_compatible":
        raise DiagnosticConfigurationError("Selected provider is not configured")
    default_url, default_model = DEFAULT_PROVIDERS.get(name, ("", ""))
    endpoint = settings.get("base_url", default_url)
    if not isinstance(endpoint, str):
        raise DiagnosticConfigurationError("Provider endpoint must be a URL")
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise DiagnosticConfigurationError("Provider endpoint must be HTTP(S), without credentials, query or fragment")
    parsed.port  # Validate a malformed/out-of-range port before any probe.
    chosen_model = _label(model or settings.get("default_model", default_model), "model")
    if name in {"firik", "llmtr"}:
        from firik_agent.providers.firik import FirikProvider
        provider = FirikProvider(name=name, base_url=endpoint, default_model=chosen_model,
                                 is_gateway=name == "llmtr")
    else:
        from firik_agent.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider(name=name, base_url=endpoint, default_model=chosen_model)
    evidence = _mapping(settings.get("model_capabilities", {}), "model_capabilities").get(chosen_model)
    if evidence is not None:
        provider.model_metadata[chosen_model] = {"id": chosen_model, **model_capabilities(_mapping(evidence, "model capability"))}
    return provider, settings


def inspect_configuration(config, provider, *, profile=None, last_failure=None):
    config = _mapping(config, "configuration")
    agent = _mapping(config.get("agent", {}), "agent")
    limits = RecoveryLimits(**_mapping(agent.get("recovery", {}), "recovery"))
    compaction_config = _mapping(agent.get("context_compaction", {}), "context_compaction")
    if "enabled" in compaction_config and not isinstance(compaction_config["enabled"], bool):
        raise DiagnosticConfigurationError("context_compaction.enabled must be a boolean")
    for field in ("trigger_ratio", "target_ratio", "fallback_input_tokens", "summary_max_tokens", "timeout_seconds", "summary_tool_output_chars"):
        if field in compaction_config:
            value = compaction_config[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise DiagnosticConfigurationError("context_compaction numeric settings must be finite and positive")
    compactor = ContextCompactor(compaction_config)
    temperature = agent.get("temperature", .7)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature):
        raise DiagnosticConfigurationError("Temperature must be a finite number")
    iterations = agent.get("max_iterations", 15)
    if iterations not in (None, "unlimited") and (isinstance(iterations, bool) or int(iterations) < 1):
        raise DiagnosticConfigurationError("max_iterations must be positive or unlimited")
    if profile is None and ("harness_profile" in agent or "harness_overrides" in agent):
        profile = resolve_harness_profile(user_config=agent)
    if profile is None and "harness" in config:
        from firik_agent.harness.config import resolve_harness_configuration
        settings = resolve_harness_configuration(config)
        profile = resolve_harness_profile(session_override=settings.profile, user_config=agent)
    from firik_agent.core.execution import ExecutionConfig
    execution = ExecutionConfig(**_mapping(config.get("execution", {}), "execution"))
    caps = model_capabilities(provider.model_metadata.get(provider.current_model))
    output = output_limit(2048, profile, caps)
    window = caps.get("context_window")
    budget = compactor.input_budget(window, output)
    budget = input_limit(budget, profile, window)
    warnings = []
    if not window:
        warnings.append("Model context capacity is unknown. Use doctor --probe to inspect the selected model catalogue before a long prompt.")
    ready = budget > 0 and limits.max_elapsed > 0 and limits.max_tokens != 0 and limits.max_cost != 0
    if budget < 1:
        warnings.append("No input budget remains after output reservation. Check the model capacity and output limit.")
    if limits.max_elapsed == 0 or limits.max_tokens == 0 or limits.max_cost == 0:
        warnings.append("The configured resource budget cannot admit a model request. Check agent.recovery limits.")
    report = {"schema_version": 1, "ready": ready, "mode": "offline", "provider": _label(provider.name, "provider"),
              "model": _label(provider.current_model, "model"), "local_endpoint": provider.is_local,
              "credentials": "not_checked", "probe": {"status": "not_requested"},
              "context": {"context_window": window, "input_budget": budget, "output_reservation": output,
                          "verification_status": caps.get("verification_status"), "compaction_enabled": compactor.enabled,
                          "budget_basis": "Before system prompt and tool-schema overhead; unknown capacity adds that overhead at runtime."},
              "resources": asdict(limits), "profile": (profile or {}).get("name"),
              "execution_backend": execution.backend, "warnings": warnings, "last_failure": last_failure,
              "completion_requests": 0}
    return report


async def probe_provider(report, provider, *, config=None, profile=None):
    """Exactly one catalogue GET; never submit a prompt or follow redirects."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(provider.base_url.rstrip("/") + "/models", headers=provider._get_headers())
        if response.status_code != 200:
            raise provider_failure(response, provider=provider.name)
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Invalid model catalogue")
        selected = [row for row in rows if isinstance(row, dict) and row.get("id") == provider.current_model]
        # A diagnostic probe is passive: compute with fresh evidence on a copy
        # without changing the active session's capability/permission state.
        inspected = copy(provider)
        inspected.model_metadata = dict(provider.model_metadata)
        previous = model_capabilities(provider.model_metadata.get(provider.current_model))
        inspected.model_metadata.pop(provider.current_model, None)
        if len(selected) == 1:
            inspected.model_metadata[provider.current_model] = {**selected[0], "source": "provider"}
            if previous.get("verified") is False:
                inspected.model_metadata[provider.current_model]["verified"] = False
        refreshed = inspect_configuration(config or {}, inspected, profile=profile, last_failure=report.get("last_failure"))
        for key in ("ready", "context", "warnings"):
            report[key] = refreshed[key]
        report["mode"] = "probe"
        report["probe"] = {"status": "online", "http_status": 200, "model_available": len(selected) == 1}
        if len(selected) != 1:
            report["ready"] = False
            report["warnings"].append("The selected model is missing or ambiguous in the provider catalogue. Check /model.")
        else:
            caps = model_capabilities(inspected.model_metadata[provider.current_model])
            report["probe"]["context_window"] = caps.get("context_window")
    except Exception as error:
        report["ready"] = False
        report["mode"] = "probe"
        report["probe"] = {"status": "failed", "error": format_failure(error)}
    return report


def render_report(report, *, as_json=False):
    if as_json:
        return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    context = report.get("context", {})
    lines = ["Firik Agent doctor: " + ("PASS" if report["ready"] else "FAIL")]
    if "provider" in report:
        lines += [f"Provider/model: {report['provider']} / {report['model']}",
                  f"Context: {context.get('context_window') or 'unknown'}; input budget: {context.get('input_budget')}; output reservation: {context.get('output_reservation')}",
                  f"Resources: {report['resources']['max_requests']} requests; {report['resources']['max_elapsed']} seconds; token cap: {report['resources']['max_tokens']}",
                  f"Probe: {report['probe']['status']}; completion requests: 0"]
    if report.get("error"):
        lines.append(report["error"])
    if report.get("probe", {}).get("error"):
        lines.append(report["probe"]["error"])
    lines.extend("Warning: " + value for value in report.get("warnings", []))
    if report.get("last_failure"):
        lines.append("Last failure: " + report["last_failure"])
    return "\n".join(lines)


async def inspect_agent(agent, *, probe=False):
    provider = agent.providers.get_active()
    report = inspect_configuration(agent.config, provider, profile=getattr(agent, "harness_profile", None),
                                   last_failure=getattr(agent, "_cli_last_failure", None))
    if probe:
        await probe_provider(report, provider, config=agent.config, profile=getattr(agent, "harness_profile", None))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="firik-agent doctor", description="Check CLI configuration offline; --probe performs one GET /models.")
    parser.add_argument("--config")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--probe", action="store_true", help="Read the selected endpoint's model catalogue; no completion request.")
    args = parser.parse_args(argv)
    try:
        config = load_doctor_config(args.config)
        provider, settings = _provider(config, args.provider, args.model)
        report = inspect_configuration(config, provider)
        if args.probe:
            from firik_agent.config import load_dotenv
            load_dotenv()
            from firik_agent.credentials import DEFAULTS, get_api_key
            if provider.name in DEFAULTS:
                provider.api_key = get_api_key(provider.name, settings, base_url=provider.base_url)
            else:
                environment = settings.get("api_key_env")
                if environment is not None and (not isinstance(environment, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", environment)):
                    raise ValueError("Invalid credential environment name")
                provider.api_key = os.environ.get(environment, "") if environment else ""
            report["credentials"] = "present" if provider.api_key else "missing_or_not_required"
            asyncio.run(probe_provider(report, provider, config=config))
    except Exception as error:
        detail = (str(error) + ". ") if isinstance(error, DiagnosticConfigurationError) else ""
        report = {"schema_version": 1, "ready": False, "mode": "offline", "error": detail + format_failure(error), "completion_requests": 0}
    print(render_report(report, as_json=args.json))
    return 0 if report["ready"] else 2
