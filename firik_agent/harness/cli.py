"""Credential-free discovery and explicitly authorized harness experiments."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
from typing import Sequence

from firik_agent.harness.config import resolve_harness_configuration

COMMANDS = frozenset({"backends", "settings", "experiment", "recommend", "prompt"})
from firik_agent.benchmarks.storage import MAX_REPORT_BYTES
MAX_CONFIG_BYTES = 8 * 1024 * 1024


def read_document(path: Path) -> dict:
    """Bound untrusted local reports before parsing; never execute content."""
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Harness document exceeds the 64 MiB limit.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Harness document must be a JSON object.")
    return value


def _configuration(path: Path | None) -> dict:
    if path is None:
        return {}
    import yaml
    with path.open("rb") as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError("Harness configuration is too large.")
    value = yaml.safe_load(raw) or {}
    resolve_harness_configuration(value)
    return value


def _write_report(path: Path | None, report: dict) -> None:
    from firik_agent.projects.coding_sessions import redact_sensitive
    value = json.dumps(redact_sensitive(report), ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n"
    if path is None:
        print(value, end="")
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(value)
        print(str(path.resolve()))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="firik harness")
    commands = parser.add_subparsers(dest="command", required=True)
    backends = commands.add_parser("backends", help="Inspect supported local interfaces without reading credentials.")
    backends.add_argument("--config", type=Path)
    settings = commands.add_parser("settings", help="Show resolved non-secret harness settings.")
    settings.add_argument("--config", type=Path)
    experiment = commands.add_parser("experiment", help="Run an explicit comparative plan in isolated disposable workspaces.")
    experiment.add_argument("--plan", type=Path, required=True)
    experiment.add_argument("--config", type=Path)
    experiment.add_argument("--allow-paid", action="store_true", help="Authorize all paid configurations named in the plan.")
    experiment.add_argument("--output", type=Path)
    recommendation = commands.add_parser("recommend", help="Suggest from validated measured evidence; never switch a task.")
    recommendation.add_argument("--report", type=Path, required=True)
    recommendation.add_argument("--objective", choices=("maximize_success", "minimize_cost", "minimize_latency", "balanced", "local_only"), default="balanced")
    recommendation.add_argument("--output", type=Path)
    prompt = commands.add_parser("prompt", help="Explicit live tool-free Pi prompt; no project files or installed credentials are imported.")
    prompt.add_argument("--backend", choices=("pi",), required=True)
    prompt.add_argument("--provider", choices=("openai", "anthropic", "google"), required=True)
    prompt.add_argument("--model", required=True)
    prompt.add_argument("--prompt", required=True)
    prompt.add_argument("--credential-env", required=True, help="Exact environment variable explicitly authorized for this provider key.")
    prompt.add_argument("--allow-live", action="store_true", help="Authorize this prompt and provider network request, which may incur charges.")
    prompt.add_argument("--config", type=Path, help="Explicit configuration enabling the audited Pi backend.")
    prompt.add_argument("--timeout", type=float, default=120)
    prompt.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    known_secrets = ()
    try:
        if args.command == "settings":
            _write_report(None, resolve_harness_configuration(_configuration(args.config)).to_dict())
            return 0
        if args.command == "backends":
            from firik_agent.harness.registry import backend_inventory
            _write_report(None, {"backends": backend_inventory(_configuration(args.config)),
                                 "automatic_backend_switching": False})
            return 0
        if args.output and (args.output.exists() or args.output.is_symlink()):
            raise FileExistsError("Output already exists; choose a new report path.")
        if args.command == "prompt":
            if not args.allow_live:
                raise ValueError("Tool-free provider requests require explicit --allow-live; no credential was read.")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.credential_env):
                raise ValueError("--credential-env must name one exact environment variable.")
            if not args.model.strip() or len(args.model) > 200 or not args.prompt.strip() or len(args.prompt) > 1_000_000:
                raise ValueError("An exact bounded model ID and nonempty bounded prompt are required.")
            from firik_agent.harness.models import HarnessExecutionPolicy
            # Validate policy and configured availability before looking up the
            # one credential explicitly named by the user. No dotenv/keychain
            # or existing Pi authentication is accessed by this entry point.
            policy = HarnessExecutionPolicy(tool_mode="none", allow_network=True, timeout_seconds=args.timeout)
            from firik_agent.harness.registry import create_backend
            backend = create_backend(args.backend, config=_configuration(args.config))
            metadata = backend.get_metadata()
            if not metadata.available:
                raise RuntimeError(metadata.reason or "The selected backend is unavailable.")
            key = os.environ.get(args.credential_env)
            if not key or "\x00" in key:
                raise ValueError("The explicitly named credential environment variable is empty or invalid.")
            known_secrets = (key,)
            result = asyncio.run(_tool_free_prompt(backend, policy,
                {"name": args.provider, "model": args.model, "api_key": key, "allow_live": True}, args.prompt))
            from firik_agent.projects.coding_sessions import redact_sensitive
            result = redact_sensitive(result, known_secrets=known_secrets)
        elif args.command == "recommend":
            from firik_agent.harness.optimizer import recommend
            result = recommend(read_document(args.report), objective=args.objective)
        else:
            from firik_agent.harness.comparison import ComparisonPlan, run_comparison
            plan = ComparisonPlan.model_validate(read_document(args.plan))
            config = _configuration(args.config)
            result = asyncio.run(run_comparison(plan, allow_paid=args.allow_paid,
                                                 backend_factory=_backend_factory(config)))
        _write_report(args.output, result)
        if args.command == "prompt":
            return 0 if result.get("status") == "completed" else 1
        return 1 if args.command == "experiment" and any(not row.get("success", False) for row in result.get("rows", [])) else 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        from firik_agent.projects.coding_sessions import redact_sensitive
        details = []
        current = error
        seen = set()
        while current is not None and id(current) not in seen and len(details) < 8:
            seen.add(id(current))
            details.append(current.to_dict() if hasattr(current, "to_dict") else
                           {"code": type(current).__name__, "message": str(current)})
            current = current.__cause__ or current.__context__
        print(json.dumps(redact_sensitive({"error": details[0], "causes": details[1:]}, known_secrets=known_secrets),
                         ensure_ascii=False, default=str), file=sys.stderr)
        return 2


async def _tool_free_prompt(backend, policy, provider: dict, text: str) -> dict:
    from firik_agent.harness.models import HarnessTask
    session = None
    run_id = None
    completed = False
    try:
        session = await backend.initialize_session(policy=policy, provider=provider)
        run_id = await backend.execute_task(session, HarnessTask(text, model=provider["model"], profile="minimal"))
        # Drain the bounded stream without printing untrusted/raw backend
        # frames. The final normalized result retains text, errors and usage.
        async for _ in backend.stream_events(session, run_id):
            pass
        result = await backend.inspect_run(session, run_id)
        completed = result.status == "completed"
        return result.to_dict()
    finally:
        try:
            if session is not None:
                try:
                    if run_id is not None and not completed:
                        await backend.request_cancellation(session, run_id)
                finally:
                    await backend.close_session(session)
        finally:
            provider.clear()


def _backend_factory(config: dict):
    from firik_agent.harness.comparison import offline_native_factory
    from firik_agent.harness.registry import create_backend
    settings = resolve_harness_configuration(config)
    def factory(configuration, root, task, plan):
        if not settings.backend(configuration.backend).enabled:
            raise ValueError("Harness backend is disabled: " + configuration.backend)
        if configuration.backend == "native" and configuration.provider == "replay":
            return offline_native_factory(configuration, root, task, plan)
        # Registry discovery is not a source of provider credentials. Live
        # native runs need a host-bound existing runtime; external experiments
        # fail clearly when their policy/provider cannot be safely mapped.
        return create_backend(configuration.backend, config=config)
    return factory


if __name__ == "__main__":
    raise SystemExit(main())
