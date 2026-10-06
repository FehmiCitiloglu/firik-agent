"""Bounded, local, crash-restorable source checkpoints and revision-bound review.

Git and non-Git projects use the same private SQLite snapshot. No stash, reset,
index or branch operation is involved, so pre-existing user changes are retained.
Secret-bearing files are excluded instead of saving redacted, non-restorable text.
Every destructive operation first commits a backup and then publishes each file
atomically. A crash can leave a partial multi-file restore; its committed backup
and operation record remain available without automatically touching the workspace.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import stat
import threading
import uuid
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from firik_agent.projects.coding_sessions import redact_sensitive
from firik_agent.projects.indexer import (
    IGNORED_DIRS,
    IGNORED_EXTENSIONS,
    is_sensitive_file,
    safe_project_path,
)
from firik_agent.projects.workspace import WorkspaceSnapshot, compare_workspaces

if TYPE_CHECKING:
    from firik_agent.projects.db import ProjectDatabase


MAX_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_FILES = 2000
MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024


def _hash(content: str | None) -> str | None:
    return hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None


def _revision(*values: Any) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointConflict(ValueError):
    """Workspace changed after review; the backup identifies any reversible work."""

    def __init__(self, message: str, backup_checkpoint_id: str | None = None):
        super().__init__(message)
        self.backup_checkpoint_id = backup_checkpoint_id


class CheckpointStore:
    """Project/run-scoped persistence using the project's existing database lock."""

    def __init__(self, db: ProjectDatabase, *, known_secrets: tuple[str, ...] = ()):
        self.db = db
        self._known_secrets = known_secrets
        self._operation_lock = threading.RLock()
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS coding_checkpoints (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        run_id TEXT REFERENCES coding_sessions(id) ON DELETE CASCADE,
                        root_key TEXT NOT NULL,
                        label TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        snapshot TEXT NOT NULL,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS coding_checkpoints_scope
                        ON coding_checkpoints(project_id, run_id, created_at);
                    CREATE TABLE IF NOT EXISTS coding_checkpoint_reviews (
                        checkpoint_id TEXT NOT NULL REFERENCES coding_checkpoints(id) ON DELETE CASCADE,
                        path TEXT NOT NULL,
                        revision TEXT NOT NULL,
                        hunk_id TEXT NOT NULL DEFAULT '',
                        decision TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(checkpoint_id, path, revision, hunk_id)
                    );
                    CREATE TABLE IF NOT EXISTS coding_checkpoint_operations (
                        id TEXT PRIMARY KEY,
                        checkpoint_id TEXT NOT NULL REFERENCES coding_checkpoints(id) ON DELETE CASCADE,
                        backup_checkpoint_id TEXT NOT NULL REFERENCES coding_checkpoints(id) ON DELETE CASCADE,
                        status TEXT NOT NULL,
                        paths TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                # Existing local checkpoint stores can be reopened after an app update.
                if "metadata" not in {r["name"] for r in conn.execute("PRAGMA table_info(coding_checkpoints)")}:
                    conn.execute("ALTER TABLE coding_checkpoints ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
                conn.commit()
            finally:
                conn.close()

    def _root(self, root: str | Path) -> Path:
        root = Path(root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Proje klasörü bulunamadı.")
        safe_project_path(root, ".")
        return root

    def _scope(self, conn, project_id: str, run_id: str | None = None, root: Path | None = None) -> None:
        project = conn.execute("SELECT local_path FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise ValueError("Proje bulunamadı.")
        if root is not None and project["local_path"] and Path(project["local_path"]).expanduser().resolve() != root:
            raise ValueError("Checkpoint başka bir proje klasörüne ait.")
        if run_id is not None:
            row = conn.execute("SELECT project_id FROM coding_sessions WHERE id=?", (run_id,)).fetchone()
            if row is None or row["project_id"] != project_id:
                raise ValueError("Çalışma bu projeye ait değil.")

    def _path(self, root: Path, raw: str) -> Path:
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise ValueError("Geçerli bir proje dosyası belirtin.")
        path = safe_project_path(root, raw)
        relative = path.relative_to(root)
        if not relative.parts or any(p in IGNORED_DIRS for p in relative.parts):
            raise ValueError("Bu dosya checkpoint kapsamına alınamaz.")
        if path.suffix.lower() in IGNORED_EXTENSIONS:
            raise ValueError("İkili dosyalar checkpoint kapsamına alınamaz.")
        if redact_sensitive(relative.as_posix(), self._known_secrets) != relative.as_posix():
            raise ValueError("Hassas dosya adı checkpoint kapsamına alınamaz.")
        return path

    @contextmanager
    def _parent(self, root: Path, path: Path, *, create: bool = False):
        """Anchor each component using no-follow directory descriptors where supported."""
        fds: list[int] = []
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                fds.append(parent)
                for part in path.relative_to(root).parts[:-1]:
                    if create:
                        try:
                            os.mkdir(part, mode=0o755, dir_fd=parent)
                        except FileExistsError:
                            pass
                    parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    fds.append(parent)
                yield parent
            else:
                self._path(root, str(path))
                if create:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._path(root, str(path))
                yield None
        finally:
            for fd in reversed(fds):
                os.close(fd)

    def _read(self, root: Path, relative: str) -> tuple[str | None, int | None]:
        path = self._path(root, relative)
        try:
            with self._parent(root, path) as parent:
                fd = os.open(path.name if parent is not None else path,
                             os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError("Yalnızca bağımsız normal dosyalar checkpoint kapsamına alınabilir.")
                    if info.st_size > MAX_FILE_BYTES:
                        raise ValueError("Dosya checkpoint boyut sınırını aşıyor.")
                    data = stream.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                        raise ValueError("İkili veya büyük dosya checkpoint kapsamına alınamaz.")
                    text = data.decode("utf-8")
                    if redact_sensitive(text, self._known_secrets) != text:
                        raise ValueError("Gizli bilgi içeren dosya checkpoint kapsamına alınamaz.")
                    return text, stat.S_IMODE(info.st_mode)
        except FileNotFoundError:
            return None, None

    def _capture(self, root: Path, paths: Iterable[str] | None = None,
                 *, max_files: int = MAX_SNAPSHOT_FILES, max_bytes: int = MAX_SNAPSHOT_BYTES) -> dict:
        snapshot: dict = {"files": {}, "modes": {}, "absent": [], "skipped": {}, "limited": False,
                          "targeted": paths is not None}
        total = 0
        max_files = max(1, min(int(max_files), MAX_SNAPSHOT_FILES))
        max_bytes = max(0, min(int(max_bytes), MAX_SNAPSHOT_BYTES))

        def candidates():
            if paths is not None:
                # Consume at most one beyond the bound, even if given an unbounded iterable.
                seen: set[str] = set()
                for raw in paths:
                    relative = self._path(root, raw).relative_to(root).as_posix()
                    if relative not in seen:
                        seen.add(relative)
                        yield relative
                        if len(seen) > max_files:
                            return
                return

            def walk_error(error):
                snapshot["limited"] = True

            for directory, dirs, files in os.walk(root, onerror=walk_error):
                dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")
                                 and not (Path(directory) / d).is_symlink())
                for name in sorted(files):
                    path = Path(directory) / name
                    if is_sensitive_file(path, project_root=root) or path.suffix.lower() in IGNORED_EXTENSIONS:
                        continue
                    yield path.relative_to(root).as_posix()

        for count, relative in enumerate(candidates(), 1):
            if count > max_files:
                snapshot["limited"] = True
                break
            try:
                content, mode = self._read(root, relative)
                if content is None:
                    snapshot["absent"].append(relative)
                    continue
                size = len(content.encode("utf-8"))
                if total + size > max_bytes:
                    snapshot["limited"] = True
                    snapshot["skipped"][relative] = "boyut sınırı"
                    continue
                snapshot["files"][relative] = content
                snapshot["modes"][relative] = mode
                total += size
            except (OSError, ValueError, UnicodeError):
                # Do not store the error: it could contain credentials or raw source data.
                snapshot["skipped"][str(redact_sensitive(relative, self._known_secrets))] = "güvenli kaynak metni değil"
        snapshot["byte_count"] = total
        return snapshot

    @staticmethod
    def _metadata(row, snapshot: dict) -> dict:
        return {"id": row["id"], "project_id": row["project_id"], "run_id": row["run_id"],
                "label": row["label"], "kind": row["kind"], "created_at": row["created_at"],
                "file_count": len(snapshot["files"]), "byte_count": snapshot["byte_count"],
                "limited": snapshot["limited"], "skipped": list(snapshot["skipped"]),
                "backend": "local_snapshot"}

    def create_checkpoint(self, project_id: str, root: str | Path, *, run_id: str | None = None,
                          label: str = "", kind: str = "manual", paths: Iterable[str] | None = None,
                          max_files: int = MAX_SNAPSHOT_FILES, max_bytes: int = MAX_SNAPSHOT_BYTES) -> dict:
        root = self._root(root)
        # Verify identity before reading the workspace; repeat it inside the transaction.
        conn = self.db._get_connection()
        try:
            self._scope(conn, project_id, run_id, root)
        finally:
            conn.close()
        snapshot = self._capture(root, paths, max_files=max_files, max_bytes=max_bytes)
        row = {"id": f"checkpoint_{uuid.uuid4().hex}", "project_id": project_id, "run_id": run_id,
               "root_key": _revision(str(root)), "label": str(redact_sensitive(label, self._known_secrets))[:300],
               "kind": str(redact_sensitive(kind, self._known_secrets))[:80], "created_at": _now()}
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                self._scope(conn, project_id, run_id, root)
                conn.execute("INSERT INTO coding_checkpoints "
                             "(id, project_id, run_id, root_key, label, kind, created_at, snapshot, metadata) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (*row.values(), json.dumps(snapshot, ensure_ascii=False),
                              json.dumps(self._metadata(row, snapshot), ensure_ascii=False)))
                conn.commit()
            finally:
                conn.close()
        return self._metadata(row, snapshot)

    def list_checkpoints(self, project_id: str, *, run_id: str | None = None, limit: int = 50) -> list[dict]:
        conn = self.db._get_connection()
        try:
            self._scope(conn, project_id, run_id)
            clauses, params = ["project_id=?"], [project_id]
            if run_id is not None:
                clauses.append("run_id=?")
                params.append(run_id)
            rows = conn.execute(f"SELECT id, metadata FROM coding_checkpoints WHERE {' AND '.join(clauses)} "
                                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                                (*params, max(1, min(int(limit), 100)))).fetchall()
            result = []
            for row in rows:
                metadata = json.loads(row["metadata"])
                if not metadata:
                    # Parse pre-migration snapshots individually, never fetch a page of raw source.
                    old_row = conn.execute("SELECT * FROM coding_checkpoints WHERE id=?", (row["id"],)).fetchone()
                    metadata = self._metadata(old_row, json.loads(old_row["snapshot"]))
                result.append(metadata)
            return result
        finally:
            conn.close()

    def _get(self, checkpoint_id: str, project_id: str, root: Path, run_id: str | None = None):
        conn = self.db._get_connection()
        try:
            self._scope(conn, project_id, run_id, root)
            row = conn.execute("SELECT * FROM coding_checkpoints WHERE id=? AND project_id=?",
                               (checkpoint_id, project_id)).fetchone()
            if row is None or (run_id is not None and row["run_id"] != run_id):
                raise ValueError("Checkpoint bu proje ve çalışmaya ait değil.")
            if row["root_key"] != _revision(str(root)):
                raise ValueError("Checkpoint başka bir proje klasörüne ait.")
            snapshot = json.loads(row["snapshot"])
            return dict(row), snapshot
        finally:
            conn.close()

    @staticmethod
    def _workspace(snapshot: dict) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(files=snapshot["files"], skipped=set(snapshot["skipped"]),
                                 limited=snapshot["limited"])

    @staticmethod
    def _hunks(path: str, before: str | None, after: str | None, revision: str) -> list[dict]:
        old, new = (before or "").splitlines(keepends=True), (after or "").splitlines(keepends=True)
        groups = list(difflib.SequenceMatcher(None, old, new).get_grouped_opcodes(3))
        hunks = []
        for group in groups:
            a, b, c, d = group[0][1], group[-1][2], group[0][3], group[-1][4]
            lines = [f"@@ -{a + 1},{b - a} +{c + 1},{d - c} @@"]
            for tag, i, j, k, l in group:
                if tag == "equal":
                    lines.extend(" " + line.rstrip("\n") for line in old[i:j])
                else:
                    lines.extend("-" + line.rstrip("\n") for line in old[i:j])
                    lines.extend("+" + line.rstrip("\n") for line in new[k:l])
            hunks.append({"id": _revision(path, revision, a, b, c, d), "old_start": a + 1,
                          "old_count": b - a, "new_start": c + 1, "new_count": d - c,
                          "diff": "\n".join(lines[:2000]), "truncated": len(lines) > 2000,
                          "decision": "pending"})
        return hunks

    def compare_checkpoint(self, checkpoint_id: str, *, project_id: str, root: str | Path,
                           run_id: str | None = None) -> dict:
        root = self._root(root)
        row, before = self._get(checkpoint_id, project_id, root, run_id)
        paths = sorted(before["files"].keys() | set(before["absent"])) if before["targeted"] else None
        after = self._capture(root, paths)
        changes = compare_workspaces(self._workspace(before), self._workspace(after))
        conn = self.db._get_connection()
        try:
            reviews = {(r["path"], r["revision"], r["hunk_id"]): r["decision"] for r in conn.execute(
                "SELECT * FROM coding_checkpoint_reviews WHERE checkpoint_id=?", (checkpoint_id,))}
        finally:
            conn.close()
        for change in changes:
            path = change["path"]
            old, new = before["files"].get(path), after["files"].get(path)
            revision = _revision(path, _hash(old), _hash(new))
            change.update(before_hash=_hash(old), after_hash=_hash(new), revision=revision,
                          decision=reviews.get((path, revision, ""), "pending"),
                          hunks=self._hunks(path, old, new, revision))
            for hunk in change["hunks"]:
                hunk["decision"] = reviews.get((path, revision, hunk["id"]), change["decision"])
        return {"checkpoint": self._metadata(row, before), "changes": changes,
                "revision": _revision([(c["path"], c["revision"]) for c in changes]),
                "expected_hashes": {c["path"]: c["after_hash"] for c in changes},
                "limited": before["limited"] or after["limited"],
                "skipped": sorted(set(before["skipped"]) | set(after["skipped"]))}

    def _replace(self, root: Path, relative: str, content: str | None, expected_hash: str | None,
                 mode: int | None = None) -> None:
        path = self._path(root, relative)
        current, current_mode = self._read(root, relative)
        if _hash(current) != expected_hash:
            raise CheckpointConflict("Dosya incelemeden sonra değişti; kullanıcı düzenlemesi korunuyor.")
        if content is None and current is None:
            return
        with self._parent(root, path, create=content is not None) as parent:
            target = path.name if parent is not None else path
            if content is None:
                # Revalidate immediately before deleting, including hardlink/symlink checks.
                if _hash(self._read(root, relative)[0]) != expected_hash:
                    raise CheckpointConflict("Dosya bu sırada değişti; silme durduruldu.")
                os.unlink(target, dir_fd=parent)
            else:
                name = f".firik-checkpoint-{uuid.uuid4().hex}.tmp"
                temporary = name if parent is not None else path.parent / name
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                             0o600, dir_fd=parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        if hasattr(os, "fchmod"):
                            os.fchmod(stream.fileno(), (current_mode if current_mode is not None else mode or 0o644) & 0o777)
                        stream.write(content.encode("utf-8"))
                        stream.flush()
                        os.fsync(stream.fileno())
                    if expected_hash is None:
                        # Publish without replacing a racing creator of a previously absent file.
                        os.link(temporary, target, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    else:
                        if _hash(self._read(root, relative)[0]) != expected_hash:
                            raise CheckpointConflict("Dosya bu sırada değişti; değiştirme durduruldu.")
                        os.replace(temporary, target, src_dir_fd=parent, dst_dir_fd=parent)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
            if parent is not None:
                os.fsync(parent)

    def _operation(self, checkpoint_id: str, backup_id: str, paths: Iterable[str]) -> str:
        operation_id = f"restore_{uuid.uuid4().hex}"
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("INSERT INTO coding_checkpoint_operations VALUES (?, ?, ?, 'applying', ?, ?, ?)",
                             (operation_id, checkpoint_id, backup_id, json.dumps(list(paths)), _now(), _now()))
                conn.commit()
            finally:
                conn.close()
        return operation_id

    def _operation_status(self, operation_id: str, status: str) -> None:
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("UPDATE coding_checkpoint_operations SET status=?, updated_at=? WHERE id=?",
                             (status, _now(), operation_id))
                conn.commit()
            finally:
                conn.close()

    def _apply(self, checkpoint_id: str, project_id: str, root: Path, run_id: str | None,
               targets: dict[str, str | None], expected_hashes: Mapping[str, str | None], modes: dict) -> dict:
        current = {}
        for path in targets:
            if path not in expected_hashes:
                raise ValueError("Geri yükleme için incelenen her dosyanın sürüm özeti gerekiyor.")
            content, mode = self._read(root, path)
            if _hash(content) != expected_hashes[path]:
                raise CheckpointConflict("Dosya incelemeden sonra değişti; kullanıcı düzenlemesi korunuyor.")
            current[path] = (content, mode)
        if not targets:
            return {"restored": [], "checkpoint_id": checkpoint_id, "backup_checkpoint_id": None}
        backup = self.create_checkpoint(project_id, root, run_id=run_id, label="Geri yükleme öncesi",
                                        kind="before_restore", paths=sorted(targets))
        if backup["limited"] or backup["skipped"]:
            raise ValueError("Güvenli geri dönüş checkpoint'i tamamlanamadı; dosyalar değiştirilmedi.")
        # Backup capture itself must still match the exact reviewed content.
        _, backup_snapshot = self._get(backup["id"], project_id, root, run_id)
        if any(_hash(backup_snapshot["files"].get(p)) != expected_hashes[p] for p in targets):
            raise CheckpointConflict("Dosya checkpoint sırasında değişti; işlem durduruldu.", backup["id"])
        operation_id = self._operation(checkpoint_id, backup["id"], targets)
        applied = []
        try:
            for path, content in targets.items():
                self._replace(root, path, content, expected_hashes[path], modes.get(path))
                applied.append(path)
        except Exception as exc:
            # Roll back only bytes still matching our own publication. Preserve any racing user edit.
            rolled_back = True
            for path in reversed(applied):
                try:
                    self._replace(root, path, current[path][0], _hash(targets[path]), current[path][1])
                except Exception:  # noqa: BLE001 -- rollback must record recovery needs for any publisher failure
                    rolled_back = False
            self._operation_status(operation_id, "rolled_back" if rolled_back else "needs_recovery")
            raise CheckpointConflict("Geri yükleme tamamlanamadı; önceki durum checkpoint'te korunuyor.",
                                     backup["id"]) from exc
        self._operation_status(operation_id, "completed")
        return {"restored": applied, "checkpoint_id": checkpoint_id, "backup_checkpoint_id": backup["id"],
                "operation_id": operation_id}

    def restore_checkpoint(self, checkpoint_id: str, *, project_id: str, root: str | Path,
                           expected_hashes: Mapping[str, str | None], run_id: str | None = None,
                           paths: Iterable[str] | None = None) -> dict:
        with self._operation_lock:
            root = self._root(root)
            _, snapshot = self._get(checkpoint_id, project_id, root, run_id)
            comparison = self.compare_checkpoint(checkpoint_id, project_id=project_id, root=root, run_id=run_id)
            changes = {c["path"]: c for c in comparison["changes"]}
            # Restore only the concrete preview the caller reviewed. A newly created,
            # unrelated user file must not silently become part of this operation.
            selected = list(expected_hashes) if paths is None else list(dict.fromkeys(paths))
            if any(p not in changes for p in selected):
                raise ValueError("Geri yükleme yalnızca incelenebilir checkpoint farklarına uygulanabilir.")
            targets = {path: snapshot["files"].get(path) for path in selected}
            return self._apply(checkpoint_id, project_id, root, run_id, targets, expected_hashes, snapshot["modes"])

    def review_change(self, checkpoint_id: str, *, project_id: str, root: str | Path, path: str,
                      decision: str, revision: str, hunk_id: str | None = None,
                      run_id: str | None = None) -> dict:
        if decision not in {"accepted", "rejected"}:
            raise ValueError("İnceleme kararı accepted veya rejected olmalıdır.")
        with self._operation_lock:
            root = self._root(root)
            path = self._path(root, path).relative_to(root).as_posix()
            _, snapshot = self._get(checkpoint_id, project_id, root, run_id)
            comparison = self.compare_checkpoint(checkpoint_id, project_id=project_id, root=root, run_id=run_id)
            change = next((c for c in comparison["changes"] if c["path"] == path), None)
            if change is None or change["revision"] != revision:
                raise CheckpointConflict("Fark incelemeden sonra değişti; yeni sürümü inceleyin.")
            hunk = next((h for h in change["hunks"] if h["id"] == hunk_id), None) if hunk_id else None
            if hunk_id and (hunk is None or hunk["truncated"]):
                raise ValueError("Hunk bulunamadı veya tam olarak görüntülenemiyor.")
            result: dict = {"path": path, "decision": decision, "revision": revision, "hunk_id": hunk_id}
            carried_reviews: list[tuple[str, str]] = []
            if decision == "rejected":
                before = snapshot["files"].get(path)
                if hunk and before is not None and change["after_hash"] is not None:
                    current, _ = self._read(root, path)
                    if _hash(current) != change["after_hash"]:
                        raise CheckpointConflict("Dosya incelemeden sonra değişti; kullanıcı düzenlemesi korunuyor.")
                    old, new = before.splitlines(keepends=True), current.splitlines(keepends=True)
                    a, b = hunk["old_start"] - 1, hunk["new_start"] - 1
                    target = "".join(new[:b] + old[a:a + hunk["old_count"]] + new[b + hunk["new_count"]:])
                else:
                    target = before
                result.update(self._apply(checkpoint_id, project_id, root, run_id, {path: target},
                                          {path: change["after_hash"]}, snapshot["modes"]))
                if hunk:
                    # Our own rejection changes the file revision. Retain acceptance
                    # only for uniquely matching, fully visible unaffected hunks.
                    updated = self.compare_checkpoint(checkpoint_id, project_id=project_id, root=root, run_id=run_id)
                    remaining = next((c for c in updated["changes"] if c["path"] == path), None)
                    if remaining:
                        def fingerprint(item):
                            return item["old_count"], item["new_count"], item["diff"].partition("\n")[2]
                        originals = [h for h in change["hunks"] if h["id"] != hunk_id and not h["truncated"]]
                        for candidate in remaining["hunks"]:
                            matching = [h for h in originals if fingerprint(h) == fingerprint(candidate)]
                            duplicates = [h for h in remaining["hunks"] if fingerprint(h) == fingerprint(candidate)]
                            if (not candidate["truncated"] and len(matching) == len(duplicates) == 1
                                    and matching[0]["decision"] == "accepted"):
                                carried_reviews.append((remaining["revision"], candidate["id"]))
            elif _hash(self._read(root, path)[0]) != change["after_hash"]:
                raise CheckpointConflict("Dosya incelemeden sonra değişti; yeni sürümü inceleyin.")
            with self.db._lock:
                conn = self.db._get_connection()
                try:
                    conn.execute("INSERT OR REPLACE INTO coding_checkpoint_reviews VALUES (?, ?, ?, ?, ?, ?)",
                                 (checkpoint_id, path, revision, hunk_id or "", decision, _now()))
                    for new_revision, new_hunk_id in carried_reviews:
                        conn.execute("INSERT OR REPLACE INTO coding_checkpoint_reviews VALUES (?, ?, ?, ?, ?, ?)",
                                     (checkpoint_id, path, new_revision, new_hunk_id, "accepted", _now()))
                    conn.commit()
                finally:
                    conn.close()
            return result

    def list_operations(self, project_id: str, *, run_id: str | None = None) -> list[dict]:
        """Expose interrupted restores without performing implicit recovery on open."""
        conn = self.db._get_connection()
        try:
            self._scope(conn, project_id, run_id)
            clause = " AND c.run_id=?" if run_id is not None else ""
            rows = conn.execute("SELECT o.* FROM coding_checkpoint_operations o JOIN coding_checkpoints c "
                                "ON c.id=o.checkpoint_id WHERE c.project_id=?" + clause +
                                " ORDER BY o.created_at DESC LIMIT 100",
                                (project_id, run_id) if run_id is not None else (project_id,)).fetchall()
            return [{**dict(row), "paths": json.loads(row["paths"])} for row in rows]
        finally:
            conn.close()
