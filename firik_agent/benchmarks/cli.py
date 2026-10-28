"""Harness Lab CLI; offline dispatch never reads provider configuration."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys
from typing import Sequence

from firik_agent.benchmarks.manifests import MAX_MANIFEST_BYTES, TaskManifest, builtin_tasks
from firik_agent.benchmarks.runner import LABEL, run_suite
from firik_agent.benchmarks.comparison import compare_reports
from firik_agent.benchmarks.metrics import enrich_report
from firik_agent.benchmarks.storage import export_report, load_report, read_json, write_outputs

_read_json = read_json  # Compatibility for older callers.


def doctor() -> dict:
    from firik_agent.benchmarks.fixtures import offline_tasks
    from firik_agent.benchmarks.coding import coding_suite
    tasks = offline_tasks()
    coding = coding_suite()
    git = shutil.which("git")
    return {"schema_version": 1, "ready": bool(git), "git_available": bool(git),
            "offline_suite": {"id": "offline-smoke", "tasks": len(tasks)},
            "coding_suite": {"id": coding.id, "tasks": len(coding.tasks), "version": coding.version,
                             "fingerprint": coding.fingerprint()},
            "offline_network": "denied", "repository_execution": "disabled",
            "evaluator": "host-controlled exact source and AST; repository code is never executed",
            "live": "explicit --allow-live, --provider and --model required; not probed",
            "sandbox": "no OS sandbox; arbitrary fixture commands are disabled"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="firik harness", description="Offline-first Harness Benchmark Lab")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check local readiness without credentials or model API access.")
    commands.add_parser("profiles", help="Çalışma profillerini ve kaynak sınırlarını göster.")
    context = commands.add_parser("context", help="Proje bağlamını API isteği göndermeden incele.")
    context.add_argument("--root", type=Path, help="Yerel proje klasörü (varsayılan: geçerli klasör).")
    context.add_argument("--project-id", help="Mevcut veritabanındaki proje kimliği.")
    context.add_argument("--database", type=Path, help="Proje tercihleri ve hafıza için mevcut SQLite dosyası.")
    context.add_argument("--request", required=True, help="Bağlam seçimi yapılacak kullanıcı görevi.")
    context.add_argument("--profile", choices=("minimal", "coding", "research", "deep-debug", "multi-agent", "local-efficient"))
    context.add_argument("--file", action="append", default=[], help="Açıkça seçilen proje dosyası.")
    context.add_argument("--exclude", action="append", default=[], help="Bağlama alınmayacak yol.")
    listing = commands.add_parser("list", help="List bounded bundled task manifests.")
    listing.add_argument("--suite", choices=("legacy", "offline-smoke", "coding"), default="legacy")
    listing.add_argument("--json", action="store_true")
    run = commands.add_parser("run", aliases=["bench"], help="Run isolated native harness trials.")
    run.add_argument("--suite", choices=("legacy", "offline-smoke", "coding"), default="offline-smoke")
    run.add_argument("--trials", "--repeat", type=int, default=3, dest="trials", help="Repetitions per task (1–100).")
    run.add_argument("--output", type=Path, default=None, help="New report directory (default: ./harness-results/<unique ID>).")
    run.add_argument("--task", action="append", default=[])
    run.add_argument("--manifest", type=Path, action="append", default=[])
    run.add_argument("--label", default="candidate")
    run.add_argument("--profile", type=Path, help="Versioned HarnessConfiguration JSON.")
    run.add_argument("--allow-live", action="store_true", help="Explicitly authorize configured model API requests.")
    run.add_argument("--provider", help="Configured provider name; live coding only.")
    run.add_argument("--model", help="Explicit model ID; live coding only.")
    run.add_argument("--config", type=Path, help="Existing provider config; live coding only.")
    compare = commands.add_parser("compare", help="Compare paired tasks, budgets, models and evaluators.")
    compare.add_argument("baseline_path", type=Path, nargs="?")
    compare.add_argument("candidate_path", type=Path, nargs="?")
    compare.add_argument("--baseline", type=Path)
    compare.add_argument("--candidate", type=Path)
    compare.add_argument("--output", type=Path, help="New comparison JSON file or output directory.")
    args = parser.parse_args(argv)
    try:
        if args.command == "profiles":
            from firik_agent.projects.harness_profiles import PROFILES, resolve_harness_profile
            print(json.dumps([resolve_harness_profile(session_override=name) for name in PROFILES], ensure_ascii=False, indent=2))
            return 0
        if args.command == "context":
            from firik_agent.projects.context_cli import print_inspection
            return print_inspection(args)
        if args.command == "doctor":
            result = doctor()
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 2
        if args.command == "list":
            if args.suite == "coding":
                from firik_agent.benchmarks.coding import coding_suite
                suite = coding_suite()
                print(suite.model_dump_json(indent=2) if args.json else "\n".join(f"{task.id}\t{task.category}\t{task.prompt}" for task in suite.tasks))
            else:
                from firik_agent.benchmarks.fixtures import offline_tasks
                tasks = offline_tasks() if args.suite == "offline-smoke" else builtin_tasks()
                print(json.dumps([task.model_dump() for task in tasks], indent=2) if args.json else
                      "\n".join(f"{task.id}\t{task.prompt}" for task in tasks))
            return 0
        if args.command == "compare":
            baseline, candidate = args.baseline or args.baseline_path, args.candidate or args.candidate_path
            if baseline is None or candidate is None:
                raise ValueError("Both baseline and candidate reports are required.")
            result = compare_reports(load_report(baseline), load_report(candidate))
            from firik_agent.benchmarks.reporters import markdown_comparison
            if args.output:
                if args.output.suffix.lower() == ".json":
                    # Preserve old file-output CLI and supply its Markdown companion.
                    output, stem = args.output.parent, args.output.stem
                else:
                    output, stem = args.output, "comparison"
                paths = write_outputs(output, result, markdown_comparison(result), stem=stem)
                print("\n".join(str(path.resolve()) for path in paths))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return 1 if result["regressions"] or result["fixture_pass_delta"] < 0 else 0
        from firik_agent.benchmarks.models import HarnessConfiguration, ModelProviderIdentity
        configuration = HarnessConfiguration.model_validate(read_json(args.profile, MAX_MANIFEST_BYTES)) if args.profile else None
        if args.suite != "coding" and any((args.allow_live, args.provider, args.model, args.config)):
            raise ValueError("Live provider options require --suite coding.")
        if args.output is None:
            from uuid import uuid4
            args.output = Path.cwd() / "harness-results" / uuid4().hex
        if any((args.output / name).exists() or (args.output / name).is_symlink() for name in ("report.json", "report.md")):
            raise FileExistsError("Output reports already exist; use a new output directory.")
        if args.suite == "coding":
            if not args.allow_live:
                raise ValueError("Coding evaluation requires explicit --allow-live; no model requests were made.")
            if args.manifest:
                raise ValueError("Coding suite tasks are supplied by the versioned suite contract.")
            if not args.provider or not args.model:
                raise ValueError("Coding evaluation requires explicit --provider and --model.")
            from firik_agent.benchmarks.coding import coding_suite
            from firik_agent.benchmarks.live import run_live_suite
            suite = coding_suite()
            if args.task:
                selected = set(args.task)
                if selected - {task.id for task in suite.tasks}:
                    raise ValueError("Unknown coding task ID.")
                suite = suite.model_copy(update={"tasks": [task for task in suite.tasks if task.id in selected]})
            configuration = configuration or HarnessConfiguration(id="native-live", model=ModelProviderIdentity(provider=args.provider, model=args.model))
            if configuration.model.provider != args.provider or configuration.model.model != args.model:
                raise ValueError("Profile model identity disagrees with explicit provider/model options.")
            report = asyncio.run(run_live_suite(suite, configuration=configuration, allow_live=True,
                                                config_path=str(args.config) if args.config else None, trials=args.trials))
        else:
            from firik_agent.benchmarks.fixtures import offline_tasks
            catalog = {task.id: task for task in (builtin_tasks() if args.suite == "legacy" else offline_tasks())}
            if set(args.task) - catalog.keys():
                raise ValueError("Unknown bundled task ID.")
            tasks = [catalog[name] for name in dict.fromkeys(args.task)] if args.task else ([] if args.manifest else list(catalog.values()))
            tasks += [TaskManifest.model_validate(read_json(path, MAX_MANIFEST_BYTES)) for path in args.manifest]
            kwargs = {"trials": args.trials, "label": args.label}
            if configuration is not None:
                kwargs["configuration"] = configuration
            report = asyncio.run(run_suite(tasks, **kwargs))
        report = enrich_report(report)
        paths = export_report(report, args.output)
        print(f"{report['summary']['fixtures_passed']}/{report['summary']['trials']} objective fixture checks passed.")
        print(report.get("method", LABEL))
        print("\n".join(str(path.resolve()) for path in paths))
        return 0 if report["summary"]["fixtures_passed"] == report["summary"]["trials"] else 1
    except KeyboardInterrupt:
        return 130
    except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
        from firik_agent.projects.coding_sessions import redact_sensitive
        print(f"Benchmark error ({type(error).__name__}): {redact_sensitive(str(error))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
