"""Crash-consistent execution intents in the existing Projects SQLite database.

A committed intent is required before dispatch. SQLite cannot make an external
side effect and a result commit atomic: interrupted dispatches are *unknown*.
This journal never invokes an operation, retries it, or grants an approval.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Callable, TYPE_CHECKING
import uuid

from firik_agent.projects.coding_sessions import redact_sensitive

if TYPE_CHECKING:
    from firik_agent.projects.db import ProjectDatabase


STATES = frozenset({"created", "planned", "ready", "dispatching", "running", "completed",
                    "failed", "cancelled", "outcome_unknown", "needs_reconciliation"})
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
UNCERTAIN_STATES = frozenset({"outcome_unknown", "needs_reconciliation"})
_ALLOWED = {
    "created": {"planned", "ready", "cancelled", "failed"},
    "planned": {"ready", "cancelled", "failed"},
    # Local decisions (e.g. approval) may combine dispatch/start in one commit.
    # There is still a durable intent before the operation enters running.
    "ready": {"dispatching", "running", "cancelled", "failed", "needs_reconciliation"},
    "dispatching": {"running", "completed", "failed", "cancelled", "outcome_unknown", "needs_reconciliation"},
    "running": {"completed", "failed", "cancelled", "outcome_unknown", "needs_reconciliation"},
    "outcome_unknown": {"needs_reconciliation"},
    "needs_reconciliation": set(),
    "completed": set(), "failed": set(), "cancelled": set(),
}
_JSON_FIELDS = {"input_summary", "permission_context", "workspace_revision", "result", "error", "metadata"}
_SAFE_EFFECTS = frozenset({"pure_read", "idempotent_state_inspection", "read_only", "pure"})
_IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$")


class JournalConflict(ValueError):
    """An identity, transition or idempotency precondition did not hold."""


class JournalCorruptionError(ValueError):
    """Persisted state cannot be interpreted safely; execution must remain blocked."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      default=lambda item: item.value if isinstance(item, Enum) else str(item))


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise JournalConflict(f"Invalid {label} identity.")
    # Arbitrary provider tokens must never be used as identities.
    if redact_sensitive(value) != value:
        raise JournalConflict(f"Secret-bearing {label} identity.")
    return value


