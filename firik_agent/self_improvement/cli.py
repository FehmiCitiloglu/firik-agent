"""Offline-first host commands for inspecting and governing learning.

Inspection and local experiments do not construct an Agent or contact a model.
Only explicit generate-code, with digest consent, initializes the existing
configured Agent. Approval is a local operation bound to a reviewed revision.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
from typing import Sequence

import yaml
from pydantic import ValidationError


def _common(parser, *, suppressed=False):
    default = argparse.SUPPRESS if suppressed else None
    parser.add_argument("--db", type=Path, default=default, help="Projects SQLite database")
    parser.add_argument("--config", type=Path, default=default, help="Configuration YAML; model access occurs only for explicit generate-code")


def build_parser():
    parser = argparse.ArgumentParser(prog="firik-agent improve",
        description="Inspect and govern Firik learning; only explicit generate-code sends a bounded digest to a model.")
    _common(parser)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "metrics", "settings", "history", "proposals", "memories", "prune"):
        command = commands.add_parser(name)
        _common(command, suppressed=True)
        if name in {"history", "proposals", "memories"}:
            command.add_argument("--limit", type=int, default=50)
            command.add_argument("--offset", type=int, default=0)
        if name == "proposals":
            command.add_argument("--status")
        elif name == "memories":
            command.add_argument("--project")
        elif name == "settings":
            command.add_argument("--set", dest="updates", help="JSON object of validated learning settings")
    command = commands.add_parser("reflect")
    _common(command, suppressed=True)
    command.add_argument("--session", required=True)
    command.add_argument("--project")
    for name in ("show", "diff", "evaluate", "approve", "reject", "rollback"):
        command = commands.add_parser(name)
        _common(command, suppressed=True)
        command.add_argument("id")
        if name in {"approve", "reject", "rollback"}:
            command.add_argument("--revision", type=int, required=True,
                                 help="Exact revision inspected using show/diff; stale decisions are rejected")
    for name in ("propose-tool", "propose-harness"):
        command = commands.add_parser(name, help="Stage a typed JSON proposal; no code is executed or activated")
        _common(command, suppressed=True)
        command.add_argument("--file", type=Path, required=True, help="JSON proposal object (maximum 256 KiB)")
        command.add_argument("--project")
        command.add_argument("--evidence", action="append", default=[])
    command = commands.add_parser("stage-code", help="Stage a bounded patch in an isolated candidate workspace")
    _common(command, suppressed=True)
    command.add_argument("--repository", type=Path, required=True)
    command.add_argument("--allowed-file", action="append", required=True,
                         help="Exact repository-relative file permitted in the candidate; repeat per file")
    command.add_argument("--patch", type=Path, required=True, help="Unified patch file (maximum 256 KiB)")
    command.add_argument("--project")
    command.add_argument("--evidence", action="append", default=[])
    command = commands.add_parser("generate-code", help="Ask the configured coding Agent for a bounded, inert source patch")
    _common(command, suppressed=True)
    command.add_argument("--repository", type=Path, required=True)
    command.add_argument("--allowed-file", action="append", required=True,
                         help="Exact repository-relative source file whose contents may be sent; repeat per file")
    command.add_argument("--task", required=True, help="Narrow source change requested from the existing configured model")
    command.add_argument("--project")
    command.add_argument("--evidence", action="append", default=[])
    command.add_argument("--allow-model-digest", action="store_true", required=True,
                         help="Authorize sending this task and exact source scope to the configured model; may use paid quota")
    command = commands.add_parser("experiment", help="Run isolated fixtures/gates only in explicitly enabled experiment mode")
    _common(command, suppressed=True)
    command.add_argument("id")
    spec = command.add_mutually_exclusive_group(required=True)
    spec.add_argument("--fixtures", type=Path, help="Host-owned tool fixture JSON")
    spec.add_argument("--gates", type=Path, help="Host-owned code quality-gate JSON")
    spec.add_argument("--cases", type=Path, help="Host-owned paired memory retrieval case JSON")
    command = commands.add_parser("monitor", help="Measure an applied optimization for regression; never rolls it back automatically")
    _common(command, suppressed=True)
    command.add_argument("id")
    command.add_argument("--cases", type=Path, required=True, help="Independent paired memory retrieval case JSON")
    command = commands.add_parser("revise", help="Stage a targeted new proposal revision")
    _common(command, suppressed=True)
    command.add_argument("id")
    command.add_argument("--revision", type=int, required=True)
    command.add_argument("--payload", type=Path, required=True, help="Replacement proposal payload JSON")
    command = commands.add_parser("disable-skill", help="Disable an approved learned skill using its reviewed revision")
    _common(command, suppressed=True)
    command.add_argument("id")
    command.add_argument("--revision", type=int, required=True)
    command = commands.add_parser("cancel")
    _common(command, suppressed=True)
    command.add_argument("id", nargs="?")
    command = commands.add_parser("remember", help="Stage a user-confirmed fact for review")
    _common(command, suppressed=True)
    command.add_argument("content")
    command.add_argument("--project")
    command.add_argument("--kind", default="semantic")
    command.add_argument("--supersedes", help="Memory ID corrected by this proposed revision")
    command.add_argument("--evidence", action="append", default=[])
    command = commands.add_parser("learn-workflow", help="Stage a skill; does not activate executable behavior")
    _common(command, suppressed=True)
    command.add_argument("name")
    command.add_argument("--description", required=True)
    command.add_argument("--instructions", type=Path, required=True, help="Bounded UTF-8 procedure file")
    command.add_argument("--project")
    command.add_argument("--evidence", action="append", default=[])
    command = commands.add_parser("remove-memory")
    _common(command, suppressed=True)
    command.add_argument("id")
    command = commands.add_parser("pin-memory", help="Pin or unpin an active approved memory")
    _common(command, suppressed=True)
    command.add_argument("id")
    command.add_argument("--unpin", action="store_true")
    command = commands.add_parser("retrieve", help="Read-only context inspector")
    _common(command, suppressed=True)
    command.add_argument("query")
    command.add_argument("--project")
    command.add_argument("--token-budget", type=int)
    return parser


def _configuration(path):
    """Read only explicit/default YAML, with a bound and no dotenv side effects."""
    if path is None:
        from firik_agent.config import _resolve_default_config_path
        candidate = _resolve_default_config_path()
        if not candidate.is_file():
            return {}
    else:
        candidate = path
    if candidate.stat().st_size > 1024 * 1024:
        raise ValueError("Configuration exceeds the 1 MiB limit.")
    try:
        result = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        raise ValueError("Configuration must be valid YAML.") from None
    if not isinstance(result, dict):
        raise ValueError("Configuration must be an object.")
    return result


def _bounded_text(path, maximum=256 * 1024):
    if path.stat().st_size > maximum:
        raise ValueError(f"Input file exceeds the {maximum // 1024} KiB limit.")
    return path.read_text(encoding="utf-8")


def _json_file(path, *, object_only=False):
    try:
        result = json.loads(_bounded_text(path))
    except json.JSONDecodeError:
        raise ValueError("Input file must contain valid JSON.") from None
    if object_only and not isinstance(result, dict):
        raise ValueError("Proposal payload must be a JSON object.")
    return result


async def _generate_code(coordinator, database, config, args):
    # Consent and mode are checked before normal Agent startup can load a
    # credential or initialize an existing integration.
    if not args.allow_model_digest:
        raise PermissionError("Explicit model digest consent is required")
    if args.config is None:
        raise ValueError("generate-code requires an explicit --config with the intended provider and model")
    if coordinator.settings().get("mode") != "experiment":
        raise PermissionError("Code generation requires explicitly enabled experiment mode")
    from firik_agent.core.agent import Agent
    agent = Agent(config=config, config_path=str(args.config))
    agent._learning_database = database
    try:
        await agent.initialize()
        return await coordinator.generate_code(agent, str(args.repository), args.allowed_file, args.task,
            project_id=args.project, evidence_ids=args.evidence, allow_model_digest=args.allow_model_digest)
    finally:
        await agent.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    coordinator = None
    try:
        from firik_agent.projects.db import ProjectDatabase
        from firik_agent.self_improvement.coordinator import ImprovementCoordinator
        config = _configuration(args.config)
        database = args.db or (args.config.parent / "projects.db" if args.config else None)
        database = ProjectDatabase(database)
        coordinator = ImprovementCoordinator(database, config=config, config_path=args.config)
        action = args.action
        if action in {"status", "metrics", "prune"}:
            result = getattr(coordinator, action)()
        elif action == "settings":
            if args.updates is None:
                result = coordinator.settings()
            else:
                try:
                    updates = json.loads(args.updates)
                except json.JSONDecodeError:
                    raise ValueError("Settings must be a JSON object.") from None
                if not isinstance(updates, dict):
                    raise ValueError("Settings must be a JSON object.")
                result = coordinator.configure(updates)
        elif action == "history":
            result = coordinator.history(limit=args.limit, offset=args.offset)
        elif action == "proposals":
            result = coordinator.proposals(status=args.status, limit=args.limit, offset=args.offset)
        elif action == "memories":
            result = coordinator.memories(project_id=args.project, limit=args.limit, offset=args.offset)
        elif action == "reflect":
            result = coordinator.reflect(args.session, project_id=args.project)
        elif action in {"show", "diff", "evaluate"}:
            result = getattr(coordinator, action)(args.id)
        elif action in {"approve", "reject", "rollback"}:
            result = getattr(coordinator, action)(args.id, expected_revision=args.revision, actor="user")
        elif action in {"propose-tool", "propose-harness"}:
            operation = coordinator.propose_tool if action == "propose-tool" else coordinator.propose_harness
            result = operation(_json_file(args.file, object_only=True), project_id=args.project,
                               evidence_ids=args.evidence)
        elif action == "stage-code":
            result = coordinator.stage_code(str(args.repository), args.allowed_file, _bounded_text(args.patch),
                                            project_id=args.project, evidence_ids=args.evidence)
        elif action == "generate-code":
            result = asyncio.run(_generate_code(coordinator, database, config, args))
        elif action == "experiment":
            result = coordinator.experiment(args.id, fixtures=_json_file(args.fixtures) if args.fixtures else None,
                                           gates=_json_file(args.gates) if args.gates else None,
                                           cases=_json_file(args.cases) if args.cases else None)
        elif action == "monitor":
            result = coordinator.monitor(args.id, _json_file(args.cases))
        elif action == "revise":
            result = coordinator.revise(args.id, _json_file(args.payload, object_only=True),
                                        expected_revision=args.revision)
        elif action == "disable-skill":
            result = coordinator.disable_skill(args.id, expected_revision=args.revision, actor="user")
        elif action == "cancel":
            result = coordinator.cancel(args.id)
        elif action == "remember":
            result = coordinator.propose_memory(args.content, project_id=args.project, kind=args.kind,
                source_reference="user:cli", evidence_ids=args.evidence, supersedes_id=args.supersedes)
        elif action == "learn-workflow":
            if args.instructions.stat().st_size > 64 * 1024:
                raise ValueError("Workflow procedure exceeds the 64 KiB limit.")
            result = coordinator.propose_skill(args.name, args.description,
                args.instructions.read_text(encoding="utf-8"), project_id=args.project, evidence_ids=args.evidence)
        elif action == "remove-memory":
            result = coordinator.remove_memory(args.id)
        elif action == "pin-memory":
            result = coordinator.pin_memory(args.id, pinned=not args.unpin)
        else:
            result = coordinator.retrieve(args.query, project_id=args.project, token_budget=args.token_budget)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except ValidationError:
        print("Learning configuration or input failed schema validation; no candidate was activated.", file=sys.stderr)
        return 2
    except (ValueError, TypeError, KeyError, PermissionError, OSError, RuntimeError) as error:
        from firik_agent.projects.coding_sessions import redact_sensitive
        print(str(redact_sensitive(str(error))), file=sys.stderr)
        return 2
    except sqlite3.Error:
        print("Learning storage could not be read or written; no operation was replayed.", file=sys.stderr)
        return 2
    finally:
        if coordinator is not None:
            coordinator.close()


if __name__ == "__main__":
    raise SystemExit(main())
