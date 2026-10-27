"""Durable, identity-scoped coding runs on the existing project database.

Opening a store never restarts work. The application calls recover_interrupted
once at startup and creates a new run only after an explicit user action.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional, TYPE_CHECKING
import uuid

from firik_agent.projects.indexer import is_sensitive_file, sanitize_secrets
from firik_agent.projects.models import CodingRunEvent, CodingSession
from firik_agent.core.completion import legacy_status

if TYPE_CHECKING:
    from firik_agent.projects.db import ProjectDatabase


REDACTED = "[SECRET_FILTERED]"
ACTIVE_STATUSES = frozenset({
    "queued", "running", "understanding", "planning", "waiting_approval",
    "applying", "implementing", "verifying", "correcting", "repairing", "reviewing", "stopping", "awaiting_approval", "paused",
})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted", "blocked"})
_SECRET_KEY = re.compile(
    r"(?i)^(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|auth|"
    r"password|passwd|secret|secret[_-]?key|client[_-]?secret|private[_-]?key|credential|credentials)$"
)
_TEXT_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----", re.S),
    re.compile(r"(?i)\b(?:[a-z0-9]+[_-])*(?:api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|password|passwd|"
               r"client[_-]?secret|secret[_-]?key|secret)\b\s*[:=]\s*(?:['\"][^'\"\r\n]+['\"]|[^\s,;\r\n]+)"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"(?i)\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_sensitive(value: Any, known_secrets: tuple[str, ...] = ()) -> Any:
    """Return a JSON-compatible copy with credentials removed before storage."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        text = value
        # Exact configured secrets supplement patterns without reading the environment.
        for secret in sorted((s for s in known_secrets if len(s) >= 4), key=len, reverse=True):
            text = text.replace(secret, REDACTED)
        for pattern in _TEXT_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return sanitize_secrets(text)
    if isinstance(value, Mapping):
        return {str(key): REDACTED if _SECRET_KEY.fullmatch(str(key)) else redact_sensitive(item, known_secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item, known_secrets) for item in value]
    if hasattr(value, "model_dump"):
        return redact_sensitive(value.model_dump(mode="json"), known_secrets)
    if is_dataclass(value) and not isinstance(value, type):
        return redact_sensitive(asdict(value), known_secrets)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact_sensitive(str(value), known_secrets)