class ExecutionJournal:
    """Transactional intent/state records, scoped to the owning run identity.

    ``fault_hook(boundary, context)`` is an explicit test facility. Before-commit
    hooks run inside the transaction (exceptions roll back); after-commit hooks
    run after durability (process termination leaves the committed state).
    Payload storage is capped; identities and terminal state are never compacted.
    ``max_steps_per_run`` bounds new intents and blocks dispatch when exhausted.
    """

    def __init__(self, db: ProjectDatabase, *, known_secrets: tuple[str, ...] = (),
                 fault_hook: Callable[[str, dict[str, Any]], None] | None = None,
                 max_steps_per_run: int = 20000):
        self.db = db
        self.known_secrets = known_secrets
        self.fault_hook = fault_hook
        self.max_steps_per_run = max(1, max_steps_per_run)

    def _hook(self, boundary: str, **context: Any) -> None:
        if self.fault_hook:
            self.fault_hook(boundary, context)

    def _clean(self, value: Any, limit: int = 16384, *, strict: bool = False) -> Any:
        value = redact_sensitive(value, self.known_secrets)
        encoded = _json(value)
        if len(encoded.encode("utf-8")) <= limit:
            return value
        if strict:
            raise JournalConflict("Safety metadata exceeds the journal storage bound.")
        # Re-redact after truncation so no partial credential from JSON/string
        # conversion can bypass the normal credential filters.
        return {"truncated": True, "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                "original_bytes": len(encoded.encode()),
                "preview": redact_sensitive(encoded[:max(0, limit // 2)], self.known_secrets)}

    def _metadata(self, value: dict | None) -> dict:
        """Safety manifests cannot be truncated into an unusable preview."""
        value = dict(value or {})
        manifest = value.pop("workspace_manifest", None)
        clean = self._clean(value)
        if manifest is not None:
            clean["workspace_manifest"] = self._clean(manifest, 1048576, strict=True)
        return clean

    @contextmanager
    def _transaction(self, operation: str, run_id: str, attempt_id: str = ""):
        context = {"operation": operation, "run_id": run_id, "attempt_id": attempt_id}
        with self.db._lock:
            conn = self.db._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                self._hook("journal.before_commit", **context)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
        self._hook("journal.after_commit", **context)

    @staticmethod
    def _run(conn, run_id: str):
        row = conn.execute("SELECT id, run_id, session_id, project_id, task_id FROM coding_sessions WHERE id=?",
                           (run_id,)).fetchone()
        if row is None or row["run_id"] != row["id"] or not row["project_id"] or not row["session_id"]:
            raise JournalConflict("Execution run identity is missing or inconsistent.")
        return row

    def _decode(self, row, owner=None) -> dict[str, Any]:
        try:
            value = dict(row)
            if value["state"] not in STATES:
                raise ValueError("unknown state")
            for key in ("attempt_id", "run_id", "session_id", "project_id", "step_id", "operation"):
                _identifier(value[key], key)
            if (not re.fullmatch(r"[0-9a-f]{64}", value["input_fingerprint"])
                    or not isinstance(value["version"], int) or value["version"] < 0
                    or value["retry_safe"] not in (0, 1)
                    or bool(value["retry_safe"]) != (value["side_effect"] in _SAFE_EFFECTS)
                    or not value["created_at"] or not value["updated_at"]
                    or (value["state"] in TERMINAL_STATES and not value["terminal_at"])
                    or (value["state"] in {"dispatching", "running"} and not value["started_at"])):
                raise ValueError("inconsistent execution metadata")
            for key in _JSON_FIELDS:
                value[key] = json.loads(value[key])
            if not isinstance(value["metadata"], dict) or not isinstance(value["permission_context"], dict):
                raise ValueError("invalid metadata")
            if owner is not None and any(value[k] != owner[k] for k in ("run_id", "session_id", "project_id", "task_id")):
                raise ValueError("owner mismatch")
            value["retry_safe"] = bool(value["retry_safe"])
            value["safe_to_retry"] = value["retry_safe"] and value["state"] in {"ready", "failed", "cancelled"}
            return redact_sensitive(value, self.known_secrets)
        except (ValueError, TypeError, KeyError) as exc:
            raise JournalCorruptionError("Execution journal state is corrupt; reconciliation required.") from exc

    def _event(self, conn, record: dict[str, Any], event_type: str = "execution_step") -> None:
        sequence = conn.execute("SELECT COALESCE(MAX(sequence), 0)+1 FROM coding_run_events WHERE run_id=?",
                                (record["run_id"],)).fetchone()[0]
        data = {key: record[key] for key in ("run_id", "session_id", "project_id", "task_id", "step_id",
                                             "attempt_id", "operation", "state", "side_effect", "retry_safe",
                                             "updated_at", "version")}
        event_id = f"execution_{record['attempt_id']}_{record['version']}"
        conn.execute("INSERT INTO coding_run_events(id, run_id, session_id, project_id, task_id, sequence, "
                     "event_type, data, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (event_id, record["run_id"], record["session_id"], record["project_id"], record["task_id"],
                      sequence, event_type, _json(data), record["updated_at"]))

    def begin(self, run_id: str, operation: str, *, step_id: str | None = None, attempt_id: str | None = None,
              parent_step_id: str | None = None, agent_id: str = "", input_data: Any = None,
              side_effect: str = "unknown", permission_context: dict | None = None,
              workspace_revision: Any = None, metadata: dict | None = None) -> dict[str, Any]:
        """Commit a ready intent; repeated exact attempt identities are idempotent.

        A different input/owner under an existing identity is always rejected.
        A completed logical step cannot acquire another attempt in the same run.
        Raw input is represented by a SHA-256 fingerprint and a bounded sanitized
        summary, rather than duplicating entire model transcripts.
        """
        run_id = _identifier(run_id, "run")
        operation = _identifier(operation, "operation")
        attempt_id = _identifier(attempt_id or f"attempt_{uuid.uuid4().hex}", "attempt")
        step_id = _identifier(step_id or f"step_{hashlib.sha256(attempt_id.encode()).hexdigest()[:32]}", "step")
        if parent_step_id is not None:
            parent_step_id = _identifier(parent_step_id, "parent step")
        if agent_id:
            agent_id = _identifier(agent_id, "agent")
        side_effect = str(side_effect.value if isinstance(side_effect, Enum) else side_effect)
        if side_effect not in _SAFE_EFFECTS | {"unknown", "conditional_write", "non_idempotent_mutation",
                                             "external_side_effect", "model_request", "mutation", "external"}:
            raise JournalConflict("Unknown side-effect classification.")
        fingerprint = hashlib.sha256(_json(input_data).encode("utf-8")).hexdigest()
        permissions = self._clean(permission_context or {}, 65536, strict=True)
        revision = self._clean(workspace_revision, 524288, strict=True)
        clean_metadata = self._metadata(metadata)
        summary = self._clean(input_data, 4096)
        now = _now()
        with self._transaction("begin", run_id, attempt_id) as conn:
            owner = self._run(conn, run_id)
            existing = conn.execute("SELECT * FROM coding_execution_steps WHERE attempt_id=?", (attempt_id,)).fetchone()
            if existing:
                if existing["run_id"] != run_id:
                    raise JournalConflict("Attempt identity belongs to another run.")
                result = self._decode(existing, owner)
                expected = {"run_id": run_id, "step_id": step_id, "operation": operation,
                            "input_fingerprint": fingerprint, "parent_step_id": parent_step_id,
                            "agent_id": agent_id, "side_effect": side_effect,
                            "permission_context": permissions, "workspace_revision": revision}
                if any(result.get(key) != val for key, val in expected.items()):
                    raise JournalConflict("Attempt identity is already bound to different execution input.")
                return result
            previous = conn.execute("SELECT * FROM coding_execution_steps WHERE run_id=? AND step_id=?",
                                    (run_id, step_id)).fetchall()
            if any(row["state"] not in {"failed", "cancelled"} for row in previous):
                raise JournalConflict("Logical step is completed or requires reconciliation before another attempt.")
            if parent_step_id and not conn.execute(
                    "SELECT 1 FROM coding_execution_steps WHERE run_id=? AND step_id=?", (run_id, parent_step_id)).fetchone():
                raise JournalConflict("Parent step does not belong to this run.")
            count = conn.execute("SELECT COUNT(*) FROM coding_execution_steps WHERE run_id=?", (run_id,)).fetchone()[0]
            if count >= self.max_steps_per_run:
                raise JournalConflict("Execution journal capacity reached; new dispatch is blocked.")
            record = {"attempt_id": attempt_id, "run_id": run_id, "session_id": owner["session_id"],
                      "project_id": owner["project_id"], "task_id": owner["task_id"], "step_id": step_id,
                      "parent_step_id": parent_step_id, "agent_id": agent_id, "operation": operation,
                      "input_fingerprint": fingerprint, "input_summary": summary, "side_effect": side_effect,
                      "permission_context": permissions, "workspace_revision": revision, "state": "ready",
                      "retry_safe": side_effect in _SAFE_EFFECTS, "created_at": now, "started_at": None,
                      "terminal_at": None, "updated_at": now, "result_ref": None, "result": None,
                      "error": None, "metadata": clean_metadata, "version": 0}
            columns = list(record)
            conn.execute(f"INSERT INTO coding_execution_steps({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                         tuple(_json(record[key]) if key in _JSON_FIELDS else record[key] for key in columns))
            self._event(conn, record)
        return self.get(run_id, attempt_id)  # type: ignore[return-value]

    def get(self, run_id: str, attempt_id: str) -> dict[str, Any] | None:
        conn = self.db._get_connection()
        try:
            owner = self._run(conn, run_id)
            row = conn.execute("SELECT * FROM coding_execution_steps WHERE run_id=? AND attempt_id=?",
                               (run_id, attempt_id)).fetchone()
            return self._decode(row, owner) if row else None
        finally:
            conn.close()

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        conn = self.db._get_connection()
        try:
            owner = self._run(conn, run_id)
            rows = conn.execute("SELECT * FROM coding_execution_steps WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
            return [self._decode(row, owner) for row in rows]
        finally:
            conn.close()

    def _transition(self, conn, owner, row, state, *, result=None, error=None, metadata=None, reconciled=False):
        record = self._decode(row, owner)
        old = record["state"]
        clean_result = self._clean(result, 8192) if result is not None else None
        clean_error = self._clean(error, 4096) if error is not None else None
        if old == state:
            # Duplicate completion delivery must not rewrite the durable result.
            compacted_match = (isinstance(record["result"], dict) and record["result"].get("compacted")
                               and record["result"].get("sha256") == hashlib.sha256(_json(clean_result).encode()).hexdigest())
            if ((result is not None and clean_result != record["result"] and not compacted_match)
                    or (error is not None and clean_error != record["error"])):
                raise JournalConflict("Duplicate terminal state carries a conflicting result.")
            return record
        if state not in _ALLOWED.get(old, set()) and not (reconciled and old in UNCERTAIN_STATES and state in TERMINAL_STATES):
            raise JournalConflict(f"Invalid execution transition: {old} -> {state}.")
        now = _now()
        record["state"] = state
        record["version"] += 1
        record["updated_at"] = now
        if state in {"dispatching", "running"} and not record["started_at"]:
            record["started_at"] = now
        if state in TERMINAL_STATES:
            record["terminal_at"] = now
        if result is not None:
            record["result"] = clean_result
            record["result_ref"] = f"sqlite:execution/{record['attempt_id']}/result"
        if error is not None:
            record["error"] = clean_error
        if metadata:
            record["metadata"] = self._metadata({**record["metadata"], **metadata})
        conn.execute("UPDATE coding_execution_steps SET state=?,version=?,updated_at=?,started_at=?,terminal_at=?,"
                     "result=?,result_ref=?,error=?,metadata=? WHERE attempt_id=? AND run_id=?",
                     (state, record["version"], now, record["started_at"], record["terminal_at"], _json(record["result"]),
                      record["result_ref"], _json(record["error"]), _json(record["metadata"]), record["attempt_id"], record["run_id"]))
        self._event(conn, record, "execution_reconciled" if reconciled else "execution_step")
        record["safe_to_retry"] = record["retry_safe"] and state in {"ready", "failed", "cancelled"}
        return record

    def transition(self, run_id: str, attempt_id: str, state: str, *, result=None, error=None,
                   metadata: dict | None = None) -> dict[str, Any]:
        if state not in STATES:
            raise JournalConflict("Unknown execution state.")
        with self._transaction("transition", run_id, attempt_id) as conn:
            owner = self._run(conn, run_id)
            row = conn.execute("SELECT * FROM coding_execution_steps WHERE run_id=? AND attempt_id=?",
                               (run_id, attempt_id)).fetchone()
            if row is None:
                raise JournalConflict("Execution attempt not found in this run.")
            result_record = self._transition(conn, owner, row, state, result=result, error=error, metadata=metadata)
        return result_record

    def recover_interrupted(self, run_id: str) -> list[dict[str, Any]]:
        """Mark unconfirmed dispatches atomically; do not perform or retry work."""
        recovered = []
        with self._transaction("recover", run_id) as conn:
            owner = self._run(conn, run_id)
            rows = conn.execute("SELECT * FROM coding_execution_steps WHERE run_id=? AND state IN ('dispatching','running')",
                                (run_id,)).fetchall()
            for row in rows:
                recovered.append(self._transition(conn, owner, row, "outcome_unknown", metadata={
                    "recovery_reason": "process_interrupted", "previous_state": row["state"],
                    "requires_reconciliation": True, "automatic_replay_permitted": False}))
        return recovered

    def reconcile(self, run_id: str, attempt_id: str, outcome: str, *, evidence: Any, result=None) -> dict[str, Any]:
        """Record an explicitly inspected outcome; evidence is mandatory, never inferred."""
        if outcome not in TERMINAL_STATES or not evidence or not str(evidence).strip():
            raise JournalConflict("Reconciliation requires a terminal outcome and inspection evidence.")
        with self._transaction("reconcile", run_id, attempt_id) as conn:
            owner = self._run(conn, run_id)
            row = conn.execute("SELECT * FROM coding_execution_steps WHERE run_id=? AND attempt_id=?",
                               (run_id, attempt_id)).fetchone()
            if row is None:
                raise JournalConflict("Execution attempt not found in this run.")
            current = self._decode(row, owner)
            if current["state"] not in UNCERTAIN_STATES and current["state"] != outcome:
                raise JournalConflict("Only an uncertain attempt can be reconciled.")
            record = self._transition(conn, owner, row, outcome, result=result, reconciled=True, metadata={
                "reconciliation": {"evidence": self._clean(evidence, 4096), "recorded_at": _now(), "outcome": outcome},
                "requires_reconciliation": False})
        return record

    def compact(self, run_id: str, *, keep_results: int = 100) -> int:
        """Drop old payload bodies only for fully terminal runs; keep outcome identities.

        Uncertain and resumable runs remain protected. Event records are kept so
        existing projection reconstruction retains the complete state sequence.
        """
        with self._transaction("compact", run_id) as conn:
            self._run(conn, run_id)
            status = conn.execute("SELECT status FROM coding_sessions WHERE id=?", (run_id,)).fetchone()[0]
            if status not in {"completed", "failed", "cancelled"}:
                return 0
            if conn.execute("SELECT 1 FROM coding_execution_steps WHERE run_id=? AND state NOT IN ('completed','failed','cancelled')",
                            (run_id,)).fetchone():
                return 0
            rows = conn.execute("SELECT attempt_id,result FROM coding_execution_steps WHERE run_id=? AND result!='null' "
                                "ORDER BY rowid DESC LIMIT -1 OFFSET ?", (run_id, max(0, keep_results))).fetchall()
            for row in rows:
                marker = {"compacted": True, "sha256": hashlib.sha256(row["result"].encode()).hexdigest()}
                conn.execute("UPDATE coding_execution_steps SET result=?,input_summary='null' WHERE attempt_id=?",
                             (_json(marker), row["attempt_id"]))
            return len(rows)
