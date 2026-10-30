"""Read-only recovery inspection and explicitly approved, non-replaying continuation.

The controller uses the existing project database, checkpoints and event stream.
No method here invokes a model, shell command with side effects, MCP operation,
patch, child agent or checkpoint restore. Git observations are read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Mapping
import uuid

from firik_agent.projects.checkpoints import CheckpointStore
from firik_agent.projects.coding_sessions import (
    ACTIVE_STATUSES, CodingSessionStore, redact_sensitive,
)
from firik_agent.projects.execution_journal import ExecutionJournal
from firik_agent.projects.indexer import IGNORED_DIRS, safe_project_path
from firik_agent.projects.models import CodingSession


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _git_observation(root: Path) -> dict[str, Any]:
    def read(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                               "-c", "core.untrackedCache=false", "-C", str(root), *args],
                              capture_output=True, timeout=5, check=False,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    try:
        check = read("rev-parse", "--show-toplevel")
        if check.returncode:
            # Distinguish an ordinary directory from an inaccessible repository.
            return {"is_repository": False, "complete": not (root / ".git").exists()}
        top = Path(check.stdout.decode().strip()).resolve()
        head = read("rev-parse", "--verify", "HEAD")
        branch = read("symbolic-ref", "--quiet", "HEAD")
        status = read("status", "--porcelain=v1", "--untracked-files=all", "-z")
        index = read("diff", "--cached", "--raw", "--no-renames", "--no-ext-diff")
        return {"is_repository": True, "root": str(top),
                "head": head.stdout.decode().strip() if head.returncode == 0 else "unborn",
                "branch": branch.stdout.decode().strip() if branch.returncode == 0 else "detached",
                "status_hash": hashlib.sha256(status.stdout).hexdigest(),
                "index_hash": hashlib.sha256(index.stdout).hexdigest(),
                "dirty": bool(status.stdout), "complete": status.returncode == index.returncode == 0}
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return {"is_repository": (root / ".git").exists(), "complete": False}


def capture_workspace(root: str | Path, *, max_files: int = 2000,
                      max_bytes: int = 20 * 1024 * 1024) -> dict[str, Any]:
    """Bounded file hashes, including dirty/untracked files; never source text.

    Sensitive filenames are hashed as opaque keys and their contents are only
    hashed in memory. A partial, racing or unreadable capture blocks resume.
    Ignored dependency/build directories are outside this safety manifest.
    """
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Proje klasörü bulunamadı.")
    safe_project_path(root, ".")
    files: dict[str, Any] = {}
    complete, total, count = True, 0, 0
    def walk_error(_error):
        nonlocal complete
        complete = False
    for directory, dirs, names in os.walk(root, onerror=walk_error, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and d != ".git")
        for dirname in list(dirs):
            if (Path(directory) / dirname).is_symlink():
                complete = False
                dirs.remove(dirname)
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            safe_key = (redact_sensitive(relative) == relative
                        and redact_sensitive({relative: "hash-marker"}).get(relative) == "hash-marker")
            key = relative if safe_key else "opaque:" + _digest(relative)
            count += 1
            if count > max_files:
                complete = False
                break
            try:
                before = path.lstat()
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    complete = False
                    files[key] = {"unsupported": True}
                    continue
                if before.st_size + total > max_bytes:
                    complete = False
                    files[key] = {"limited": True}
                    continue
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (info.st_ino, info.st_dev) != (before.st_ino, before.st_dev):
                        complete = False
                        continue
                    data = stream.read(max_bytes - total + 1)
                    after = os.fstat(stream.fileno())
                published = path.lstat()
                total += len(data)
                if (len(data) > max_bytes or total > max_bytes or
                        (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size) or
                        (published.st_ino, published.st_dev, published.st_mtime_ns, published.st_size)
                        != (after.st_ino, after.st_dev, after.st_mtime_ns, after.st_size)):
                    complete = False
                files[key] = {"hash": hashlib.sha256(data).hexdigest(),
                              "mode": stat.S_IMODE(after.st_mode), "size": len(data)}
            except OSError:
                complete = False
                files[key] = {"unreadable": True}
        if count > max_files:
            break
    git = _git_observation(root)
    manifest = {"version": 1, "root": str(root), "files": files, "git": git,
                "complete": complete and git["complete"], "captured_at": _now()}
    manifest["digest"] = _digest({key: manifest[key] for key in ("version", "root", "files", "git", "complete")})
    return manifest


def narrow_permissions(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, bool | str]:
    """Only retain grants supported by both the saved and live policy."""
    if not isinstance(previous, Mapping) or not isinstance(current, Mapping):
        raise ValueError("Geçerli önceki ve güncel izin politikası gerekiyor.")
    result: dict[str, bool | str] = {}
    rank = {"allow": 0, "ask": 1, "deny": 2, "never": 2}
    for key in previous.keys() | current.keys():
        old, live = previous.get(key), current.get(key)
        if isinstance(old, bool) or isinstance(live, bool):
            result[key] = old is True and live is True
        elif old in rank or live in rank:
            result[key] = ("allow", "ask", "deny")[max(rank.get(old, 2), rank.get(live, 2))]
        else:
            result[key] = "deny"
    return result


class DurableResumeController:
    def __init__(self, db, *, known_secrets: tuple[str, ...] = ()):
        self.db = db
        self.known_secrets = known_secrets
        self.store = CodingSessionStore(db, known_secrets=known_secrets)
        self.journal = ExecutionJournal(db, known_secrets=known_secrets)
        self.checkpoints = CheckpointStore(db, known_secrets=known_secrets)

    def _run(self, run_id: str, project_id: str | None = None) -> CodingSession:
        try:
            run = self.store.get_run(run_id, project_id=project_id)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError("Kalıcı çalışma verisi bozuk; güvenli devam engellendi.") from exc
        if run is None:
            raise ValueError("Çalışma bu projeye ait değil veya bulunamadı.")
        if run.run_id != run.id or not run.session_id:
            raise ValueError("Kalıcı çalışma kimliği doğrulanamadı.")
        return run

    def record_workspace(self, run_id: str, root: str | Path) -> dict[str, Any]:
        run = self._run(run_id)
        project = self.db.get_project(run.project_id)
        if not project or Path(project.local_path).expanduser().resolve() != Path(root).expanduser().resolve():
            raise ValueError("Çalışma klasörü proje kimliğiyle uyuşmuyor.")
        manifest = capture_workspace(root)
        entry = {"kind": "workspace_manifest", "workspace_manifest": manifest, "created_at": manifest["captured_at"]}
        entry["id"] = "manifest_" + uuid.uuid4().hex
        def update(session):
            session.checkpoints = [item for item in session.checkpoints if item.get("kind") != "workspace_manifest"] + [entry]
        return self.store._mutate(run_id, update).checkpoints[-1]

    def _manifest(self, run: CodingSession, steps: list[dict]) -> dict | None:
        candidates = []
        for item in run.checkpoints:
            if isinstance(item, dict) and isinstance(item.get("workspace_manifest"), dict):
                candidates.append((item.get("created_at", ""), item["workspace_manifest"]))
        for item in steps:
            value = item.get("metadata", {}).get("workspace_manifest")
            if item["state"] in {"completed", "failed"} and isinstance(value, dict):
                candidates.append((item.get("updated_at", ""), value))
        return max(candidates, key=lambda entry: entry[0])[1] if candidates else None

    def inspect(self, run_id: str, *, project_id: str | None = None,
                current_permissions: Mapping[str, Any] | None = None) -> dict[str, Any]:
        run = self._run(run_id, project_id)
        project = self.db.get_project(run.project_id)
        if project is None:
            raise ValueError("Proje bulunamadı.")
        conflicts: list[dict] = []
        steps, journal_error = [], None
        try:
            steps = self.journal.list_steps(run_id)
        except (ValueError, TypeError, KeyError):
            journal_error = "journal_corrupt"
            conflicts.append({"reason": journal_error})
        baseline = self._manifest(run, steps)
        current = None
        try:
            current = capture_workspace(project.local_path)
        except (OSError, ValueError):
            conflicts.append({"reason": "project_path_missing_or_invalid"})
        if baseline is None:
            conflicts.append({"reason": "missing_workspace_manifest"})
        elif (baseline.get("version") != 1 or not isinstance(baseline.get("files"), dict)
              or not isinstance(baseline.get("git"), dict)):
            conflicts.append({"reason": "workspace_manifest_corrupt"})
        elif current is not None:
            if baseline.get("digest") != _digest({key: baseline.get(key) for key in
                                                   ("version", "root", "files", "git", "complete")}):
                conflicts.append({"reason": "workspace_manifest_corrupt"})
            if baseline.get("root") != current["root"]:
                conflicts.append({"reason": "project_path_changed"})
            if not baseline.get("complete") or not current["complete"]:
                conflicts.append({"reason": "workspace_capture_incomplete"})
            for path in sorted(baseline["files"].keys() | current["files"].keys()):
                old, new = baseline["files"].get(path), current["files"].get(path)
                if old != new:
                    conflicts.append({"path": path, "reason": "file_added" if old is None else
                                      "file_missing" if new is None else "file_modified"})
            if baseline["git"] != current["git"]:
                conflicts.append({"reason": "git_state_changed"})
        uncertain = [dict(item, state="outcome_unknown") if item["state"] in {"running", "dispatching"}
                     else item for item in steps if item["state"] in
                     {"running", "dispatching", "outcome_unknown", "needs_reconciliation"}]
        projected_ids = {item.get("execution", {}).get("durable_request_id") for item in run.messages
                         if isinstance(item, dict) and isinstance(item.get("execution", {}), dict)}
        for decision in run.decisions:
            for message in decision.get("messages", []) if isinstance(decision.get("messages", []), list) else []:
                if isinstance(message, dict) and isinstance(message.get("execution", {}), dict):
                    projected_ids.add(message.get("execution", {}).get("durable_request_id"))
        acknowledged = {item.get("attempt_id") for item in run.decisions
                        if item.get("kind") == "projection_reconciled" and item.get("evidence")}
        # A child's committed final outcome is itself a durable projection for
        # its intermediate model responses. Delivery to the parent may still
        # be pending, but neither model nor child needs to be recreated.
        final_children = {item["step_id"] for item in steps
                          if item["state"] == "completed" and item["operation"].startswith("child:")
                          and isinstance(item.get("result"), dict)
                          and isinstance(item["result"].get("result"), str)
                          and not item["result"].get("truncated") and not item["result"].get("compacted")}
        unprojected = [item for item in steps if item["state"] == "completed"
                       and item.get("metadata", {}).get("kind") == "model"
                       and item.get("metadata", {}).get("projection_required")
                       and item.get("parent_step_id") not in final_children
                       and item["attempt_id"] not in projected_ids | acknowledged]
        approvals = [item for item in steps if item.get("operation", "").startswith("approval:")
                     and item["state"] not in {"completed", "cancelled", "failed"}]
        if not approvals and any(item.get("previous_status") in {"waiting_approval", "awaiting_approval"}
                                 for item in run.checkpoints if isinstance(item, dict)):
            approvals = [{"state": "pending", "requires_fresh_approval": True}]
        pending = [item for item in steps if any(word in item.get("operation", "")
                                               for word in ("run_tests", "quality_gate", "verification"))
                   and item["state"] != "completed"]
        invalidated = bool(conflicts) or any(item.get("verification_invalidated") for item in run.decisions)
        pending += [dict(item, stale=invalidated) for item in run.tests_executed
                    if item.get("status") not in {"passed", "failed"} or invalidated]
        try:
            saved_checkpoints = self.checkpoints.list_checkpoints(run.project_id, run_id=run_id)
            operations = self.checkpoints.list_operations(run.project_id, run_id=run_id)
            for operation in operations:
                if operation["status"] not in {"completed", "rolled_back"}:
                    conflicts.append({"reason": "interrupted_checkpoint_restore", "operation_id": operation["id"]})
            existing = {item["id"] for item in saved_checkpoints}
            for item in run.checkpoints:
                pointer = item.get("checkpoint_id") or (item.get("id") if item.get("backend") == "local_snapshot" else None)
                if pointer and pointer not in existing:
                    # A page of history is bounded; inspect a specific older
                    # reference before deciding that its checkpoint is missing.
                    conn = self.db._get_connection()
                    try:
                        found = conn.execute("SELECT 1 FROM coding_checkpoints WHERE id=? AND project_id=? AND run_id=?",
                                             (pointer, run.project_id, run.id)).fetchone()
                    finally:
                        conn.close()
                    if found is None:
                        conflicts.append({"reason": "missing_checkpoint", "checkpoint_id": pointer})
        except (ValueError, TypeError, KeyError):
            saved_checkpoints = []
            conflicts.append({"reason": "checkpoint_state_corrupt"})
        children = [item for item in steps if item.get("operation", "").startswith(("subagent:", "child:"))
                    or item.get("metadata", {}).get("kind") in {"subagent", "child", "child_delivery"}]
        delivered_children = {item.get("metadata", {}).get("agent_id") for item in children
                              if item.get("metadata", {}).get("kind") == "child_delivery"
                              and item["state"] == "completed"}
        children = [dict(item, delivery_state="delivered" if
                         (item.get("metadata", {}).get("agent_id") or item.get("agent_id") or item["attempt_id"])
                         in delivered_children else "persisted_not_delivered" if item["state"] == "completed"
                         else "requires_reconciliation") for item in children]
        live = self.store.is_live(run_id)
        blockers = (["active_executor"] if live else [])
        prepared = False
        if run.status == "queued" and run.resumed_from:
            conn = self.db._get_connection()
            try:
                claimed = conn.execute("SELECT continuation_run_id FROM coding_resume_claims WHERE source_run_id=?",
                                       (run.resumed_from,)).fetchone()
                prepared = bool(claimed and claimed[0] == run.id)
            finally:
                conn.close()
        if run.status not in {"interrupted", "failed", "cancelled", "blocked"} and not prepared:
            blockers.append("run_not_interrupted")
        if uncertain:
            blockers.append("uncertain_operations")
        if unprojected:
            blockers.append("model_output_projection_unconfirmed")
        if conflicts:
            blockers.append("workspace_requires_reconciliation")
        last_success = next((item for item in reversed(steps) if item["state"] == "completed"), None)
        report = {"run_id": run.id, "project_id": run.project_id, "session_id": run.session_id,
                  "status": run.status, "last_checkpoint": saved_checkpoints[0] if saved_checkpoints else None,
                  "lifecycle_state": "needs_reconciliation" if uncertain or unprojected or conflicts else run.status,
                  "last_successful_operation": last_success, "uncertain_operations": uncertain,
                  "pending_verification": pending, "pending_approvals": approvals,
                  "workspace_conflicts": conflicts, "resume_eligible": not blockers,
                  "resume_blockers": blockers, "live_owner": live,
                  "prepared_continuation": prepared, "resume_source_run_id": run.resumed_from if prepared else None,
                  "actions": ["inspect", "compare", "export"] + ([] if live or run.status == "completed" else ["cancel"]) +
                             (["reconcile"] if uncertain or conflicts or unprojected else []) +
                             (["resume_with_approval"] if not blockers else []),
                  "steps": steps, "children": children,
                  "unprojected_model_outputs": unprojected,
                  "workspace": {"expected": baseline, "current": current},
                  "permissions": narrow_permissions(run.permissions, current_permissions)
                                 if current_permissions is not None else run.permissions,
                  "approval_policy": "fresh_per_operation_approval_required",
                  "journal_error": journal_error}
        return redact_sensitive(report, self.known_secrets)

    def compare(self, run_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        report = self.inspect(run_id, project_id=project_id)
        checkpoint = report["last_checkpoint"]
        if checkpoint is None:
            return {"changes": report["workspace_conflicts"], "checkpoint_id": None}
        project = self.db.get_project(report["project_id"])
        return self.checkpoints.compare_checkpoint(checkpoint["id"], project_id=project.id,
                                                  root=project.local_path, run_id=run_id)

    def _ancestors(self, run_id: str) -> list[CodingSession]:
        """Validate the complete lineage before reusing any inherited outcome."""
        current = self._run(run_id)
        ancestors = []
        seen = {current.id}
        source_id = current.resumed_from
        while source_id:
            if source_id in seen or len(ancestors) >= 1000:
                raise ValueError("Devam çalışma zinciri bozuk veya güvenli inceleme sınırını aşıyor.")
            seen.add(source_id)
            source = self._run(source_id, project_id=current.project_id)
            if (source.session_id != current.session_id or source.task_id != current.task_id
                    or source.project_id != current.project_id or source.status in ACTIVE_STATUSES
                    or self.store.is_live(source.id)):
                raise ValueError("Devam zincirinin proje, görev, oturum veya sahiplik kimliği uyuşmuyor.")
            ancestors.append(source)
            source_id = source.resumed_from
        return ancestors

    def inherited_child_outcome(self, run_id: str, agent_id: str) -> dict[str, Any] | None:
        """Read a terminal inherited child; never reconnect or create a worker.

        Unavailable/truncated result bodies are returned intact so the caller
        can require reconciliation while preserving the durable child identity.
        """
        ancestors = self._ancestors(run_id)
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("Geçerli alt ajan kimliği gerekiyor.")
        for source in ancestors:
            for row in reversed(self.journal.list_steps(source.id)):
                if (row["operation"].startswith("child:")
                        and row["state"] in {"completed", "failed", "cancelled"}
                        and agent_id in {row["attempt_id"], row["step_id"], row.get("agent_id"),
                                        row.get("metadata", {}).get("agent_id")}):
                    return row
        return None

    def inherited_completed_tool(self, run_id: str, tool_name: str, arguments: str) -> dict[str, Any] | None:
        """Find a durably completed inherited operation without relying on chat.

        The current run is deliberately excluded. Every ancestor must share the
        immutable project/task/session identity, and cycles or missing ancestors
        block execution. A returned row can have a missing/compacted/truncated
        result; its completed identity still forbids dispatching that effect
        again. The caller must require reconciliation when it cannot present
        the original result.
        """
        from firik_agent.core.recovery import operation_fingerprint

        ancestors = self._ancestors(run_id)
        if not ancestors:
            return None
        if not isinstance(tool_name, str) or not tool_name or not isinstance(arguments, str):
            raise ValueError("Araç adı ve parametreleri geçerli metin olmalıdır.")
        try:
            parsed = json.loads(arguments or "{}")
        except (TypeError, ValueError):
            return None  # The regular argument validator rejects invalid calls.
        if not isinstance(parsed, dict):
            return None
        canonical = json.dumps(parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        input_hashes = {_digest(arguments), _digest(canonical), _digest(parsed)}
        operation_hash = operation_fingerprint(tool_name, arguments)
        for source in ancestors:
            for row in reversed(self.journal.list_steps(source.id)):
                if row["operation"] != "tool:" + tool_name or row["state"] != "completed":
                    continue
                if (row["input_fingerprint"] in input_hashes
                        or row.get("metadata", {}).get("operation_fingerprint") == operation_hash):
                    return row
                # Older journal versions stored raw arguments but no normalized
                # operation fingerprint. Only use their summary if its hash
                # proves it is the complete, unredacted original input.
                summary = row.get("input_summary")
                if (isinstance(summary, str) and _digest(summary) == row["input_fingerprint"]
                        and operation_fingerprint(tool_name, summary) == operation_hash):
                    return row
        return None

    def reconcile(self, run_id: str, attempt_id: str, outcome: str, *, evidence: str,
                  project_id: str | None = None) -> dict[str, Any]:
        run = self._run(run_id, project_id)
        if self.store.is_live(run_id):
            raise ValueError("Etkin yürütücü varken sonuç uzlaştırılamaz.")
        step = self.journal.get(run_id, attempt_id)
        if (step and step["state"] == "completed" and step.get("metadata", {}).get("kind") == "model"
                and step.get("metadata", {}).get("projection_required")):
            if outcome != "completed" or not isinstance(evidence, str) or not evidence.strip():
                raise ValueError("Tamamlanmış model çıktısını kabul etmek için inceleme kanıtı gerekiyor.")
            self.store.record_decision(run.id, {"kind": "projection_reconciled", "attempt_id": attempt_id,
                                                "evidence": evidence, "output_loss_acknowledged": True})
            return step
        # A direct inspect can observe a crash before startup recovery completed.
        self.journal.recover_interrupted(run_id)
        return self.journal.reconcile(run_id, attempt_id, outcome, evidence=evidence)

    def reconcile_workspace(self, run_id: str, *, evidence: str, approved: bool = False,
                            project_id: str | None = None) -> dict[str, Any]:
        run = self._run(run_id, project_id)
        if not approved or not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("Değişmiş çalışma alanını kabul etmek için açık onay ve inceleme kanıtı gerekiyor.")
        if self.store.is_live(run_id):
            raise ValueError("Etkin çalışma alanı uzlaştırılamaz.")
        report = self.inspect(run_id, project_id=run.project_id)
        forbidden = {"missing_checkpoint", "checkpoint_state_corrupt", "interrupted_checkpoint_restore",
                     "journal_corrupt", "project_path_changed", "project_path_missing_or_invalid"}
        if any(item["reason"] in forbidden for item in report["workspace_conflicts"]):
            raise ValueError("Eksik veya bozuk kalıcı durum elle düzeltilmeden uzlaştırılamaz.")
        project = self.db.get_project(run.project_id)
        entry = self.record_workspace(run_id, project.local_path)
        self.store.record_decision(run_id, {"kind": "workspace_reconciled", "evidence": evidence,
                                            "verification_invalidated": True})
        return entry

    def prepare_resume(self, run_id: str, *, project_id: str | None = None,
                       current_permissions: Mapping[str, Any], approved: bool = False,
                       continuation_run_id: str | None = None) -> CodingSession:
        if approved is not True:
            raise ValueError("Devam etmek için açık kullanıcı onayı gerekiyor.")
        source = self._run(run_id, project_id)
        report = self.inspect(run_id, project_id=source.project_id, current_permissions=current_permissions)
        if not report["resume_eligible"]:
            raise ValueError("Güvenli devam önkoşulları sağlanmadı: " + ", ".join(report["resume_blockers"]))
        identifier = continuation_run_id or f"run_{uuid.uuid4().hex}"
        # Run creation and source claim share one SQLite transaction. An
        # interrupted client can repeat this request without a duplicate run.
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                claimed = conn.execute("SELECT continuation_run_id FROM coding_resume_claims WHERE source_run_id=?",
                                       (run_id,)).fetchone()
                if claimed:
                    existing = conn.execute("SELECT * FROM coding_sessions WHERE id=?", (claimed[0],)).fetchone()
                    if existing is None:
                        raise ValueError("Devam kaydı eksik; yeniden yürütme engellendi.")
                    prepared = self.store._clean_session(self.db._row_to_session(existing))
                    if (prepared.project_id != source.project_id or prepared.session_id != source.session_id
                            or prepared.task_id != source.task_id or prepared.resumed_from != source.id):
                        raise ValueError("Devam kaydının proje veya oturum kimliği bozuk.")
                    if prepared.status != "queued":
                        raise ValueError("Bu devam çalışması zaten kullanıldı; yeniden başlatılamaz.")
                    prepared.permissions = narrow_permissions(prepared.permissions, current_permissions)
                    self.db._write_session(conn, prepared)
                    conn.commit()
                    return prepared
                row = conn.execute("SELECT * FROM coding_sessions WHERE id=?", (run_id,)).fetchone()
                latest = self.db._row_to_session(row)
                if latest.updated_at != source.updated_at or latest.status != source.status:
                    raise ValueError("Çalışma incelemeden sonra değişti; yeniden inceleyin.")
                if conn.execute("SELECT 1 FROM coding_sessions WHERE id=?", (identifier,)).fetchone():
                    raise ValueError("Devam çalışması kimliği zaten kullanılıyor.")
                result = CodingSession(id=identifier, run_id=identifier, session_id=source.session_id,
                    project_id=source.project_id, task_id=source.task_id, provider=source.provider,
                    model=source.model, mode=source.mode, title=source.title, user_request=source.user_request,
                    permissions=report["permissions"], status="queued", resumed_from=source.id,
                    plan=source.plan, completed_steps=source.completed_steps, messages=source.messages,
                    context_summary=source.context_summary,
                    checkpoints=[{"id": "manifest_" + uuid.uuid4().hex, "kind": "workspace_manifest",
                                  "created_at": _now(), "workspace_manifest": report["workspace"]["expected"]}],
                    decisions=[{"kind": "resume_authorized", "source_run_id": source.id,
                                "created_at": _now(), "pending_approvals_require_fresh_decision": True,
                                "verification_invalidated": True}])
                self.db._write_session(conn, self.store._clean_session(result))
                conn.execute("INSERT INTO coding_resume_claims(source_run_id,continuation_run_id,created_at) VALUES (?,?,?)",
                             (source.id, identifier, _now()))
                conn.commit()
                return result
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def cancel(self, run_id: str, *, project_id: str | None = None) -> CodingSession:
        run = self._run(run_id, project_id)
        if run.status == "completed":
            raise ValueError("Tamamlanmış çalışmanın kalıcı sonucu iptal durumuna dönüştürülemez.")
        if self.store.is_live(run_id):
            raise ValueError("Etkin çalışma kendi yürütücüsü üzerinden durdurulmalıdır.")
        self.journal.recover_interrupted(run_id)
        # Cancellation cannot turn uncertain effects into confirmed failures.
        for step in self.journal.list_steps(run_id):
            if step["state"] in {"created", "planned", "ready"}:
                self.journal.transition(run_id, step["attempt_id"], "cancelled")
        return self.store.finish_run(run.id, "cancelled", result="cancelled")

    def export_run(self, run_id: str, *, project_id: str | None = None) -> dict[str, Any]:
        report = self.inspect(run_id, project_id=project_id)
        return redact_sensitive({"run": self.store.export_run(run_id, project_id=report["project_id"]),
                                 "recovery": report}, self.known_secrets)
