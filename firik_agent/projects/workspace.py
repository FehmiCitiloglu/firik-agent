"""Bounded source snapshots and before/after comparisons for coding runs."""
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import os
from pathlib import Path

from firik_agent.projects.indexer import IGNORED_DIRS, IGNORED_EXTENSIONS, is_sensitive_file, safe_project_path


@dataclass
class WorkspaceSnapshot:
    files: dict[str, str] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    limited: bool = False
    changes: list[dict] | None = field(default=None, repr=False)


def workspace_path(root: str | Path, relative_path: str) -> Path:
    root = Path(root).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Dosya proje klasörünün dışında.")
    return path


def capture_workspace(root: str | Path, max_files: int = 2000,
                      max_bytes: int = 20 * 1024 * 1024) -> WorkspaceSnapshot:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Proje klasörü bulunamadı.")
    safe_project_path(root, ".")
    snapshot = WorkspaceSnapshot()
    total_bytes = 0
    count = 0
    def on_walk_error(error):
        snapshot.limited = True

    for directory, dirs, files in os.walk(root, onerror=on_walk_error):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS
                         and not d.startswith(".") and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.is_symlink() or is_sensitive_file(path, project_root=root) or path.suffix.lower() in IGNORED_EXTENSIONS:
                continue
            relative = path.relative_to(root).as_posix()
            count += 1
            if count > max_files:
                snapshot.limited = True
                return snapshot
            try:
                size = path.stat().st_size
                if size > 1024 * 1024 or total_bytes + size > max_bytes:
                    snapshot.skipped.add(relative)
                    snapshot.limited = True
                    continue
                with path.open("rb") as stream:
                    data = stream.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024 or total_bytes + len(data) > max_bytes:
                    snapshot.skipped.add(relative)
                    snapshot.limited = True
                    continue
                if b"\x00" in data:
                    snapshot.skipped.add(relative)
                    continue
                snapshot.files[relative] = data.decode("utf-8")
                total_bytes += len(data)
            except (OSError, UnicodeDecodeError):
                snapshot.skipped.add(relative)
    return snapshot


def compare_workspaces(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[dict]:
    changes = []
    for path in sorted(before.files.keys() | after.files.keys()):
        if path in before.skipped or path in after.skipped:
            continue
        old = before.files.get(path)
        new = after.files.get(path)
        # A partial scan cannot establish that a missing file was added or deleted.
        if (old is None and before.limited) or (new is None and after.limited):
            continue
        if old == new:
            continue
        raw_lines = list(difflib.unified_diff(
            (old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
            fromfile=f"a/{path}" if old is not None else "/dev/null",
            tofile=f"b/{path}" if new is not None else "/dev/null", lineterm="",
        ))
        lines = []
        for index, line in enumerate(raw_lines):
            lines.append(line.rstrip("\n"))
            if index >= 2 and line.startswith(("+", "-", " ")) and not line.endswith("\n"):
                lines.append("\\ No newline at end of file")
        added = sum(line.startswith("+") for line in lines[2:])
        removed = sum(line.startswith("-") for line in lines[2:])
        diff = "\n".join(lines[:2000])
        if len(lines) > 2000:
            diff += "\n… Fark görünümü 2000 satırla sınırlandırıldı."
        if not diff:
            diff = "Boş dosya eklendi." if old is None else "Boş dosya silindi."
        changes.append({"path": path, "status": "added" if old is None else "deleted" if new is None else "modified",
                        "added": added, "removed": removed, "diff": diff})
    return changes
