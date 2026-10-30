"""Bounded source snapshots and before/after comparisons for coding runs."""
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import os
from pathlib import Path
import stat

from firik_agent.projects.indexer import CodebaseIndexer, IGNORED_DIRS, IGNORED_EXTENSIONS, is_sensitive_file, safe_project_path


@dataclass
class WorkspaceSnapshot:
    files: dict[str, str] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    limited: bool = False
    changes: list[dict] | None = field(default=None, repr=False)
    omitted: dict[str, str] = field(default_factory=dict)
    ignored_paths: set[str] = field(default_factory=set)
    limit_reasons: set[str] = field(default_factory=set)
    read_errors: set[str] = field(default_factory=set)

    @property
    def inspection_incomplete(self) -> bool:
        """Intentional non-source omissions do not indicate a failed source scan.

        Keep omitted paths in ``skipped`` so a file becoming binary or oversized
        between captures can never be mistaken for a deletion. Unknown skipped
        paths from older checkpoint snapshots remain conservatively incomplete.
        """
        return self.limited or bool(self.skipped - self.omitted.keys())


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
    indexer = CodebaseIndexer(root)
    total_bytes = 0
    count = 0
    def on_walk_error(error):
        snapshot.limited = True
        snapshot.limit_reasons.add("directories")

    for directory, dirs, files in os.walk(root, onerror=on_walk_error):
        directory = Path(directory)
        indexer._load_ignore(directory)
        visible_dirs = []
        for name in sorted(dirs):
            if name in IGNORED_DIRS or name.startswith(".") or (directory / name).is_symlink():
                continue
            relative = (directory / name).relative_to(root).as_posix()
            if indexer.repository_ignored(relative, is_dir=True):
                snapshot.ignored_paths.add(relative)
                continue
            visible_dirs.append(name)
        dirs[:] = visible_dirs
        for name in sorted(files):
            path = directory / name
            if path.is_symlink() or is_sensitive_file(path, project_root=root) or path.suffix.lower() in IGNORED_EXTENSIONS:
                continue
            relative = path.relative_to(root).as_posix()
            if indexer.repository_ignored(relative):
                snapshot.ignored_paths.add(relative)
                continue
            count += 1
            if count > max_files:
                snapshot.limited = True
                snapshot.limit_reasons.add("files")
                return snapshot
            try:
                info = path.stat()
                if not stat.S_ISREG(info.st_mode):
                    snapshot.skipped.add(relative)
                    snapshot.omitted[relative] = "non_regular"
                    continue
                if info.st_size > 1024 * 1024:
                    snapshot.skipped.add(relative)
                    snapshot.omitted[relative] = "file_size"
                    continue
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        snapshot.skipped.add(relative)
                        snapshot.omitted[relative] = "non_regular"
                        continue
                    data = stream.read(1024 * 1024 + 1)
                if len(data) > 1024 * 1024:
                    snapshot.skipped.add(relative)
                    snapshot.omitted[relative] = "file_size"
                    continue
                if b"\x00" in data:
                    snapshot.skipped.add(relative)
                    snapshot.omitted[relative] = "binary"
                    continue
                try:
                    content = data.decode("utf-8")
                except UnicodeDecodeError:
                    snapshot.skipped.add(relative)
                    snapshot.omitted[relative] = "encoding"
                    continue
                if total_bytes + len(data) > max_bytes:
                    snapshot.skipped.add(relative)
                    snapshot.limited = True
                    snapshot.limit_reasons.add("bytes")
                    continue
                snapshot.files[relative] = content
                total_bytes += len(data)
            except OSError:
                snapshot.skipped.add(relative)
                snapshot.read_errors.add(relative)
    return snapshot


def compare_workspaces(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> list[dict]:
    changes = []
    ignored = before.ignored_paths | after.ignored_paths
    for path in sorted(before.files.keys() | after.files.keys()):
        if path in before.skipped or path in after.skipped:
            continue
        if any(path == entry or path.startswith(entry + "/") for entry in ignored):
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
