"""Command-line interface for development tasks and task records."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .agent import SeniorDevelopmentAgent
from .process import DevelopmentProcess
from .workspace import Workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="firik-agent",
        description="Gated senior software-development agent",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    develop = subparsers.add_parser("develop", help="Run a software-development objective")
    develop.add_argument("objective", help="Concrete development objective")
    develop.add_argument(
        "--workspace", default=".", help="Workspace root (default: current directory)"
    )
    develop.add_argument("--model", help="Hugging Face model id")
    develop.add_argument("--provider", help="Hugging Face Inference Provider")
    develop.add_argument("--local", action="store_true", help="Run a local Transformers model")
    develop.add_argument("--task-id", help="Stable task id")
    develop.add_argument("--max-steps", type=int, default=40)
    develop.add_argument("--verification-retries", type=int, default=3)

    status = subparsers.add_parser("status", help="Read a persisted task record")
    status.add_argument("task_id")
    status.add_argument("--workspace", default=".")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "status":
        process = DevelopmentProcess(Workspace(arguments.workspace))
        record = process.load(arguments.task_id)
        print(json.dumps(record.to_dict(), indent=2))
        return 0

    agent = SeniorDevelopmentAgent(
        workspace=Path(arguments.workspace),
        model_id=arguments.model,
        provider=arguments.provider,
        local=arguments.local,
        max_steps=arguments.max_steps,
        max_verification_attempts=arguments.verification_retries,
    )
    result = agent.run(arguments.objective, task_id=arguments.task_id)
    print(result.output)
    print(f"\nTask: {result.task_id}")
    print(f"Phase: {result.phase}")
    print(f"Record: {result.record_path}")
    return 0 if result.complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
