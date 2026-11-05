"""Additive, transactional learning records in the existing ProjectDatabase.

No chat, project or execution history is copied. Every connection is obtained
from its existing owner, retaining WAL, foreign keys and durable sync settings.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import json
import os
import sqlite3
from typing import Any, Iterator

from firik_agent.projects.db import ProjectDatabase
from .models import identifier, now

_TABLES = {"experiences", "proposals", "memories", "jobs", "audit", "retrievals"}


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class LearningStore:
    def __init__(self, database: ProjectDatabase):
        self.database = database
        self.fts_available = False
        self.max_records = 10000
        self._migrate()
        self._recover()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.database._lock:
            conn = self.database._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _migrate(self) -> None:
        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS improvement_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = conn.execute("SELECT value FROM improvement_meta WHERE key='schema_version'").fetchone()
            if row and int(json.loads(row[0])) > 1:
                raise ValueError("Learning database version is newer than this application")
            for table in sorted(_TABLES):
                conn.execute(f"CREATE TABLE IF NOT EXISTS improvement_{table} ("
                             "id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, status TEXT NOT NULL, "
                             "idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL, data TEXT NOT NULL)")
                conn.execute(f"CREATE INDEX IF NOT EXISTS improvement_{table}_scope "
                             f"ON improvement_{table}(project_id,session_id,status,created_at)")
            conn.execute("INSERT OR IGNORE INTO improvement_meta VALUES ('schema_version', '1')")
            try:
                conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS improvement_memory_fts USING "
                             "fts5(memory_id UNINDEXED, content, tokenize='unicode61')")
                self.fts_available = True
            except sqlite3.OperationalError:
                # SQLite builds without FTS5 still provide bounded lexical retrieval.
                self.fts_available = False

    @staticmethod
    def _table(table: str) -> str:
        if table not in _TABLES:
            raise ValueError("Unknown learning table")
        return "improvement_" + table

    def put(self, table: str, record: dict[str, Any], *, conn=None, replace=False) -> bool:
        if conn is None:
            with self.transaction() as tx:
                return self.put(table, record, conn=tx, replace=replace)
        table_name = self._table(table)
        key = record.get("id") or record.get("memory_id")
        if not key:
            raise ValueError("Record identity required")
        values = (key, record.get("project_id"), record.get("session_id"), record.get("status", "recorded"),
                  record.get("idempotency_key"), record.get("created_at", now()), _encode(record))
        if replace:
            # UPDATE preserves identity and never hides a conflicting idempotency key.
            result = conn.execute(f"UPDATE {table_name} SET project_id=?,session_id=?,status=?,"
                                  "idempotency_key=?,created_at=?,data=? WHERE id=?",
                                  values[1:] + (key,))
            if result.rowcount != 1:
                raise ValueError("Record no longer exists")
        else:
            if conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] >= self.max_records:
                if conn.execute(f"SELECT 1 FROM {table_name} WHERE id=? OR idempotency_key=?",
                                (key, record.get("idempotency_key"))).fetchone():
                    return False
                raise ValueError("Learning storage limit reached; inspect retention before adding records")
            result = conn.execute(f"INSERT OR IGNORE INTO {table_name} VALUES (?,?,?,?,?,?,?)", values)
        if table == "memories" and self.fts_available and result.rowcount:
            conn.execute("DELETE FROM improvement_memory_fts WHERE memory_id=?", (key,))
            if record.get("status") == "active":
                conn.execute("INSERT INTO improvement_memory_fts VALUES (?,?)", (key, record["content"]))
        return bool(result.rowcount)

    def get(self, table: str, key: str, *, conn=None) -> dict[str, Any] | None:
        own = conn is None
        conn = conn or self.database._get_connection()
        try:
            row = conn.execute(f"SELECT data FROM {self._table(table)} WHERE id=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            if own:
                conn.close()

    def by_key(self, table: str, key: str, *, conn=None) -> dict[str, Any] | None:
        own = conn is None
        conn = conn or self.database._get_connection()
        try:
            row = conn.execute(f"SELECT data FROM {self._table(table)} WHERE idempotency_key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            if own:
                conn.close()

    def list(self, table: str, *, project_id: str | None = None, session_id: str | None = None,
             status: str | None = None, limit: int = 50, offset: int = 0, conn=None) -> list[dict[str, Any]]:
        query = f"SELECT data FROM {self._table(table)} WHERE 1=1"
        args: list[Any] = []
        for column, value in (("project_id", project_id), ("session_id", session_id), ("status", status)):
            if value is not None:
                query += f" AND {column}=?"
                args.append(value)
        query += " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?"
        args.extend((max(1, min(int(limit), 100000)), max(0, int(offset))))
        own = conn is None
        conn = conn or self.database._get_connection()
        try:
            return [json.loads(row[0]) for row in conn.execute(query, args).fetchall()]
        finally:
            if own:
                conn.close()

    def meta(self, key: str, default=None):
        conn = self.database._get_connection()
        try:
            row = conn.execute("SELECT value FROM improvement_meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        finally:
            conn.close()

    def set_meta(self, key: str, value: Any, *, conn=None) -> None:
        if conn is None:
            with self.transaction() as tx:
                return self.set_meta(key, value, conn=tx)
        conn.execute("INSERT INTO improvement_meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, _encode(value)))

    def audit(self, action: str, *, proposal_id: str | None = None, actor="host", details=None, conn=None):
        record = {"id": identifier("audit"), "action": action, "proposal_id": proposal_id,
                  "actor": actor, "details": details or {}, "created_at": now(), "status": "recorded"}
        self.put("audit", record, conn=conn)
        return record

    def counts(self) -> dict[str, dict[str, int]]:
        conn = self.database._get_connection()
        try:
            return {table: {row[0]: row[1] for row in conn.execute(
                f"SELECT status,COUNT(*) FROM {self._table(table)} GROUP BY status").fetchall()}
                for table in sorted(_TABLES)}
        finally:
            conn.close()

    def _recover(self) -> None:
        # No pending mutation is replayed, even if its owner died during apply.
        with self.transaction() as conn:
            for table, statuses in (("jobs", {"running", "queued"}),
                                    ("proposals", {"applying", "rolling_back", "validating", "disabling"})):
                for item in self.list(table, limit=100000, conn=conn):
                    if item.get("status") not in statuses:
                        continue
                    pid = item.get("owner_pid")
                    if pid and _pid_alive(pid):
                        continue
                    item["status"] = "interrupted" if table == "jobs" else "unknown"
                    item["error"] = "Owner exited; inspect and reconcile. No automatic replay."
                    item["updated_at"] = now()
                    self.put(table, item, conn=conn, replace=True)
                    self.audit("restart_recovery", proposal_id=item["id"] if table == "proposals" else None,
                               details={"id": item["id"], "status": item["status"]}, conn=conn)

    def prune(self, retention_days: int, max_records: int) -> dict[str, int]:
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)).isoformat()
        removed = {}
        # Keep pending proposals and all applied rollback records. Experience
        # referenced by retained proposals/memories is protected from retention.
        with self.transaction() as conn:
            retained = set()
            for table in ("proposals", "memories"):
                for row in self.list(table, limit=100000, conn=conn):
                    retained.update(row.get("evidence_ids", []))
                    if row.get("source_reference"):
                        retained.add(row["source_reference"])
            for table in ("experiences", "jobs", "retrievals", "audit"):
                rows = self.list(table, limit=100000, conn=conn)
                count = 0
                for position, row in enumerate(rows):
                    if row["id"] in retained or row.get("status") in {"queued", "running"}:
                        continue
                    if table == "audit" and row.get("proposal_id") and self.get("proposals", row["proposal_id"], conn=conn):
                        continue
                    if row.get("created_at", "") < cutoff or position >= max_records:
                        conn.execute(f"DELETE FROM {self._table(table)} WHERE id=?", (row["id"],))
                        count += 1
                removed[table] = count
        return removed


def _pid_alive(pid: Any) -> bool:
    try:
        if os.name == "nt":
            from firik_agent.projects.coding_sessions import _windows_process
            return bool(_windows_process(int(pid))[0])
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False
