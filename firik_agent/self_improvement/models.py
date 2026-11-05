"""Versioned records. Approval state is owned by the host, never model output."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def identifier(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass
class Experience:
    id: str
    idempotency_key: str
    session_id: str
    event_type: str
    project_id: str | None = None
    task_id: str | None = None
    source_reference: str = ""
    provider: str = ""
    model: str = ""
    harness: str = "native"
    outcome: str = "unknown"
    verified: bool = False
    facts: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now)
    schema_version: int = 1


@dataclass
class MemoryRecord:
    memory_id: str
    content: str
    scope: str
    kind: str
    project_id: str | None = None
    source_type: str = "user"
    source_reference: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    verification_status: str = "unverified"
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    last_used_at: str | None = None
    expires_at: str | None = None
    supersedes_id: str | None = None
    revision: int = 1
    privacy_classification: str = "project"
    user_approved: bool = False
    status: str = "active"
    pinned: bool = False
    schema_version: int = 1


@dataclass
class ImprovementProposal:
    id: str
    kind: str
    title: str
    payload: dict[str, Any]
    project_id: str | None = None
    evidence_ids: list[str] = field(default_factory=list)
    source_type: str = "reflection"
    source_reference: str = ""
    status: str = "draft"
    revision: int = 1
    digest: str = ""
    evaluation: dict[str, Any] = field(default_factory=dict)
    applied: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    schema_version: int = 1


def record_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
