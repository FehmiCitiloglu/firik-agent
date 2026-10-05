"""Lazy project navigation backed by existing symbol metadata."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any, Iterable

from evren_agent.projects.indexer import (
    CodebaseIndexer, IGNORED_DIRS, IGNORED_EXTENSIONS, is_sensitive_file,
    read_project_text, safe_project_path,
)


class ProjectExplorer:
    """Lists only requested children; Git status is supplied by the caller."""

    def __init__(self, project_root: str | Path,
                 indexed_data: Iterable[dict[str, Any]] | None = None,
                 ignored_dirs: Iterable[str] | None = None, show_hidden: bool = False,
                 git_status: dict[str, str] | None = None):
        self.root = Path(project_root).resolve()
        self.index = {entry["file_path"]: entry for entry in (indexed_data or [])}
        self.ignored_dirs = set(IGNORED_DIRS if ignored_dirs is None else ignored_dirs)
        self.show_hidden = show_hidden
        self.git_status = git_status or {}
        self.limited = False

    def _visible(self, relative: str, is_dir: bool) -> bool:
        path = Path(relative)
        if is_sensitive_file(path, project_root=self.root) or any(p in {".git", ".svn", ".hg"} for p in path.parts):
            return False
        if not self.show_hidden and any(p.startswith(".") for p in path.parts):
            return False
        directory_parts = path.parts if is_dir else path.parts[:-1]
        return not any(fnmatch.fnmatch(part, pattern) or fnmatch.fnmatch(relative, pattern)
                       for part in directory_parts for pattern in self.ignored_dirs)

    def children(self, relative: str = "", limit: int = 500) -> list[dict[str, Any]]:
        directory = safe_project_path(self.root, relative)
        if not directory.is_dir():
            raise ValueError("Proje klasörü bulunamadı.")
        if relative and not self._visible(directory.relative_to(self.root).as_posix(), True):
            return []
        result = []
        self.limited = False
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                try:
                    child = safe_project_path(self.root, entry.path)
                    rel = child.relative_to(self.root).as_posix()
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if not self._visible(rel, is_dir):
                        continue
                    metadata = self.index.get(rel, {})
                    result.append({"path": rel, "name": entry.name, "is_dir": is_dir,
                                   "indexed": bool(metadata), "symbols": metadata.get("symbols", []),
                                   "git_status": self.git_status.get(rel, "")})
                except (ValueError, OSError):
                    continue
                if len(result) >= max(1, limit):
                    self.limited = True
                    break
        return sorted(result, key=lambda item: (not item["is_dir"], item["name"].casefold()))

    def search(self, query: str, content: bool = False, limit: int = 50,
               max_files: int = 1500, max_bytes: int = 4 * 1024 * 1024) -> list[dict[str, Any]]:
        """Search names/symbols or content within a bounded source sample."""
        query = query.strip().casefold()
        if not query or limit <= 0:
            return []
        results: list[dict[str, Any]] = []
        visited = 0
        bytes_read = 0
        self.limited = False
        # Indexed paths are candidates first, then a bounded walk includes newly created files.
        candidates = dict.fromkeys(self.index)
        for directory, dirs, files in os.walk(self.root):
            dirs[:] = sorted(name for name in dirs
                             if not (Path(directory) / name).is_symlink()
                             and self._visible((Path(directory) / name).relative_to(self.root).as_posix(), True))
            for name in sorted(files):
                path = Path(directory) / name
                rel = path.relative_to(self.root).as_posix()
                if self._visible(rel, False) and not path.is_symlink():
                    candidates.setdefault(rel, None)
                    visited += 1
                    if visited >= max_files:
                        self.limited = True
                        break
            if self.limited:
                break
        for relative in list(candidates)[:max_files]:
            if not self._visible(relative, False):
                continue
            try:
                path = safe_project_path(self.root, relative)
                if not path.is_file():
                    continue
            except (ValueError, OSError):
                continue
            metadata = self.index.get(relative, {})
            symbols = metadata.get("symbols", [])
            matched = query in relative.casefold() or any(query in symbol.casefold() for symbol in symbols)
            line, preview = None, ""
            if content:
                remaining = min(64 * 1024, max_bytes - bytes_read)
                if remaining <= 0:
                    self.limited = True
                    break
                try:
                    text = read_project_text(self.root, relative, max_bytes=remaining)
                except (ValueError, OSError):
                    continue
                bytes_read += len(text.encode("utf-8"))
                for number, value in enumerate(text.splitlines(), 1):
                    if query in value.casefold():
                        line, preview = number, value[:240]
                        break
                matched = line is not None
            if matched:
                results.append({"path": relative, "file_path": relative, "name": path.name,
                "line": line, "preview": preview, "indexed": bool(metadata),
                                "symbols": symbols, "git_status": self.git_status.get(relative, "")})
                if len(results) >= limit:
                    self.limited = True
                    break
        return results
