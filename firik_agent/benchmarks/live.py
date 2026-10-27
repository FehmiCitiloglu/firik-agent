"""Explicitly authorized model evaluation through the existing native runtime.

Only model traffic is enabled. Repository shell, MCP, plugins and networking
remain denied; the host evaluates source bytes without importing fixture code.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import platform
import sqlite3
from typing import Any

from firik_agent import __version__
from firik_agent.benchmarks.coding import coding_suite, task_manifest
from firik_agent.benchmarks.models import BenchmarkSuite, HarnessConfiguration, fingerprint
from firik_agent.core.metrics import RunMetrics
from firik_agent.projects.coding_sessions import redact_sensitive
from firik_agent.providers.base import BaseProvider

LIVE_LABEL = "Opt-in live model evaluation; host-controlled source/AST verification; no repository code execution."


class LiveBudgetExceeded(RuntimeError):
    """A configured request or provider-reported token allowance is exhausted."""


class BudgetedProvider(BaseProvider):
    """Use native cumulative usage accounting; never retain request payloads.

    Request count and requested output length are hard bounds. Token limits use
    provider evidence and can stop only after a reported frame arrives; unknown
    input usage cannot establish a hard total-token or monetary spending cap.
    """

    def __init__(self, provider: BaseProvider, configuration: HarnessConfiguration):
        super().__init__(provider.name, provider.base_url, provider.api_key, provider.current_model)
        self.inner = provider
        self.configuration = configuration
        self.model_metadata = provider.model_metadata
        self.reasoning_effort = provider.reasoning_effort
        self.recorder = RunMetrics()
        self.index = 0
        self.fixture_cancelled = False

    @property
    def tokens(self) -> int:
        # Different requests may report different subsets of usage. Taking a
        # maximum after aggregation would lose known tokens across requests.
        return self.recorder.snapshot()["reported_token_lower_bound"]

    @property
    def unknown_usage_requests(self) -> int:
        snapshot = self.recorder.snapshot()
        return snapshot["request_count"] - snapshot["coverage"]["total_tokens"]

    def set_model(self, model_name):
        super().set_model(model_name)
        self.inner.set_model(model_name)

    async def chat_stream(self, messages, **kwargs):
        if self.index >= self.configuration.request_budget:
            raise LiveBudgetExceeded("Model request budget exhausted.")
        remaining = self.configuration.token_budget - self.tokens
        if remaining <= 0:
            raise LiveBudgetExceeded("Provider-reported token budget exhausted.")
        self.index += 1
        identity = f"budget_{self.index}"
        self.recorder.observe({"type": "model_request_started", "metadata": {"request_id": identity}})
        maximum = kwargs.get("max_tokens") or self.configuration.max_output_tokens
        kwargs["max_tokens"] = min(maximum, self.configuration.max_output_tokens, remaining)
        status = "failed"
        stream = self.inner.chat_stream(messages, **kwargs)
        try:
            async for chunk in stream:
                if chunk.usage is not None:
                    self.recorder.observe({"type": "model_usage", "metadata": {"request_id": identity, "usage": chunk.usage}})
                yield chunk
                if self.tokens > self.configuration.token_budget:
                    raise LiveBudgetExceeded("Provider-reported token budget exceeded during streaming.")
            status = "completed"
        finally:
            if hasattr(stream, "aclose"):
                await stream.aclose()
            self.recorder.observe({"type": "model_request_finished", "metadata": {"request_id": identity, "status": status}})

    async def chat(self, *args, **kwargs):
        raise LiveBudgetExceeded("Implicit nonstream provider fallback is disabled.")

    async def list_models(self):
        return []


def configured_provider(configuration: HarnessConfiguration, config_path: str | None = None) -> BaseProvider:
    """Construct only the explicitly selected configured provider; no probes."""
    from firik_agent.config import load_config
    from firik_agent.credentials import get_api_key
    from firik_agent.providers.firik import FirikProvider
    from firik_agent.providers.openai_provider import OpenAIProvider

    config = load_config(config_path)
    name = configuration.model.provider
    settings = config.get("providers", {}).get(name)
    if not isinstance(settings, dict):
        raise ValueError("Selected benchmark provider is absent from configuration.")
    defaults = {"firik": "https://evren-llmapi.ssyz.org.tr/v1", "llmtr": "https://llmtr.com/v1",
                "openai": "https://api.openai.com/v1", "ollama": "http://127.0.0.1:11434/v1",
                "lmstudio": "http://127.0.0.1:1234/v1"}
    address = settings.get("base_url", defaults.get(name))
    if not isinstance(address, str) or not address:
        raise ValueError("Selected benchmark provider needs a configured base_url.")
    key = get_api_key(name, settings)
    if name in {"firik", "llmtr"}:
        return FirikProvider(api_key=key, base_url=address, default_model=configuration.model.model,
                             name=name, is_gateway=name == "llmtr")
    if name not in {"openai", "ollama", "lmstudio"} and settings.get("type") != "openai_compatible":
        raise ValueError("Selected benchmark provider is not a supported configured provider.")
    return OpenAIProvider(api_key=key, base_url=address, default_model=configuration.model.model, name=name)


async def run_live_suite(
    suite: BenchmarkSuite | None = None, *, configuration: HarnessConfiguration,
    allow_live: bool = False, config_path: str | None = None, trials: int = 1,
    provider: BaseProvider | None = None, label: str = "candidate",
) -> dict[str, Any]:
    """Run a validated source suite only after explicit opt-in to model traffic."""
    if allow_live is not True:
        raise PermissionError("Live benchmarks require explicit --allow-live authorization before provider or credential access.")
    configuration = HarnessConfiguration.model_validate(configuration.model_dump())
    suite = coding_suite() if suite is None else BenchmarkSuite.model_validate(suite.model_dump())
    if suite.mode != "live":
        raise ValueError("A live benchmark requires a live suite.")
    if isinstance(trials, bool) or not isinstance(trials, int) or not 1 <= trials <= 100:
        raise ValueError("Trials must be between 1 and 100.")
    if configuration.model.provider == "replay":
        raise ValueError("Live evaluation requires an explicit non-replay provider and model.")
    # Validate every immutable fixture before touching credentials or providers.
    from firik_agent.benchmarks.fixtures import fixture_revision
    for task in suite.tasks:
        if fixture_revision(task.repository.files) != task.repository.starting_revision:
            raise ValueError("Task starting revision does not match its immutable fixture bytes.")
    selected = provider or configured_provider(configuration, config_path)
    if selected.name != configuration.model.provider or selected.current_model != configuration.model.model:
        raise ValueError("Selected provider identity does not match the benchmark configuration.")
    endpoint_hash = hashlib.sha256(selected.base_url.encode()).hexdigest()
    if configuration.model.endpoint_sha256 and configuration.model.endpoint_sha256 != endpoint_hash:
        raise ValueError("Selected provider endpoint does not match the pinned endpoint fingerprint.")
    from firik_agent.benchmarks.runner import (
        LAB_VERSION, PROMPT_REVISION, TOOL_REVISION, repository_metadata,
        run_trial, source_fingerprint,
    )
    source_start = source_fingerprint()
    rows = []
    manifests = [task_manifest(task, timeout_seconds=configuration.timeout_seconds,
                               token_budget=configuration.token_budget) for task in suite.tasks]
    for repetition in range(1, trials + 1):
        for task, manifest in zip(suite.tasks, manifests):
            row = await run_trial(manifest, repetition, profile=configuration.id,
                                  provider=BudgetedProvider(selected, configuration), configuration=configuration)
            if row["fixture_revision"] != task.repository.starting_revision:
                raise RuntimeError("Trial workspace does not match the pinned starting revision.")
            row["category"] = task.category
            row["curation_status"] = task.curation_status
            rows.append(row)
    controlled = {"tasks": [task.model_dump() for task in suite.tasks], "model": configuration.model.model_dump(),
                  "endpoint_sha256": endpoint_hash, "token_budget": configuration.token_budget,
                  "request_budget": configuration.request_budget, "timeout_seconds": configuration.timeout_seconds,
                  "permissions": configuration.permissions.model_dump(), "evaluator": configuration.evaluator_version,
                  "temperature": configuration.temperature, "max_output_tokens": configuration.max_output_tokens}
    report = {"schema_version": 1, "label": label, "method": LIVE_LABEL,
        "created_at": datetime.now(timezone.utc).isoformat(), "suite_id": suite.id,
        "metadata": {"mode": "live", "repository": repository_metadata(), "source_sha256": source_start,
            "source_sha256_after": source_fingerprint(),
            "controlled_settings_sha256": fingerprint(controlled), "configuration_sha256": configuration.fingerprint(),
            "configuration": configuration.model_dump(), "harness_version": __version__, "lab_version": LAB_VERSION,
            "provider": selected.name, "model": selected.current_model, "endpoint_sha256": endpoint_hash,
            "profile": configuration.id, "prompt_revision": PROMPT_REVISION, "tool_revision": TOOL_REVISION,
            "evaluator_version": configuration.evaluator_version, "suite_sha256": suite.fingerprint(),
            "capability_evidence": {"source": "configured_benchmark_contract", "supports_tools": True},
            "environment": {"python": platform.python_version(), "os": platform.system(),
                            "machine": platform.machine(), "sqlite": sqlite3.sqlite_version},
            "budget_enforcement": "Hard request/output bounds; cumulative provider-reported token limits. Unknown usage remains null; no monetary cap is inferred."},
        "tasks": [{"id": task.id, "manifest_sha256": manifest.fingerprint(),
                   "timeout_seconds": manifest.timeout_seconds, "token_budget": manifest.token_budget,
                   "starting_revision": task.repository.starting_revision, "category": task.category}
                  for task, manifest in zip(suite.tasks, manifests)],
        "trials_per_task": trials, "results": rows, "summary": {},
    }
    report["metadata"]["source_changed"] = source_start != report["metadata"]["source_sha256_after"]
    from firik_agent.benchmarks.metrics import enrich_report
    enriched = enrich_report(report)
    return redact_sensitive(enriched if enriched is not None else report, (selected.api_key,))
