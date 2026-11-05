"""Allowlisted metadata observations, with durable evidence identity checks."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
from typing import Any, Mapping

from .models import Experience, digest, identifier, record_dict
from .privacy import safe_label, validate_knowledge

OBSERVED_EVENTS = {"tool_call_started", "tool_call_result", "tool_call_error", "error", "recovery",
                   "verification", "quality_gate", "context_compacted", "context_compaction_started",
                   "model_request_started", "model_request_finished", "model_usage", "task_state",
                   "done", "skill.activated", "workspace_conflict", "user_feedback"}
_TEXT = {"status", "failure_kind", "action", "reason_code", "submission_phase", "evidence_id",
         "workspace_hash", "completion_classification", "execution_backend", "request_id", "request_kind",
         "skill_name", "gate_id", "kind", "run_id", "capability", "capability_source"}
_NUMBERS = {"exit_code", "workspace_revision", "retry_count", "iteration", "input_tokens", "output_tokens",
            "total_tokens", "prompt_tokens", "completion_tokens", "cost", "cost_usd", "latency_ms", "ttft_ms"}
_BOOLS = {"executed", "passed", "fresh", "source_tree_matches", "source_unchanged"}


def _mapping(event: Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        return dict(event)
    if hasattr(event, "data"):
        return {**dict(event.data), "event_id": event.id, "event_type": event.event_type}
    if is_dataclass(event):
        return asdict(event)
    return {}


class ExperienceCollector:
    def __init__(self, store):
        self.store = store

    def collect(self, event: Any, *, session_id: str, project_id=None, task_id=None,
                provider="", model="", harness="native", max_events=256, max_records=10000) -> dict[str, Any] | None:
        data = _mapping(event)
        durable_id = data.get("event_id")
        trusted = False
        if durable_id:
            conn = self.store.database._get_connection()
            try:
                row = conn.execute("SELECT * FROM coding_run_events WHERE id=?", (str(durable_id),)).fetchone()
                if row is None or row["session_id"] != session_id or row["project_id"] != project_id:
                    raise ValueError("Learning event identity does not match durable evidence")
                # Re-read actual host evidence; ignore a caller's replaced metadata.
                data = {**json.loads(row["data"]), "event_id": row["id"], "event_type": row["event_type"]}
                task_id = row["task_id"]
                trusted = True
            finally:
                conn.close()
        kind = str(getattr(data.get("event_type", data.get("type", "")), "value",
                           data.get("event_type", data.get("type", ""))))
        if kind not in OBSERVED_EVENTS:
            return None
        metadata = data.get("metadata", {})
        if not isinstance(metadata, Mapping):
            metadata = {}
        # Reflection-generated evidence is terminal: no recursive learning jobs.
        if metadata.get("origin") == "self_improvement" or data.get("origin") == "self_improvement":
            return None
        facts: dict[str, Any] = {}
        for key in _TEXT:
            value = metadata.get(key, data.get(key))
            if isinstance(value, str):
                facts[key] = safe_label(value)
        for key in _NUMBERS:
            value = metadata.get(key, data.get(key))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 10**15:
                if value == value and value not in (float("inf"), float("-inf")):
                    facts[key] = value
        for key in _BOOLS:
            value = metadata.get(key, data.get(key))
            if isinstance(value, bool):
                facts[key] = value
        if trusted and isinstance(data.get("run_id"), str):
            facts["run_id"] = safe_label(data["run_id"])
        for key in ("usage", "metrics"):
            source = metadata.get(key, data.get(key))
            if isinstance(source, Mapping):
                facts[key] = {name: value for name, value in source.items() if name in _NUMBERS
                              and (value is None or isinstance(value, (int, float)) and not isinstance(value, bool)
                                   and value == value and abs(value) < 10**15)}
                if key == "usage" and isinstance(source.get("currency"), str):
                    facts[key]["currency"] = safe_label(source["currency"], 8)
        tool = data.get("tool_name") or metadata.get("tool_name")
        if isinstance(tool, str):
            facts["tool_name"] = safe_label(tool, 80)
        duration = data.get("duration_ms")
        if isinstance(duration, (int, float)) and 0 <= duration < 86400000:
            facts["duration_ms"] = duration
        # Commands are retained only from real host verification, never tool
        # arguments/output. Secret-bearing commands are omitted, not redacted rules.
        command = metadata.get("command", data.get("command"))
        if trusted and kind == "verification" and isinstance(command, str):
            try:
                facts["command"] = validate_knowledge(command, limit=240)
            except ValueError:
                pass
        host_test = (trusted and kind in {"verification", "quality_gate"}
                     and facts.get("executed") is True and facts.get("fresh") is True
                     and facts.get("passed") is True and facts.get("exit_code") == 0
                     and bool(facts.get("evidence_id")) and bool(facts.get("workspace_hash")))
        completion = (trusted and kind == "quality_gate" and facts.get("passed") is True
                      and facts.get("completion_classification") == "completed_verified"
                      and bool(metadata.get("evidence_ids", data.get("evidence_ids"))))
        verified = host_test or completion
        outcome = "verified_success" if verified else "failed" if (
            kind in {"tool_call_error", "error"} or facts.get("passed") is False
            or facts.get("status") in {"failed", "blocked"}) else "unknown"
        identity = str(durable_id or data.get("id") or digest({"session": session_id, "kind": kind,
                       "timestamp": data.get("timestamp"), "facts": facts}))
        key = digest([session_id, project_id, identity])
        previous = self.store.by_key("experiences", key)
        if previous:
            return previous
        record = record_dict(Experience(id=identifier("exp"), idempotency_key=key, session_id=session_id,
            project_id=project_id, task_id=task_id, event_type=kind, source_reference=str(durable_id or "runtime:" + identity),
            provider=safe_label(provider), model=safe_label(model), harness=safe_label(harness),
            outcome=outcome, verified=bool(verified), facts=facts))
        # Concurrency-safe bound and duplicate check in the same transaction.
        with self.store.transaction() as conn:
            count = conn.execute("SELECT COUNT(*) FROM improvement_experiences WHERE session_id=?", (session_id,)).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM improvement_experiences").fetchone()[0]
            if count >= max_events or total >= max_records:
                return None
            self.store.put("experiences", record, conn=conn)
        return self.store.by_key("experiences", key)
