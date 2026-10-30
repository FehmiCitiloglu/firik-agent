"""Provider-free Docker prerequisite and isolated-workspace inspection."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

from firik_agent.core.execution import DockerExecutionBackend, ExecutionConfig, build_backend
from firik_agent.projects.coding_runtime import safe_payload


def main(argv=None):
    parser = argparse.ArgumentParser(prog="firik-agent sandbox")
    commands = parser.add_subparsers(dest="action", required=True)
    doctor = commands.add_parser("doctor", help="Check local daemon, Linux containers and seccomp; no image pulls")
    doctor.add_argument("--backend", choices=("host", "docker"), default="docker")
    commands.add_parser("retained", help="List retained Docker artifacts and crash metadata; no commands replayed")
    worktrees = commands.add_parser("workspaces", help="Inspect managed agent worktrees and integration/cleanup status")
    worktrees.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.action == "doctor":
            config = ExecutionConfig(backend=args.backend)
            result = asdict(asyncio.run(build_backend(config).availability()))
            result.update(limits=asdict(config), external_tools_sandboxed=False,
                host_fallback=False, image_pulled=False,
                network="host" if config.backend == "host" else "none",
                resource_enforcement="container" if config.backend == "docker" else "timeout_and_output_only")
            status = 0 if result["available"] else 2
        elif args.action == "retained":
            result = {"workspaces": DockerExecutionBackend.retained_workspaces(), "commands_replayed": False}
            status = 0
        else:
            from firik_agent.projects.isolated_workspaces import IsolatedWorkspaceManager
            manager = IsolatedWorkspaceManager(args.repository)
            result = {"workspaces": manager.list_workspaces(), "commands_replayed": False}
            status = 0
        print(json.dumps(safe_payload(result), ensure_ascii=False, indent=2, default=str))
        return status
    except (ValueError, RuntimeError, OSError) as error:
        print(str(safe_payload(str(error))), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
