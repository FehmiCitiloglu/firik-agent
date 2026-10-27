"""Bounded versioned result IO, with existing coding-session redaction."""
from __future__ import annotations

import json
from pathlib import Path

from firik_agent.projects.coding_sessions import redact_sensitive

MAX_REPORT_BYTES = 64 * 1024 * 1024


def read_json(path: Path, limit: int = MAX_REPORT_BYTES):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key.")
            result[key] = value
        return result
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input file exceeds the size limit.")
    try:
        return json.loads(data, object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number.")))
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Invalid benchmark JSON.") from error


def load_report(path: Path) -> dict:
    from firik_agent.benchmarks.comparison import validate_report
    report = read_json(path)
    validate_report(report)
    return report


def write_outputs(output: Path, payload: dict, markdown: str, *, stem="report", known_secrets=()) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    paths = output / f"{stem}.json", output / f"{stem}.md"
    if any(path.exists() or path.is_symlink() for path in paths):
        raise FileExistsError("Output reports already exist; use a new output directory.")
    contents = (json.dumps(redact_sensitive(payload, known_secrets), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                redact_sensitive(markdown, known_secrets))
    created = []
    try:
        for path, content in zip(paths, contents):
            with path.open("x", encoding="utf-8") as stream:
                created.append(path)
                stream.write(content)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return paths


def export_report(report: dict, output: Path) -> tuple[Path, Path]:
    from firik_agent.benchmarks.comparison import validate_report
    from firik_agent.benchmarks.reporters import markdown_report
    validate_report(report)
    return write_outputs(output, report, markdown_report(report))