class CodingSessionStore:
    """Small persistence API; each mutation is one serialized SQLite transaction."""

    def __init__(self, db: ProjectDatabase, *, known_secrets: tuple[str, ...] = ()):
        self.db = db
        self._known_secrets = known_secrets

    def create_run(
        self, project_id: str, *, task_id: Optional[str] = None,
        session_id: Optional[str] = None, run_id: Optional[str] = None,
        provider: str = "", model: str = "", mode: str = "plan",
        permissions: Optional[dict[str, str | bool]] = None, user_request: str = "",
        title: str = "Kodlama oturumu", resumed_from: Optional[str] = None,
    ) -> CodingSession:
        """Create one run; returned id and run_id are identical."""
        identifier = run_id or f"run_{uuid.uuid4().hex}"
        session = CodingSession(
            id=identifier, run_id=identifier, session_id=session_id or f"coding_{uuid.uuid4().hex}",
            project_id=project_id, task_id=task_id, provider=provider, model=model,
            mode=mode, permissions=permissions or {}, user_request=user_request,
            title=title, status="running", resumed_from=resumed_from,
        )
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                    raise ValueError("Proje bulunamadı.")
                if conn.execute("SELECT 1 FROM coding_sessions WHERE id=?", (identifier,)).fetchone():
                    raise ValueError("Bu çalışma kimliği zaten kullanılıyor.")
                previous = conn.execute(
                    "SELECT project_id, task_id FROM coding_sessions WHERE session_id=? LIMIT 1",
                    (session.session_id,),
                ).fetchone()
                if previous and (previous["project_id"] != project_id or previous["task_id"] != task_id):
                    raise ValueError("Oturum başka bir projeye veya göreve ait.")
                if resumed_from:
                    prior = conn.execute("SELECT * FROM coding_sessions WHERE id=?", (resumed_from,)).fetchone()
                    if (prior is None or prior["project_id"] != project_id or prior["task_id"] != task_id
                            or prior["session_id"] != session.session_id):
                        raise ValueError("Devam edilecek çalışma bu proje ve göreve ait değil.")
                    if prior["status"] in ACTIVE_STATUSES:
                        raise ValueError("Etkin bir çalışma devam kaynağı olarak kullanılamaz.")
                self.db._write_session(conn, self._clean_session(session))
                conn.commit()
            finally:
                conn.close()
        return self.get_run(identifier)  # type: ignore[return-value]

    start_run = create_run

    def _clean_session(self, session: CodingSession) -> CodingSession:
        data = redact_sensitive(session.model_dump(mode="json"), self._known_secrets)
        data["file_journal"] = [self._clean_file_entry(entry) for entry in session.file_journal]
        return CodingSession.model_validate(data)

    def _clean_file_entry(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        clean = redact_sensitive(entry, self._known_secrets)
        path = entry.get("path") or entry.get("file_path") or ""
        if is_sensitive_file(Path(path)):
            for name in ("before", "after", "before_content", "after_content", "content", "diff", "patch"):
                if name in clean:
                    clean[name] = REDACTED
        if clean != dict(entry):
            clean["redacted"] = True
            clean["reversible"] = False
        return clean

    def get_run(self, run_id: str, *, project_id: Optional[str] = None) -> Optional[CodingSession]:
        conn = self.db._get_connection()
        try:
            row = conn.execute("SELECT * FROM coding_sessions WHERE id=?", (run_id,)).fetchone()
            if row is None or (project_id is not None and row["project_id"] != project_id):
                return None
            return self._clean_session(self.db._row_to_session(row))
        finally:
            conn.close()

    def list_runs(self, project_id: str, *, task_id: Optional[str] = None,
                  session_id: Optional[str] = None, limit: int = 100) -> list[CodingSession]:
        filters, values = ["project_id=?"], [project_id]
        if task_id is not None:
            filters.append("task_id=?")
            values.append(task_id)
        if session_id is not None:
            filters.append("session_id=?")
            values.append(session_id)
        conn = self.db._get_connection()
        try:
            rows = conn.execute(
                f"SELECT * FROM coding_sessions WHERE {' AND '.join(filters)} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*values, max(1, min(limit, 1000))),
            ).fetchall()
            return [self._clean_session(self.db._row_to_session(row)) for row in rows]
        finally:
            conn.close()

    def latest_run(self, project_id: str, *, task_id: Optional[str] = None,
                   session_id: Optional[str] = None) -> Optional[CodingSession]:
        runs = self.list_runs(project_id, task_id=task_id, session_id=session_id, limit=1)
        return runs[0] if runs else None

    def _mutate(self, run_id: str, change: Callable[[CodingSession], None]) -> CodingSession:
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM coding_sessions WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    raise ValueError("Kodlama çalışması bulunamadı.")
                session = self.db._row_to_session(row)
                change(session)
                session.updated_at = _now()
                session = self._clean_session(session)
                self.db._write_session(conn, session)
                conn.commit()
                return session
            finally:
                conn.close()

    def update_run(self, run_id: str, **fields: Any) -> CodingSession:
        immutable = {"id", "run_id", "session_id", "project_id", "task_id", "created_at", "resumed_from"}
        if immutable.intersection(fields) or any(key not in CodingSession.model_fields for key in fields):
            raise ValueError("Geçersiz veya değiştirilemez çalışma alanı.")
        if isinstance(fields.get("plan"), (list, tuple, dict)):
            fields["plan"] = json.dumps(fields["plan"], ensure_ascii=False)

        def change(session: CodingSession) -> None:
            status = fields.get("status", session.status)
            if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
                raise ValueError("Geçersiz çalışma durumu.")
            if session.status in TERMINAL_STATUSES and status in ACTIVE_STATUSES:
                raise ValueError("Kesilen bir çalışma otomatik sürdürülemez; yeni çalışma başlatın.")
            data = session.model_dump()
            data.update(fields)
            if status in TERMINAL_STATUSES and not data.get("ended_at"):
                data["ended_at"] = _now()
            validated = CodingSession.model_validate(data)
            for key in fields:
                setattr(session, key, getattr(validated, key))
            session.ended_at = validated.ended_at

        return self._mutate(run_id, change)

    def finish_run(self, run_id: str, status: str = "completed", **fields: Any) -> CodingSession:
        if status in {"completed_verified", "completed_unverified"}:
            fields.setdefault("result", status)
            status = legacy_status(status)
        if status not in TERMINAL_STATUSES:
            raise ValueError("Çalışmayı bitirmek için son durum gerekli.")
        return self.update_run(run_id, status=status, **fields)

    @staticmethod
    def _event_mapping(event: Any) -> dict[str, Any]:
        if isinstance(event, Mapping):
            return dict(event)
        if hasattr(event, "model_dump"):
            return event.model_dump(mode="json", exclude_none=True)
        if is_dataclass(event) and not isinstance(event, type):
            data = asdict(event)
            # Existing AgentEvent exposes optional project/task fields with None
            # defaults. Those represent absent identity, while mapping values
            # supplied explicitly by callers remain subject to strict validation.
            return {key: value for key, value in data.items()
                    if key not in {"project_id", "task_id", "run_id"} or value is not None}
        raise TypeError("Olay bir eşleme veya yapılandırılmış olay olmalı.")

    @staticmethod
    def _validate_identity(session: CodingSession, data: Mapping[str, Any]) -> None:
        expected = {"run_id": session.run_id, "session_id": session.session_id,
                    "project_id": session.project_id, "task_id": session.task_id}
        for key, value in expected.items():
            if key in data and data[key] != value:
                raise ValueError(f"Olayın {key} kimliği çalışma ile uyuşmuyor.")
        for name in ("data", "metadata"):
            nested = data.get(name)
            if isinstance(nested, Mapping):
                for key, value in expected.items():
                    if key in nested and nested[key] != value:
                        raise ValueError(f"Olay verisinin {key} kimliği çalışma ile uyuşmuyor.")

    def append_event(self, run_id: str, event: Any) -> CodingRunEvent:
        data = self._event_mapping(event)
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM coding_sessions WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    raise ValueError("Olayın çalışması bulunamadı.")
                session = self.db._row_to_session(row)
                self._validate_identity(session, data)
                data.update(run_id=run_id, session_id=session.session_id,
                            project_id=session.project_id, task_id=session.task_id)
                data = redact_sensitive(data, self._known_secrets)
                event_type = data.get("event_type") or data.get("type") or "activity"
                sequence = conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0)+1 FROM coding_run_events WHERE run_id=?", (run_id,),
                ).fetchone()[0]
                stored = CodingRunEvent(
                    id=f"event_{uuid.uuid4().hex}", run_id=run_id, session_id=session.session_id,
                    project_id=session.project_id, task_id=session.task_id, sequence=sequence,
                    event_type=event_type, data=data,
                )
                conn.execute(
                    "INSERT INTO coding_run_events(id, run_id, session_id, project_id, task_id, sequence, event_type, data, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (stored.id, run_id, stored.session_id, stored.project_id, stored.task_id,
                     sequence, stored.event_type, json.dumps(data, ensure_ascii=False), stored.created_at),
                )
                conn.execute("UPDATE coding_sessions SET updated_at=? WHERE id=?", (stored.created_at, run_id))
                conn.commit()
                return stored
            finally:
                conn.close()

    def list_events(self, run_id: str, *, project_id: Optional[str] = None,
                    limit: Optional[int] = None, include_deltas: bool = True) -> list[CodingRunEvent]:
        run = self.get_run(run_id, project_id=project_id)
        if run is None:
            return []
        conn = self.db._get_connection()
        try:
            query = "SELECT * FROM coding_run_events WHERE run_id=?"
            args: list[Any] = [run_id]
            if not include_deltas:
                query += " AND event_type NOT IN ('text_delta', 'reasoning_delta', 'tool_call_arguments')"
            query += " ORDER BY sequence" + (" DESC LIMIT ?" if limit is not None else "")
            if limit is not None:
                args.append(max(1, min(int(limit), 10000)))
            rows = conn.execute(query, args).fetchall()
            if limit is not None:
                rows.reverse()
            return [CodingRunEvent.model_validate({**dict(row), "data": redact_sensitive(json.loads(row["data"]), self._known_secrets)})
                    for row in rows]
        finally:
            conn.close()

    def record_message(self, run_id: str, role: str | Mapping[str, Any], content: Optional[str] = None,
                       **metadata: Any) -> CodingSession:
        message = dict(role) if isinstance(role, Mapping) else {"role": role, "content": content or ""}
        message.update(metadata)
        if message.get("role") not in {"user", "assistant", "system", "tool"}:
            raise ValueError("Geçersiz mesaj rolü.")
        message.setdefault("created_at", _now())

        def change(session: CodingSession) -> None:
            self._validate_identity(session, message)
            message.update(run_id=run_id, session_id=session.session_id,
                           project_id=session.project_id, task_id=session.task_id)
            session.messages.append(message)

        return self._mutate(run_id, change)

    def record_file_change(self, run_id: str, change: Optional[Mapping[str, Any]] = None,
                           **fields: Any) -> CodingSession:
        entry = {**(change or {}), **fields}
        path = entry.get("path") or entry.get("file_path")
        if not isinstance(path, str) or not path:
            raise ValueError("Dosya değişikliği için yol gerekli.")
        entry["path"] = path
        clean = self._clean_file_entry(entry)
        clean.setdefault("created_at", _now())

        def change(session: CodingSession) -> None:
            self._validate_identity(session, clean)
            session.file_journal.append(clean)
            if path not in session.files_changed:
                session.files_changed.append(path)

        return self._mutate(run_id, change)

    def record_test(self, run_id: str, result: Mapping[str, Any]) -> CodingSession:
        entry = dict(result)
        # No synthetic success for skipped, cancelled or incomplete executions.
        if (entry.get("passed") is True or entry.get("status") in {"passed", "success"}) and entry.get("exit_code") != 0:
            raise ValueError("Başarılı test sonucu gerçek sıfır çıkış kodu gerektirir.")
        entry.setdefault("created_at", _now())

        def change(session: CodingSession) -> None:
            self._validate_identity(session, entry)
            session.tests_executed.append(entry)

        return self._mutate(run_id, change)

    def record_decision(self, run_id: str, decision: Mapping[str, Any]) -> CodingSession:
        entry = {"created_at": _now(), **decision}

        def change(session: CodingSession) -> None:
            self._validate_identity(session, entry)
            session.decisions.append(entry)

        return self._mutate(run_id, change)

    def checkpoint(self, run_id: str, checkpoint: Optional[Mapping[str, Any]] = None,
                   **fields: Any) -> CodingSession:
        entry = {"id": f"checkpoint_{uuid.uuid4().hex}", "created_at": _now(), **(checkpoint or {}), **fields}

        def change(session: CodingSession) -> None:
            self._validate_identity(session, entry)
            session.checkpoints.append(entry)

        return self._mutate(run_id, change)

    def recover_interrupted(self, *, project_id: Optional[str] = None) -> list[CodingSession]:
        """Mark unfinished startup records; do not execute tools or start a worker."""
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
                query = f"SELECT * FROM coding_sessions WHERE status IN ({placeholders})"
                values: tuple[Any, ...] = tuple(ACTIVE_STATUSES)
                if project_id is not None:
                    query += " AND project_id=?"
                    values += (project_id,)
                recovered = []
                for row in conn.execute(query, values).fetchall():
                    session = self.db._row_to_session(row)
                    previous_status = session.status
                    session.status = "interrupted"
                    session.updated_at = session.ended_at = _now()
                    session.checkpoints.append({"id": f"checkpoint_{uuid.uuid4().hex}",
                                                "created_at": session.updated_at, "kind": "interrupted",
                                                "previous_status": previous_status,
                                                "requires_revalidation": True})
                    session = self._clean_session(session)
                    self.db._write_session(conn, session)
                    recovered.append(session)
                conn.commit()
                return recovered
            finally:
                conn.close()

    def export_run(self, run_id: str, *, project_id: Optional[str] = None) -> dict[str, Any]:
        run = self.get_run(run_id, project_id=project_id)
        if run is None:
            raise ValueError("Dışa aktarılacak çalışma bulunamadı.")
        return redact_sensitive({**run.model_dump(mode="json"),
                                 "events": [event.model_dump(mode="json") for event in self.list_events(run_id, project_id=run.project_id)]},
                                self._known_secrets)

    def export_json(self, run_id: str, *, project_id: Optional[str] = None) -> str:
        return json.dumps(self.export_run(run_id, project_id=project_id), ensure_ascii=False, indent=2)
