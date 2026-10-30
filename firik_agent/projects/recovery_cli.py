"""Provider-free inspection and explicit preparation of durable coding runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from firik_agent.projects.coding_sessions import CodingSessionStore, redact_sensitive
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.resume import DurableResumeController


def _permissions(db, project_id):
    """Use today's project policy, including legacy denials, for preparation."""
    from firik_agent.projects.coding_runtime import CodingRunControl
    settings = db.get_settings(project_id)
    prefs = settings.coding_preferences
    control = CodingRunControl("recovery-inspection", project_id,
        mode=prefs.get("mode", "approved"), permissions=prefs.get("permissions"), policy=prefs.get("policy"))
    for old, names in {
        "read_files": ("allow_reads",), "write_files": ("allow_edits",),
        "delete_files": ("allow_deletes",), "terminal": ("allow_terminal", "allow_tests"),
        "network_access": ("allow_terminal", "allow_tests", "allow_mcp"),
        "install_packages": ("allow_terminal", "allow_tests"),
    }.items():
        if settings.permissions.get(old) in {"deny", "never"}:
            control.permissions.update({name: False for name in names})
    return {**control.permissions, **control.policy_snapshot()}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="firik-agent runs",
        description="Inspect durable coding runs without contacting a model or replaying operations.")
    parser.add_argument("--db", help="Existing projects SQLite database")
    commands = parser.add_subparsers(dest="action", required=True)
    history = commands.add_parser("history", help="List project run history")
    history.add_argument("--project", required=True)
    history.add_argument("--limit", type=int, default=100)
    for action in ("inspect", "compare", "reconcile", "resume", "cancel", "export"):
        command = commands.add_parser(action)
        command.add_argument("run_id")
        command.add_argument("--project", required=True)
        if action == "reconcile":
            scope = command.add_mutually_exclusive_group(required=True)
            scope.add_argument("--attempt")
            scope.add_argument("--workspace", action="store_true")
            command.add_argument("--outcome", choices=("completed", "failed", "cancelled"))
            command.add_argument("--evidence", required=True, help="Observed evidence; never a request to repeat the operation")
            command.add_argument("--approve", action="store_true", help="Accept today's workspace as the new baseline")
        elif action == "resume":
            command.add_argument("--approve", action="store_true", help="Explicitly approve preparation under current policy")
        elif action == "export":
            command.add_argument("--output", type=Path, help="New sanitized JSON report (existing files are never overwritten)")
    args = parser.parse_args(argv)
    try:
        db = ProjectDatabase(args.db)
        controller = DurableResumeController(db)
        if args.action == "history":
            result = [{"run_id": run.id, "session_id": run.session_id, "status": run.status,
                       "title": run.title, "created_at": run.created_at, "resumed_from": run.resumed_from}
                      for run in CodingSessionStore(db).list_runs(args.project, limit=args.limit)]
        elif args.action == "inspect":
            result = controller.inspect(args.run_id, project_id=args.project,
                current_permissions=_permissions(db, args.project))
        elif args.action == "compare":
            result = controller.compare(args.run_id, project_id=args.project)
        elif args.action == "reconcile":
            if args.workspace:
                result = controller.reconcile_workspace(args.run_id, evidence=args.evidence,
                    approved=args.approve, project_id=args.project)
            elif args.outcome:
                result = controller.reconcile(args.run_id, args.attempt, args.outcome,
                    evidence=args.evidence, project_id=args.project)
            else:
                raise ValueError("Attempt reconciliation requires --outcome.")
        elif args.action == "cancel":
            result = controller.cancel(args.run_id, project_id=args.project).model_dump(mode="json")
        elif args.action == "resume":
            prepared = controller.prepare_resume(args.run_id, project_id=args.project,
                current_permissions=_permissions(db, args.project), approved=args.approve)
            result = {"run_id": prepared.id, "status": prepared.status, "resumed_from": prepared.resumed_from,
                      "execution_started": False,
                      "message": "Continuation prepared. Open this run in the desktop and explicitly continue; no model or tool was dispatched."}
        else:
            result = controller.export_run(args.run_id, project_id=args.project)
        serialized = json.dumps(redact_sensitive(result), ensure_ascii=False, indent=2)
        if args.action == "export" and args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(serialized + "\n")
            print(json.dumps({"report": str(args.output.resolve()), "sanitized": True}))
        else:
            print(serialized)
        return 0
    except (ValueError, PermissionError, OSError) as error:
        print(str(redact_sensitive(str(error))), file=sys.stderr)
        return 2
    except sqlite3.Error:
        print("Durable storage could not be read or written; recovery did not dispatch any operation.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
