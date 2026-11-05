"""Scoped approved knowledge, historical corrections and bounded lexical recall."""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from typing import Any

from .models import MemoryRecord, digest, identifier, now, record_dict
from .privacy import validate_knowledge

KINDS = {"working", "episodic", "semantic", "procedural", "failure", "preference", "project", "global"}


def normalized(text: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


class MemoryEngine:
    def __init__(self, store):
        self.store = store

    def prepare(self, payload: dict, *, project_id=None, source_type="user") -> dict:
        content = validate_knowledge(payload.get("content", ""))
        kind = payload.get("kind", "semantic")
        if kind not in KINDS or kind == "procedural":
            raise ValueError("Procedures belong to the existing skill registry; invalid memory kind")
        explicit = source_type == "user"
        if not project_id and not explicit:
            raise ValueError("Inferred project evidence cannot become global knowledge")
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            if not isinstance(expires_at, str):
                raise ValueError("Expiry must be an ISO timestamp with a timezone")
            expiry = dt.datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                raise ValueError("Expiry must include a timezone")
            expires_at = expiry.astimezone(dt.timezone.utc).isoformat()
        supersedes = payload.get("supersedes_id")
        previous = self.store.get("memories", supersedes) if supersedes else None
        if supersedes:
            if not previous or previous["project_id"] != project_id or previous["status"] != "active":
                raise ValueError("Correction must refer to an active memory in the same scope")
            if previous["verification_status"] == "user_confirmed" and not explicit:
                raise ValueError("Inference cannot replace user-confirmed knowledge")
        return {**payload, "content": content, "kind": kind,
                "scope": "project" if project_id else "global", "project_id": project_id,
                "source_type": source_type, "confidence": 1.0 if explicit else min(float(payload.get("confidence", 0.5)), 0.85),
                "verification_status": "user_confirmed" if explicit else payload.get("verification_status", "hypothesis"),
                "base_memory_revision": previous["revision"] if previous else None,
                "expires_at": expires_at,
                "fact_key": previous.get("fact_key") if previous else payload.get("fact_key", digest(normalized(content))),
                "diff": ("--- previous memory\n+++ proposed memory\n" +
                         ("-" + previous["content"] + "\n" if previous else "") + "+" + content + "\n")}

    def validate(self, payload: dict, evidence: list[dict]) -> dict:
        reasons = []
        try:
            validate_knowledge(payload.get("content", ""))
        except ValueError as error:
            reasons.append(str(error))
        if payload.get("verification_status") == "hypothesis":
            reasons.append("An unverified hypothesis cannot be activated as knowledge")
        if payload.get("expires_at") and payload["expires_at"] <= now():
            reasons.append("Knowledge expired before approval")
        if payload.get("source_type") != "user" and not any(item.get("verified") for item in evidence):
            reasons.append("No verified host evidence supports this record")
        if payload.get("supersedes_id"):
            previous = self.store.get("memories", payload["supersedes_id"])
            if not previous or previous["status"] != "active" or previous["revision"] != payload.get("base_memory_revision"):
                reasons.append("Memory base revision is stale")
        return {"passed": not reasons, "checks": ["privacy", "scope", "evidence", "revision"],
                "blocked_reasons": reasons, "method": "host_deterministic", "measured": None}

    def apply(self, proposal: dict, *, conn) -> dict:
        payload = proposal["payload"]
        project_id = proposal.get("project_id")
        previous_id = payload.get("supersedes_id")
        previous = self.store.get("memories", previous_id, conn=conn) if previous_id else None
        if previous_id and (not previous or previous["status"] != "active"
                            or previous["revision"] != payload.get("base_memory_revision")):
            raise ValueError("Stale memory correction")
        records = self.store.list("memories", limit=10000, conn=conn)
        for record in records:
            if record["project_id"] != project_id or record["status"] != "active" or record["memory_id"] == previous_id:
                continue
            if normalized(record["content"]) == normalized(payload["content"]):
                record["evidence_ids"] = sorted(set(record["evidence_ids"] + proposal["evidence_ids"]))
                record["updated_at"] = now()
                self.store.put("memories", record, conn=conn, replace=True)
                return {"memory_id": record["memory_id"], "deduplicated": True,
                        "rollback_eligible": False, "reason": "Existing active knowledge retained; supporting evidence merged"}
            if record.get("fact_key") == payload.get("fact_key"):
                raise ValueError("Contradictory knowledge: stage an explicit correction against the current record")
        memory = record_dict(MemoryRecord(memory_id=identifier("mem"), content=payload["content"],
            scope=payload["scope"], project_id=project_id, kind=payload["kind"],
            source_type=payload["source_type"], source_reference=proposal["source_reference"],
            evidence_ids=proposal["evidence_ids"], confidence=payload["confidence"],
            verification_status=payload["verification_status"], supersedes_id=previous_id,
            revision=previous["revision"] + 1 if previous else 1, user_approved=True,
            privacy_classification="project" if project_id else "user_approved_global",
            expires_at=payload.get("expires_at"), pinned=bool(payload.get("pinned", False))))
        memory["fact_key"] = payload["fact_key"]
        memory["proposal_id"] = proposal["id"]
        if previous:
            previous["status"] = "superseded"
            previous["updated_at"] = now()
            self.store.put("memories", previous, conn=conn, replace=True)
        self.store.put("memories", memory, conn=conn)
        return {"memory_id": memory["memory_id"], "previous_id": previous_id, "revision": memory["revision"],
                "rollback_eligible": True}

    def rollback(self, proposal: dict, *, conn) -> dict:
        applied = proposal["applied"]
        if not applied.get("rollback_eligible"):
            raise ValueError("This proposal has no independently reversible memory change")
        memory = self.store.get("memories", applied["memory_id"], conn=conn)
        if not memory or memory["status"] != "active" or memory["revision"] != applied["revision"]:
            raise ValueError("Memory changed after application; stage a correction instead")
        memory["status"] = "rolled_back"
        self.store.put("memories", memory, conn=conn, replace=True)
        if applied.get("previous_id"):
            previous = self.store.get("memories", applied["previous_id"], conn=conn)
            if previous and previous["status"] == "superseded":
                previous["status"] = "active"
                self.store.put("memories", previous, conn=conn, replace=True)
        return {"restored_id": applied.get("previous_id"), "removed_id": memory["memory_id"]}

    def retrieve(self, query: str, *, project_id=None, token_budget=600) -> list[dict[str, Any]]:
        # Explicit project-local SQL predicate is mandatory even if a future
        # semantic backend returns IDs from another project.
        terms = set(normalized(query[:4000]).split())
        if not terms or token_budget <= 0:
            return []
        conn = self.store.database._get_connection()
        try:
            rows = conn.execute("SELECT data FROM improvement_memories WHERE status='active' "
                                "AND (project_id=? OR project_id IS NULL) ORDER BY created_at DESC LIMIT 2000",
                                (project_id,)).fetchall()
            fts_ids = set()
            if self.store.fts_available:
                expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in sorted(terms)[:32])
                try:
                    fts_ids = {row[0] for row in conn.execute("SELECT memory_id FROM improvement_memory_fts WHERE content MATCH ? LIMIT 2000",
                                                             (expression,)).fetchall()}
                except Exception:
                    # Lexical scanning of the same scoped records is functional.
                    fts_ids = set()
            candidates = []
            for row in rows:
                memory = json.loads(row[0])
                if not memory["user_approved"] or memory["verification_status"] not in {"observed", "user_confirmed", "verified"}:
                    continue
                if memory.get("expires_at") and memory["expires_at"] <= now():
                    continue
                try:
                    validate_knowledge(memory["content"])
                except ValueError:
                    continue
                overlap = terms & set(normalized(memory["content"]).split())
                if not overlap:
                    continue
                age_days = max(0, (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(memory["updated_at"])).days)
                score = len(overlap) / len(terms) + 0.1 * memory["confidence"] + (0.25 if memory["project_id"] else 0)
                score += (0.1 if memory["pinned"] else 0) + 0.05 / (1 + age_days / 30)
                reason = "Lexical match: " + ", ".join(sorted(overlap)[:8]) + "; approved " + memory["scope"] + " knowledge"
                item = {**memory, "id": memory["memory_id"], "reason": reason, "score": round(score, 5),
                        "retrieval_method": "fts5+lexical" if memory["memory_id"] in fts_ids else "lexical",
                        "estimate_only": True}
                # Budget the rendered context data, not revision/audit metadata
                # retained only for the inspector. UTF-8 bytes are a conservative
                # local upper bound; actual provider usage remains unknown.
                envelope = {"öğrenilmiş_kayıt": item["id"], "tür": item["kind"], "kapsam": item["scope"],
                            "kaynak": item["source_reference"], "gerekçe": reason, "içerik": item["content"]}
                item["token_estimate"] = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) + 16
                candidates.append(item)
        finally:
            conn.close()
        selected, used = [], 0
        for item in sorted(candidates, key=lambda x: (x["score"], x["updated_at"]), reverse=True):
            if used + item["token_estimate"] > token_budget:
                continue
            used += item["token_estimate"]
            selected.append(item)
        if selected:
            with self.store.transaction() as conn:
                for item in selected:
                    latest = self.store.get("memories", item["id"], conn=conn)
                    if latest and latest["status"] == "active":
                        latest["last_used_at"] = now()
                        self.store.put("memories", latest, conn=conn, replace=True)
                self.store.put("retrievals", {"id": identifier("recall"), "project_id": project_id, "created_at": now(),
                    "status": "recorded", "query_digest": digest(query[:4000]), "memory_ids": [i["id"] for i in selected],
                    "token_estimate": used, "estimate_only": True, "token_budget": token_budget}, conn=conn)
        return selected
