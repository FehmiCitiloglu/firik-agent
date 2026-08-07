"""Workspace-constrained file operations."""

from __future__ import annotations

import fnmatch
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .contracts import ToolError

DEFAULT_IGNORES = (
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".firik-agent",
    "venv",
    "node_modules",
    "target",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)


class Workspace:
    """Provides bounded filesystem access under one canonical root."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_read_bytes: int = 1_000_000,
        max_results: int = 500,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"Workspace is not a directory: {self.root}")
        self.max_read_bytes = max_read_bytes
        self.max_results = max_results
        self._protected = (self.root / ".firik-agent").resolve(strict=False)

    def resolve(
        self,
        path: str | Path = ".",
        *,
        must_exist: bool = False,
        writable: bool = False,
    ) -> Path:
        """Resolve a path and reject traversal or symlink escapes."""
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise ToolError(f"Path escapes workspace: {path}")
        if must_exist and not resolved.exists():
            raise ToolError(f"Path does not exist: {path}")
        if writable and (resolved == self._protected or resolved.is_relative_to(self._protected)):
            raise ToolError("The internal .firik-agent directory is managed by the workflow")
        return resolved

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix() or "."

    def list_files(self, pattern: str = "**/*", limit: int | None = None) -> list[str]:
        """List regular, non-ignored files matching a glob."""
        result_limit = min(limit or self.max_results, self.max_results)
        results: list[str] = []
        for path in self.root.glob(pattern):
            if len(results) >= result_limit:
                break
            relative = path.relative_to(self.root)
            if self._ignored(relative) or path.is_symlink() or not path.is_file():
                continue
            results.append(relative.as_posix())
        return sorted(results)

    def read_text(self, path: str, *, max_bytes: int | None = None) -> str:
        """Read bounded UTF-8 text from the workspace."""
        resolved = self.resolve(path, must_exist=True)
        if not resolved.is_file():
            raise ToolError(f"Not a file: {path}")
        byte_limit = min(max_bytes or self.max_read_bytes, self.max_read_bytes)
        size = resolved.stat().st_size
        if size > byte_limit:
            raise ToolError(f"File is {size} bytes; limit is {byte_limit} bytes")
        payload = resolved.read_bytes()
        if b"\x00" in payload[:8192]:
            raise ToolError(f"Binary files are not supported: {path}")
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"File is not valid UTF-8: {path}") from exc

    def write_text(self, path: str, content: str) -> dict[str, Any]:
        """Atomically write UTF-8 text within the workspace."""
        resolved = self.resolve(path, writable=True)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        before = resolved.read_text(encoding="utf-8") if resolved.exists() else None
        if before == content:
            return {
                "path": self.relative(resolved),
                "changed": False,
                "bytes": len(content.encode()),
            }

        fd, temporary_name = tempfile.mkstemp(prefix=f".{resolved.name}.", dir=resolved.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, resolved)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return {"path": self.relative(resolved), "changed": True, "bytes": len(content.encode())}

    def replace_text(self, path: str, old: str, new: str, *, count: int = 1) -> dict[str, Any]:
        """Replace an exact, uniquely identified text fragment."""
        if not old:
            raise ToolError("old text must not be empty")
        content = self.read_text(path)
        matches = content.count(old)
        if matches == 0:
            raise ToolError("old text was not found")
        if count < 1:
            raise ToolError("count must be at least 1")
        if matches < count:
            raise ToolError(f"requested {count} replacements but found {matches}")
        result = self.write_text(path, content.replace(old, new, count))
        result["replacements"] = count
        return result

    def search(
        self,
        pattern: str,
        *,
        glob: str = "**/*",
        regex: bool = False,
        case_sensitive: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Search text files and return line-based evidence."""
        if not pattern:
            raise ToolError("Search pattern must not be empty")
        flags = 0 if case_sensitive else re.IGNORECASE
        expression = re.compile(pattern if regex else re.escape(pattern), flags)
        results: list[dict[str, Any]] = []
        result_limit = min(max(limit, 1), self.max_results)
        for relative in self.list_files(glob, limit=self.max_results):
            try:
                text = self.read_text(relative)
            except ToolError:
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if expression.search(line):
                    results.append({"path": relative, "line": line_number, "text": line[:500]})
                    if len(results) >= result_limit:
                        return results
        return results

    @staticmethod
    def _ignored(relative: Path) -> bool:
        return any(
            part in DEFAULT_IGNORES or fnmatch.fnmatch(part, "*.egg-info")
            for part in relative.parts
        )
