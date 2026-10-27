"""Trusted declarative evaluator; repository code is always inert input data."""
from __future__ import annotations
import ast
from pathlib import Path
from firik_agent.projects.indexer import read_project_text


def evaluate_sources(root: Path, expected: dict[str, str]) -> tuple[bool, str]:
    try:
        for name, content in expected.items():
            observed = read_project_text(root, name, max_bytes=32_768, sanitize=False)
            if observed != content:
                return False, f"Unexpected source bytes: {name}"
            if name.endswith(".py"):
                ast.parse(observed, filename=name)
        # Count all visible files, including any unexpected side effects; .git
        # metadata is host-created and excluded from the expected source set.
        observed_paths = {path.relative_to(root).as_posix() for path in root.rglob("*")
                          if path.is_file() and ".git" not in path.relative_to(root).parts}
        if observed_paths != set(expected):
            return False, "Unexpected files were created or removed."
        return True, "Exact source bytes and Python syntax matched; repository code was not executed."
    except (OSError, ValueError, SyntaxError, MemoryError, RecursionError) as error:
        return False, f"Source evaluator failed: {type(error).__name__}"

