"""Host-owned lifecycle for bounded, auditable learning.

This is a service, not a second agent runtime. Its worker has no tool registry,
credentials, shell or network. Only explicit CLI/desktop calls can promote.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import threading
import time
from typing import Any
import weakref

from firik_agent.core.metrics import RunMetrics
from firik_agent.projects.db import ProjectDatabase
from .config import ImprovementConfig
from .experience import ExperienceCollector
from .memory_engine import MemoryEngine
from .models import ImprovementProposal, digest, identifier, now, record_dict
from .privacy import safe_label
from .reflection import ReflectionEngine
from .storage import LearningStore

_REGISTRY_LOCK = threading.RLock()
_REGISTRY: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def coordinator_for(database: ProjectDatabase, config=None, skill_manager=None, plugin_manager=None, config_path=None):
    key = str(database.db_path.resolve())
    with _REGISTRY_LOCK:
        existing = _REGISTRY.get(key)
        if existing is None or existing._closed:
            existing = ImprovementCoordinator(database, config, skill_manager, plugin_manager=plugin_manager, config_path=config_path)
            _REGISTRY[key] = existing
        else:
            if config is not None:
                existing._base_config = ImprovementConfig.from_config(config).model_dump()
                existing._application_config = config
            if skill_manager is not None:
                existing.skill_manager = skill_manager
                if existing._candidate_engine is not None:
                    existing._candidate_engine.skill_manager = skill_manager
            if config_path is not None:
                existing.config_path = Path(config_path).resolve()
            if plugin_manager is not None:
                existing.bind_runtime(plugin_manager=plugin_manager)
        database._improvement_coordinator = existing
        if plugin_manager is not None:
            existing.bind_runtime(plugin_manager=plugin_manager)
        return existing


class ImprovementCoordinator:
    def __init__(self, database: ProjectDatabase, config=None, skill_manager=None, *, plugin_manager=None, config_path=None):
        self.database = database
        self._application_config = config or {}
        self._base_config = ImprovementConfig.from_config(config).model_dump()
        self.store = LearningStore(database)
        self.collector = ExperienceCollector(self.store)
        self.memory = MemoryEngine(self.store)
        self.reflection = ReflectionEngine()
        self.skill_manager = skill_manager
        self.plugin_manager = plugin_manager
        self.config_path = Path(config_path).resolve() if config_path else None
        self._restored_runtime = None
        self._candidate_engine = None
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.RLock()
        self._cancelled: dict[str, threading.Event] = {}
        self._futures = {}
        self._closed = False
        self._experiment_lock = threading.Lock()
        self._periodic_timer = None
        self._schedule_periodic()

    def settings(self) -> dict:
        settings = ImprovementConfig.from_config({**self._base_config, **self.store.meta("settings", {})}).model_dump()
        self.store.max_records = settings["max_records"]
        return settings

    @property
    def mode(self):
        return self.settings()["mode"]

    @property
    def automatic_reflection(self):
        settings = self.settings()
        return settings["automatic_reflection"] and settings["mode"] in {"propose", "experiment"}

    def configure(self, updates: dict) -> dict:
        if not isinstance(updates, dict):
            raise ValueError("Settings updates must be a mapping")
        result = ImprovementConfig.from_config({**self.settings(), **updates}).model_dump()
        # Persist only explicit overrides. Application configuration remains the
        # owner of its defaults; user desktop/CLI decisions have precedence.
        with self.store.transaction() as conn:
            prior = self.store.meta("settings", {})
            self.store.set_meta("settings", {**prior, **updates}, conn=conn)
            self.store.audit("settings_changed", actor="user", details={"keys": sorted(updates)}, conn=conn)
        if result["mode"] == "off" or not result["automatic_reflection"]:
            self.cancel()
        self._schedule_periodic()
        return result

    def _schedule_periodic(self):
        with self._lock:
            if self._periodic_timer is not None:
                self._periodic_timer.cancel()
                self._periodic_timer = None
            seconds = self.settings()["periodic_review_seconds"]
            if self._closed or not self.automatic_reflection or not seconds:
                return
            self._periodic_timer = threading.Timer(max(60, seconds), self._periodic_tick)
            self._periodic_timer.daemon = True
            self._periodic_timer.start()

    def _periodic_tick(self):
        try:
            if self._closed or not self.automatic_reflection:
                return
            settings = self.settings()
            self.store.prune(settings["retention_days"], settings["max_records"])
            selected = set()
            for row in self.store.list("experiences", limit=2000):
                pair = (row["session_id"], row.get("project_id"))
                if pair in selected or not self.project_enabled(row.get("project_id")):
                    continue
                if row["outcome"] != "failed" and not row["verified"]:
                    continue
                selected.add(pair)
                self.submit_reflection(*pair)
                if len(selected) >= self.settings()["max_pending_jobs"]:
                    break
        finally:
            self._schedule_periodic()

    def project_enabled(self, project_id=None) -> bool:
        if self._closed or self.mode == "off":
            return False
        if not project_id:
            return True
        if project_id in self.settings()["private_projects"]:
            return False
        project = self.database.get_project(project_id)
        if project is None:
            return False
        prefs = self.database.get_settings(project_id).coding_preferences
        learning = prefs.get("self_improvement", {})
        return not (learning is False or prefs.get("private") is True or prefs.get("learning_enabled") is False
                    or isinstance(learning, dict) and (learning.get("enabled") is False or learning.get("private") is True))

    def _enabled(self, project_id=None):
        if not self.project_enabled(project_id):
            raise PermissionError("Learning is disabled or the project is private")

    def _candidates(self):
        if self._candidate_engine is None:
            from .candidates import CandidateEngine
            self._candidate_engine = CandidateEngine(self.skill_manager, plugin_manager=self.plugin_manager,
                state_root=self.database.db_path.parent.resolve() / (self.database.db_path.stem + "-improvement"))
            self._candidate_engine.runtime_authorizer = self._tool_authorized
        return self._candidate_engine

    def _claim_experiment(self):
        """Reserve candidate execution across instances sharing this database.

        The transaction contains only lease inspection and persistence. Git,
        model requests and candidate execution run after its commit. A dead
        owner's lease may be replaced by this explicit host operation; durable
        experiment/job keys still prohibit replaying an unknown attempt.
        """
        from firik_agent.projects.coding_sessions import _owner_alive, _process_start
        if not self._experiment_lock.acquire(blocking=False):
            raise RuntimeError("An improvement experiment is already active")
        try:
            token = identifier("candidate_lease")
            owner = {"token": token, "owner_token": token, "owner_pid": os.getpid(),
                     "owner_host": socket.gethostname(), "owner_start": _process_start(os.getpid()),
                     "created_at": now(), "schema_version": 1}
            with self.store.transaction() as conn:
                row = conn.execute("SELECT value FROM improvement_meta WHERE key='candidate_execution_lease'").fetchone()
                previous = json.loads(row[0]) if row else None
                if previous is not None:
                    if (not isinstance(previous, dict) or type(previous.get("owner_pid")) is not int
                            or previous["owner_pid"] <= 0 or not isinstance(previous.get("token"), str)
                            or not previous["token"] or not isinstance(previous.get("owner_host"), str)
                            or not previous["owner_host"] or not isinstance(previous.get("owner_start"), str)):
                        raise RuntimeError("Candidate execution lease is unknown; inspect it before starting another experiment")
                    # Coding owners have a different same-process token registry.
                    # Our current PID is always treated as live; external PID
                    # checks reuse the existing platform-aware implementation.
                    # In particular, that implementation never os.kill's Windows.
                    alive = previous["owner_pid"] == os.getpid() or _owner_alive({
                        **previous, "owner_token": previous["token"]})
                    if alive:
                        raise RuntimeError("An improvement experiment is already active in another coordinator")
                    self.store.audit("candidate_lease_recovered", details={
                        "previous_owner_pid": previous["owner_pid"], "reason": "explicit_candidate_request",
                        "unknown_operations_replayed": False}, conn=conn)
                self.store.set_meta("candidate_execution_lease", owner, conn=conn)
            self._experiment_lease_token = token
            return token
        except BaseException:
            self._experiment_lock.release()
            raise

    def _release_experiment(self, token):
        """Release only the exact lease claimed by this coordinator."""
        if not isinstance(token, str) or not token or token != getattr(self, "_experiment_lease_token", None):
            return False
        released = False
        try:
            with self.store.transaction() as conn:
                row = conn.execute("SELECT value FROM improvement_meta WHERE key='candidate_execution_lease'").fetchone()
                owner = json.loads(row[0]) if row else None
                if isinstance(owner, dict) and owner.get("token") == token:
                    self.store.set_meta("candidate_execution_lease", None, conn=conn)
                    released = True
            return released
        finally:
            self._experiment_lease_token = None
            self._experiment_lock.release()

    def _candidate_capacity(self, repository, *, reserve=1):
        settings = self.settings()
        if sum(self.store.counts()["proposals"].values()) >= settings["max_records"]:
            raise ValueError("Proposal capacity reached before candidate creation")
        manager = self._candidates()._code_manager({"repository": str(Path(repository).absolute())})
        active = [record for record in manager.list_workspaces() if record.get("state") != "destroyed"]
        if len(active) + reserve > settings["max_candidate_workspaces"]:
            raise ValueError("Retained candidate workspace limit reached; inspect existing workspaces before creating more")
        size = files = 0
        code_root = self._candidates().state_root / "code"
        for directory, dirs, names in os.walk(code_root, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in [*dirs, *names]):
                raise ValueError("Candidate storage contains an unexpected link")
            for name in names:
                size += (Path(directory) / name).stat().st_size
                files += 1
                if files > 50000:
                    raise ValueError("Candidate storage scan limit reached")
        if size + reserve * 32 * 1024 * 1024 > settings["candidate_storage_mb"] * 1024 * 1024:
            raise ValueError("Candidate storage budget would be exceeded")

    def _tool_authorized(self, project_id, candidate_identity):
        if not self.project_enabled(project_id):
            return False
        for row in self.store.list("proposals", project_id=project_id, status="applied", limit=1000):
            if row["kind"] == "tool" and row.get("project_id") == project_id and row.get("applied", {}).get("candidate_sha256") == candidate_identity:
                return row["digest"] == self._proposal_digest(row)
        return False

    def _skill_manager(self):
        if self.skill_manager is None:
            from firik_agent.skills.manager import SkillManager
            section = self._application_config.get("skills", {})
            directory = section.get("directory") if isinstance(section, dict) else None
            directory = Path(directory or "skills")
            if not directory.is_absolute() and self.config_path:
                directory = self.config_path.parent / directory
            self.skill_manager = SkillManager(directory)
            if self._candidate_engine is not None:
                self._candidate_engine.skill_manager = self.skill_manager
        return self.skill_manager

    def bind_runtime(self, *, plugin_manager=None):
        """Restore only durable, validated applied tools into an existing registry."""
        if plugin_manager is None:
            return
        self.plugin_manager = plugin_manager
        engine = self._candidates()
        engine.plugin_manager = plugin_manager
        engine.runtime_authorizer = self._tool_authorized
        if not self.project_enabled():
            return
        applied = self.store.list("proposals", status="applied", limit=1000)
        fingerprint = (id(plugin_manager), digest([(row["id"], row["revision"]) for row in applied if row["kind"] == "tool"]))
        if self._restored_runtime == fingerprint:
            return
        self._restored_runtime = fingerprint
        for proposal in applied:
            if proposal["kind"] != "tool" or not self.project_enabled(proposal["project_id"]):
                continue
            try:
                self._proposal(proposal["id"])
                if proposal["project_id"] is not None:
                    # Project-specific tools need a project-bound registry; do
                    # not leak them into the global ordinary-chat registry.
                    continue
                engine.restore_approved_tool(proposal["payload"])
            except (ValueError, OSError):
                self.store.audit("tool_restore_blocked", proposal_id=proposal["id"])

    def project_skill_approvals(self, project_id):
        if not self.project_enabled(project_id):
            return []
        result = []
        for proposal in self.store.list("proposals", project_id=project_id, status="applied", limit=1000):
            if proposal["kind"] == "skill" and proposal.get("evaluation", {}).get("passed") is True:
                self._proposal(proposal["id"])
                result.append({"name": proposal["payload"]["name"], "target": proposal["applied"].get("target"),
                               "after_sha256": proposal["applied"].get("after_sha256")})
        return result

    def observe_event(self, event, *, session_id, project_id=None, task_id=None,
                      provider="", model="", harness="native", private=False):
        if private or not self.project_enabled(project_id):
            return None
        settings = self.settings()
        counts = self.store.counts()["experiences"]
        if sum(counts.values()) >= settings["max_records"]:
            self.store.prune(settings["retention_days"], settings["max_records"] - 1)
            if sum(self.store.counts()["experiences"].values()) >= settings["max_records"]:
                return None
        return self.collector.collect(event, session_id=str(session_id), project_id=project_id, task_id=task_id,
            provider=provider, model=model, harness=harness, max_events=settings["max_events_per_session"], max_records=settings["max_records"])

    def ingest_session(self, session_id, project_id=None):
        self._enabled(project_id)
        from firik_agent.projects.coding_sessions import CodingSessionStore
        conn = self.database._get_connection()
        try:
            rows = conn.execute("SELECT id,session_id,project_id,task_id,provider,model FROM coding_sessions "
                                "WHERE (session_id=? OR id=?) ORDER BY created_at DESC LIMIT 20", (session_id, session_id)).fetchall()
        finally:
            conn.close()
        captured = 0
        logical_session = str(session_id)
        resolved_project = project_id
        sessions = CodingSessionStore(self.database)
        for row in rows:
            if project_id and row["project_id"] != project_id:
                continue
            if not self.project_enabled(row["project_id"]):
                continue
            logical_session, resolved_project = row["session_id"], row["project_id"]
            for event in sessions.list_events(row["id"], include_deltas=False,
                    limit=self.settings()["max_events_per_session"]):
                if self.observe_event(event, session_id=row["session_id"], project_id=row["project_id"],
                        task_id=row["task_id"], provider=row["provider"], model=row["model"]):
                    captured += 1
        return {"captured": captured, "session_id": logical_session, "project_id": resolved_project}

    def _evidence(self, ids: list[str], project_id=None) -> list[dict]:
        result = []
        for key in ids[:2000]:
            row = self.store.get("experiences", key)
            if row is None or row.get("project_id") != project_id:
                raise ValueError("Missing evidence or evidence belongs to another project")
            result.append(row)
        return result

    @staticmethod
    def _proposal_digest(proposal: dict) -> str:
        return digest({key: proposal.get(key) for key in ("kind", "project_id", "payload", "evidence_ids", "source_type", "source_reference")})

    def _new_proposal(self, kind, title, payload, *, project_id=None, evidence_ids=None,
                      source_type="user", source_reference="user", idempotency_key=None):
        self._enabled(project_id)
        if kind not in {"memory", "skill", "tool", "harness", "code"}:
            raise ValueError("Unknown proposal kind")
        evidence_ids = sorted(set(evidence_ids or []))
        self._evidence(evidence_ids, project_id)
        if source_type != "user" and not evidence_ids:
            raise ValueError("Reflection proposals require actual evidence")
        if kind == "memory":
            if source_type != "user" and payload.get("kind") == "failure" and not payload.get("expires_at"):
                import datetime as dt
                payload = {**payload, "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=self.settings()["retention_days"])).isoformat()}
            prepared = self.memory.prepare(payload, project_id=project_id, source_type=source_type)
        else:
            if kind == "skill":
                self._skill_manager()
                payload = {**payload, "evidence_ids": evidence_ids}
                if project_id:
                    payload["project_root"] = self.database.get_project(project_id).local_path
            prepared = self._candidates().prepare(kind, payload, project_id=project_id)
        proposal = record_dict(ImprovementProposal(id=identifier("proposal"), kind=kind,
            title=safe_label(title, 200), payload=prepared, project_id=project_id, evidence_ids=evidence_ids,
            source_type=source_type, source_reference=safe_label(source_reference, 240)))
        proposal["digest"] = self._proposal_digest(proposal)
        proposal["idempotency_key"] = idempotency_key or digest([project_id, kind, prepared, evidence_ids])
        with self.store.transaction() as conn:
            existing = self.store.by_key("proposals", proposal["idempotency_key"], conn=conn)
            if existing:
                return existing
            count = conn.execute("SELECT COUNT(*) FROM improvement_proposals").fetchone()[0]
            if count >= self.settings()["max_records"]:
                raise ValueError("Proposal storage limit reached; review and remove retained records")
            self.store.put("proposals", proposal, conn=conn)
            self.store.audit("proposal_staged", proposal_id=proposal["id"],
                             details={"kind": kind, "digest": proposal["digest"], "evidence_ids": evidence_ids}, conn=conn)
        return proposal

    def propose_memory(self, content, project_id=None, kind="semantic", source_reference="user",
                       evidence_ids=None, supersedes_id=None):
        return self._new_proposal("memory", "Remember: " + content[:80], {"content": content, "kind": kind,
            "supersedes_id": supersedes_id}, project_id=project_id, evidence_ids=evidence_ids,
            source_reference=source_reference)

    def propose_skill(self, name, description, instructions, project_id=None, evidence_ids=None):
        evidence = list(evidence_ids or [])
        if not evidence:
            # Explicit host-authored procedure is itself auditable user feedback,
            # separate from model-generated or execution success claims.
            evidence = [self.feedback("User supplied a reusable workflow", session_id="manual:" + identifier("workflow"),
                                      project_id=project_id)["id"]]
        return self._new_proposal("skill", description, {"name": name, "description": description,
            "instructions": instructions}, project_id=project_id, evidence_ids=evidence, source_reference="user:workflow")

    def propose_tool(self, payload, project_id=None, evidence_ids=None):
        return self._new_proposal("tool", payload.get("problem", "Tool capability proposal"), payload,
                                  project_id=project_id, evidence_ids=evidence_ids)

    def propose_harness(self, payload, project_id=None, evidence_ids=None):
        return self._new_proposal("harness", payload.get("hypothesis", "Harness experiment"), payload,
                                  project_id=project_id, evidence_ids=evidence_ids)

    def feedback(self, content, *, session_id, project_id=None, correction=False):
        self._enabled(project_id)
        from .privacy import validate_knowledge
        content = validate_knowledge(content)
        record = {"id": identifier("exp"), "idempotency_key": digest([session_id, project_id, content, correction]),
            "session_id": session_id, "project_id": project_id, "task_id": None, "event_type": "user_feedback",
            "source_reference": "user:explicit", "provider": "", "model": "", "harness": "host",
            "outcome": "user_confirmed", "verified": False, "facts": {"user_confirmed": True,
            "correction": correction, "feedback": content}, "created_at": now(), "schema_version": 1}
        self.store.put("experiences", record)
        return self.store.by_key("experiences", record["idempotency_key"])

    def _proposal(self, key, *, conn=None):
        result = self.store.get("proposals", key, conn=conn)
        if not result:
            raise ValueError("Proposal not found")
        if result["digest"] != self._proposal_digest(result):
            raise ValueError("Proposal integrity check failed")
        return result

    def show(self, key):
        proposal = self._proposal(key)
        return {**proposal, "evidence": self._evidence(proposal["evidence_ids"], proposal["project_id"]),
                "audit": [row for row in self.history(limit=1000) if row.get("proposal_id") == key]}

    def diff(self, key):
        return self._proposal(key)["payload"].get("diff", "No file diff; inspect the proposed structured record")

    def _validate(self, proposal):
        evidence = self._evidence(proposal["evidence_ids"], proposal["project_id"])
        if proposal["kind"] == "memory":
            result = self.memory.validate(proposal["payload"], evidence)
        else:
            if proposal["kind"] == "skill":
                self._skill_manager()
            result = self._candidates().validate(proposal["kind"], proposal["payload"])
        if proposal["kind"] == "harness" and result["passed"]:
            reasons = list(result.get("blocked_reasons", []))
            if proposal["payload"].get("memory_fingerprint") != self._memory_fingerprint():
                reasons.append("Approved memory snapshot changed after the paired experiment")
            if proposal["payload"].get("baseline_changes") != {"memory_token_budget": self.settings()["memory_token_budget"]}:
                reasons.append("Active learning settings changed after the paired experiment")
            result.update(passed=not reasons, blocked_reasons=reasons)
        measured = result.get("measured") or {}
        return {**result, "proposal_digest": proposal["digest"], "evaluated_at": now(),
                "evaluator": "host-owned-v1", "sample_size": measured.get("cases", len(evidence)), "evidence_count": len(evidence),
                "tokens": None, "cost_usd": None, "safety_gate": bool(result["passed"])}

    def evaluate(self, key):
        proposal = self._proposal(key)
        self._enabled(proposal["project_id"])
        if proposal["status"] not in {"draft", "blocked", "pending_approval"}:
            raise ValueError("Only pending proposals can be evaluated")
        evaluation = self._validate(proposal)
        with self.store.transaction() as conn:
            current = self._proposal(key, conn=conn)
            if current["revision"] != proposal["revision"] or current["status"] != proposal["status"]:
                raise ValueError("Proposal changed during evaluation")
            current.update(evaluation=evaluation, status="pending_approval" if evaluation["passed"] else "blocked",
                           revision=current["revision"] + 1, updated_at=now())
            self.store.put("proposals", current, replace=True, conn=conn)
            self.store.audit("proposal_evaluated", proposal_id=key,
                             details={"passed": evaluation["passed"], "revision": current["revision"]}, conn=conn)
        return current

    @staticmethod
    def _decision(proposal, expected_revision, actor, allowed):
        if actor != "user":
            raise PermissionError("Only the trusted user interface may decide improvement approvals")
        if type(expected_revision) is not int or proposal["revision"] != expected_revision:
            raise ValueError("Stale proposal revision; inspect the current proposal again")
        if proposal["status"] not in allowed:
            raise ValueError("Proposal state does not permit this decision")

    def approve(self, key, expected_revision, actor="user"):
        proposal = self._proposal(key)
        self._enabled(proposal["project_id"])
        self._decision(proposal, expected_revision, actor, {"pending_approval"})
        if proposal["kind"] == "code" and self.mode != "experiment":
            raise PermissionError("Source integration requires explicitly enabled experiment mode")
        if proposal["kind"] == "tool" and proposal["project_id"] is not None:
            raise PermissionError("Project tools require a project-bound runtime registry; global activation is blocked")
        if proposal["kind"] == "tool" and self.plugin_manager is None:
            from firik_agent.plugins.manager import PluginManager
            from firik_agent.core.tools import ToolRegistry
            # No plugin discovery/autoload. CLI records approved availability;
            # an actual Agent restores this adapter from the applied record.
            self.plugin_manager = PluginManager(ToolRegistry(), plugins_dir=self.database.db_path.parent / "approved-tool-adapters")
            self._candidates().plugin_manager = self.plugin_manager
        evaluation = self._validate(proposal)
        if not evaluation["passed"] or proposal["evaluation"].get("proposal_digest") != proposal["digest"]:
            raise ValueError("Current independent validation blocks promotion: " + "; ".join(evaluation["blocked_reasons"]))
        with self.store.transaction() as conn:
            current = self._proposal(key, conn=conn)
            self._decision(current, expected_revision, actor, {"pending_approval"})
            if current["kind"] == "memory":
                current["applied"] = self.memory.apply(current, conn=conn)
                current.update(status="applied", revision=current["revision"] + 1, updated_at=now())
                self.store.put("proposals", current, replace=True, conn=conn)
                self.store.audit("proposal_applied", proposal_id=key, actor=actor,
                    details={"digest": current["digest"], "revision": current["revision"], "applied": current["applied"]}, conn=conn)
                return current
            # Intent committed before filesystem side effects. A crash leaves
            # 'unknown', never a pending approval that can be blindly replayed.
            current.update(status="applying", owner_pid=os.getpid(), revision=current["revision"] + 1, updated_at=now())
            self.store.put("proposals", current, replace=True, conn=conn)
            self.store.audit("apply_intent", proposal_id=key, actor=actor,
                             details={"digest": current["digest"], "revision": current["revision"]}, conn=conn)
        try:
            applied = self._candidates().apply(current["kind"], current["payload"])
            if current["kind"] == "harness":
                changes = applied["changes"]
                if set(changes) - {"memory_token_budget"}:
                    raise ValueError("This optimization has no active settings owner")
                applied["previous_settings"] = {k: self.settings()[k] for k in changes}
                self.configure(changes)
        except BaseException:
            with self.store.transaction() as conn:
                current.update(status="unknown", error="Application interrupted or failed; inspect artifacts before reconciliation", updated_at=now())
                self.store.put("proposals", current, replace=True, conn=conn)
                self.store.audit("apply_unknown", proposal_id=key, conn=conn)
            raise
        with self.store.transaction() as conn:
            if current["kind"] == "skill":
                previous_active = []
                for previous in self.store.list("proposals", status="applied", project_id=current["project_id"], limit=10000, conn=conn):
                    if previous["id"] != key and previous["kind"] == "skill" and previous["applied"].get("target") == applied.get("target"):
                        previous_active.append(previous["id"])
                        previous.update(status="superseded", revision=previous["revision"] + 1, updated_at=now())
                        self.store.put("proposals", previous, replace=True, conn=conn)
                applied["previous_active_ids"] = previous_active
            current.update(status="applied", applied=applied, revision=current["revision"] + 1, updated_at=now())
            self.store.put("proposals", current, replace=True, conn=conn)
            self.store.audit("proposal_applied", proposal_id=key, actor=actor,
                             details={"digest": current["digest"], "revision": current["revision"]}, conn=conn)
        return current

    def reject(self, key, expected_revision, actor="user"):
        with self.store.transaction() as conn:
            proposal = self._proposal(key, conn=conn)
            self._decision(proposal, expected_revision, actor, {"draft", "blocked", "pending_approval", "deferred"})
            proposal.update(status="rejected", revision=proposal["revision"] + 1, updated_at=now())
            self.store.put("proposals", proposal, replace=True, conn=conn)
            self.store.audit("proposal_rejected", proposal_id=key, actor=actor, conn=conn)
        return proposal

    def rollback(self, key, expected_revision, actor="user"):
        with self.store.transaction() as conn:
            proposal = self._proposal(key, conn=conn)
            self._decision(proposal, expected_revision, actor, {"applied"})
            if proposal["kind"] == "memory":
                restored = self.memory.rollback(proposal, conn=conn)
                proposal.update(status="rolled_back", revision=proposal["revision"] + 1, updated_at=now(), rollback=restored)
                self.store.put("proposals", proposal, replace=True, conn=conn)
                self.store.audit("proposal_rolled_back", proposal_id=key, actor=actor, details=restored, conn=conn)
                return proposal
            proposal.update(status="rolling_back", owner_pid=os.getpid(), revision=proposal["revision"] + 1, updated_at=now())
            self.store.put("proposals", proposal, replace=True, conn=conn)
            self.store.audit("rollback_intent", proposal_id=key, actor=actor, conn=conn)
        try:
            if proposal["kind"] == "skill":
                self._skill_manager()
            restored = self._candidates().rollback(proposal["kind"], proposal["payload"], proposal["applied"])
            if proposal["kind"] == "harness":
                if any(self.settings().get(k) != v for k, v in proposal["applied"]["changes"].items()):
                    raise ValueError("Settings changed after promotion; stage a new proposal")
                self.configure(proposal["applied"]["previous_settings"])
        except BaseException:
            proposal.update(status="unknown", error="Rollback incomplete; inspect artifacts before reconciliation")
            self.store.put("proposals", proposal, replace=True)
            self.store.audit("rollback_unknown", proposal_id=key)
            raise
        proposal.update(status="rolled_back", revision=proposal["revision"] + 1, updated_at=now(), rollback=restored)
        with self.store.transaction() as conn:
            self.store.put("proposals", proposal, replace=True, conn=conn)
            if proposal["kind"] == "skill":
                for prior_id in proposal["applied"].get("previous_active_ids", []):
                    prior = self._proposal(prior_id, conn=conn)
                    if prior["status"] == "superseded":
                        prior.update(status="applied", revision=prior["revision"] + 1, updated_at=now())
                        self.store.put("proposals", prior, replace=True, conn=conn)
            self.store.audit("proposal_rolled_back", proposal_id=key, actor=actor, details=restored, conn=conn)
        return proposal

    def disable_skill(self, key, expected_revision, actor="user"):
        proposal = self._proposal(key)
        if proposal["kind"] != "skill":
            raise ValueError("Only learned skills can be disabled here")
        with self.store.transaction() as conn:
            proposal = self._proposal(key, conn=conn)
            self._decision(proposal, expected_revision, actor, {"applied"})
            proposal.update(status="disabling", owner_pid=os.getpid(), revision=proposal["revision"] + 1, updated_at=now())
            self.store.put("proposals", proposal, replace=True, conn=conn)
            self.store.audit("disable_intent", proposal_id=key, actor=actor, conn=conn)
        try:
            self._skill_manager()
            result = self._candidates().disable_skill(proposal["payload"], proposal["applied"])
        except BaseException:
            proposal.update(status="unknown", error="Skill disabling incomplete; inspect files before reconciliation")
            self.store.put("proposals", proposal, replace=True)
            raise
        proposal.update(status="disabled", revision=proposal["revision"] + 1, updated_at=now(), disabled=result)
        self.store.put("proposals", proposal, replace=True)
        self.store.audit("skill_disabled", proposal_id=key, actor=actor)
        return proposal

    def revise(self, key, payload, expected_revision):
        old = self._proposal(key)
        self._enabled(old["project_id"])
        self._decision(old, expected_revision, "user", {"draft", "blocked", "pending_approval", "deferred"})
        if old["kind"] == "code":
            # A changed patch must be recaptured in its worktree by stage_code;
            # receipt attachment may not change the already authorized scope.
            if any(payload.get(k, old["payload"].get(k)) != old["payload"].get(k) for k in
                   ("repository", "workspace_id", "base_revision", "digest", "paths", "allowed_paths")):
                raise ValueError("Code source identity cannot be revised; stage a new candidate")
        combined = {**old["payload"], **payload}
        if old["kind"] == "skill":
            if "project_id" in payload and payload["project_id"] != old["project_id"]:
                raise ValueError("A skill revision cannot change project identity")
            if payload.get("scope", old["payload"]["scope"]) != old["payload"]["scope"]:
                raise ValueError("A skill revision cannot change its approved scope")
            combined["project_id"] = old["project_id"]
            combined["scope"] = old["payload"]["scope"]
            if old["project_id"]:
                combined["project_root"] = self.database.get_project(old["project_id"]).local_path
        if old["kind"] == "memory":
            prepared = self.memory.prepare(combined, project_id=old["project_id"], source_type=old["source_type"])
        else:
            prepared = self._candidates().prepare(old["kind"], combined, project_id=old["project_id"])
        with self.store.transaction() as conn:
            current = self._proposal(key, conn=conn)
            self._decision(current, expected_revision, "user", {"draft", "blocked", "pending_approval", "deferred"})
            self.store.audit("proposal_revision", proposal_id=key, actor="user",
                             details={"revision": old["revision"], "payload": old["payload"], "digest": old["digest"]}, conn=conn)
            current.update(payload=prepared, status="draft", evaluation={}, revision=current["revision"] + 1, updated_at=now())
            current["digest"] = self._proposal_digest(current)
            self.store.put("proposals", current, replace=True, conn=conn)
        return current

    def stage_code(self, repository, allowed_paths, patch, project_id=None, evidence_ids=None):
        self._enabled(project_id)
        if self.mode != "experiment":
            raise PermissionError("Isolated code candidate creation requires explicit experiment mode")
        if project_id and Path(self.database.get_project(project_id).local_path).resolve() != Path(repository).resolve():
            raise ValueError("Candidate repository does not match the selected project")
        self._evidence(evidence_ids or [], project_id)
        lease = self._claim_experiment()
        try:
            self._candidate_capacity(repository)
            payload = self._candidates().stage_code(repository, allowed_paths, patch)
            return self._new_proposal("code", "Isolated source improvement", payload,
                                      project_id=project_id, evidence_ids=evidence_ids)
        finally:
            self._release_experiment(lease)

    def _memory_fingerprint(self):
        records = self.store.list("memories", status="active", limit=100000)
        fields = ("memory_id", "content", "project_id", "revision", "status", "user_approved", "pinned", "confidence", "expires_at")
        return digest([{**{k: row.get(k) for k in fields}, "currently_expired": bool(row.get("expires_at") and row["expires_at"] <= now())}
                       for row in sorted(records, key=lambda item: item["memory_id"])])

    async def generate_code(self, agent, repository, allowed_paths, task, project_id=None,
                            evidence_ids=None, allow_model_digest=False):
        self._enabled(project_id)
        if self.mode != "experiment" or allow_model_digest is not True:
            raise PermissionError("Code generation requires experiment mode and explicit consent to send the bounded source digest to the selected model")
        if project_id and Path(self.database.get_project(project_id).local_path).resolve() != Path(repository).resolve():
            raise ValueError("Candidate repository does not match the selected project")
        route = self.settings()["model_routes"].get("coding")
        active = agent.providers.get_active()
        if route and (route["provider"] != active.name or route["model"] != active.current_model):
            raise PermissionError("Configured auxiliary coding route differs from the selected agent; no automatic provider/model switch")
        self._evidence(evidence_ids or [], project_id)
        token = threading.Event()
        job = {"id": identifier("generation"), "session_id": "generation", "project_id": project_id,
               "created_at": now(), "status": "running", "owner_pid": os.getpid(), "kind": "code_generation",
               "provider": safe_label(active.name), "model": safe_label(active.current_model),
               "disclosure": "Explicit user consent to send only authorized source files and task to the existing selected provider"}
        lease = self._claim_experiment()
        try:
            self._candidate_capacity(repository, reserve=2)
            self.store.put("jobs", job)
            self._cancelled[job["id"]] = token
            generation = asyncio.create_task(self._candidates().generate_code(agent, repository, allowed_paths, task,
                evidence_ids=evidence_ids or (), cancel_event=token))
            try:
                while not generation.done():
                    if token.is_set() or not self.project_enabled(project_id):
                        generation.cancel()
                        await asyncio.gather(generation, return_exceptions=True)
                        raise asyncio.CancelledError
                    await asyncio.wait({generation}, timeout=0.05)
                payload = await generation
            finally:
                if not generation.done():
                    generation.cancel()
                    await asyncio.gather(generation, return_exceptions=True)
            if token.is_set() or not self.project_enabled(project_id):
                raise asyncio.CancelledError
            result = self._new_proposal("code", "Generated isolated source patch", payload,
                project_id=project_id, evidence_ids=evidence_ids, source_reference=job["id"])
            job.update(status="completed", proposal_id=result["id"], generation=payload.get("generation"), updated_at=now())
            return result
        except BaseException as error:
            job.update(status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                       error="Generation stopped; no running application source was changed", updated_at=now())
            raise
        finally:
            if self.store.get("jobs", job["id"]):
                self.store.put("jobs", job, replace=True)
            self._cancelled.pop(job["id"], None)
            self._release_experiment(lease)

    def experiment(self, key, fixtures=None, gates=None, cases=None):
        proposal = self._proposal(key)
        self._enabled(proposal["project_id"])
        if self.mode != "experiment" or proposal["status"] not in {"draft", "blocked"}:
            raise PermissionError("Explicit experiment mode and an unapplied candidate are required")
        token = threading.Event()
        job = {"id": identifier("experiment"), "session_id": "experiment:" + key, "project_id": proposal["project_id"],
               "status": "running", "owner_pid": os.getpid(), "created_at": now(), "proposal_id": key}
        lease = self._claim_experiment()
        try:
            engine = self._candidates()
            fingerprint = self._memory_fingerprint()
            job["idempotency_key"] = digest(["experiment", proposal["digest"], fixtures, gates, cases, fingerprint])
            if proposal["kind"] == "code":
                self._candidate_capacity(proposal["payload"]["repository"])
            if not self.store.put("jobs", job):
                raise ValueError("This exact experiment was already attempted; inspect its outcome before changing the candidate or cases")
            self._cancelled[job["id"]] = token
            async def operation():
                if proposal["kind"] == "tool":
                    return await engine.experiment_tool(proposal["payload"], fixtures or [])
                if proposal["kind"] == "code":
                    return await engine.experiment_code(proposal["payload"], gates or [])
                if proposal["kind"] == "harness":
                    return await engine.experiment_harness(proposal["payload"], cases or [],
                        retriever=self.memory.retrieve, baseline_budget=self.settings()["memory_token_budget"], memory_fingerprint=fingerprint)
                raise ValueError("This candidate has no executable experiment")
            async def supervised():
                task = asyncio.create_task(operation())
                try:
                    while not task.done():
                        if token.is_set() or not self.project_enabled(proposal["project_id"]):
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                            raise InterruptedError("Experiment cancelled; inspect cleanup evidence before retry")
                        await asyncio.wait({task}, timeout=0.05)
                    return await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            result = asyncio.run(supervised())
            if proposal["kind"] == "harness" and fingerprint != self._memory_fingerprint():
                raise ValueError("Memory changed while the paired experiment ran")
            updated = self.revise(key, {**result.get("payload_updates", {}), "receipt_id": result["receipt_id"]}, proposal["revision"])
            job.update(status="completed", result=result, updated_at=now())
            return updated
        except BaseException as error:
            job.update(status="cancelled" if isinstance(error, (InterruptedError, asyncio.CancelledError)) else "failed",
                       error="Experiment stopped or failed; no automatic replay; inspect source/cleanup evidence", updated_at=now())
            raise
        finally:
            if self.store.get("jobs", job["id"]):
                self.store.put("jobs", job, replace=True)
            self._cancelled.pop(job["id"], None)
            self._release_experiment(lease)

    def monitor(self, key, cases):
        proposal = self._proposal(key)
        self._enabled(proposal["project_id"])
        if proposal["status"] != "applied" or proposal["kind"] != "harness":
            raise ValueError("Paired regression monitoring is available for applied memory-budget optimizations")
        if {k: self.settings().get(k) for k in proposal["applied"]["changes"]} != proposal["applied"]["changes"]:
            raise ValueError("Active settings changed; inspect the current configuration before monitoring this revision")
        result = asyncio.run(self._candidates().experiment_harness(proposal["payload"], cases,
            retriever=self.memory.retrieve, baseline_budget=proposal["applied"]["previous_settings"]["memory_token_budget"],
            memory_fingerprint=self._memory_fingerprint()))
        report = result["report"]
        # A failed paired correctness gate is evidence; lack of speed/token gain
        # alone is inconclusive and is never labeled a reliability regression.
        measured = report.get("measured", {})
        rows = report.get("rows", [])
        baseline_rows = {row["case"]: row for row in rows if row["configuration"] == "baseline"}
        regression = any(baseline_rows.get(row["case"], {}).get("passed") is True and row.get("passed") is False
                         for row in rows if row["configuration"] == "candidate")
        monitoring = {"proposal_id": key, "report": report, "regression_detected": regression,
                      "rollback_available": regression, "automatic_rollback": False, "created_at": now()}
        self.store.audit("regression_detected" if regression else "monitoring_observation", proposal_id=key, details=monitoring)
        return monitoring

    def reflect(self, session_id, project_id=None):
        self._enabled(project_id)
        imported = self.ingest_session(session_id, project_id)
        return self._review(imported["session_id"], imported["project_id"], manual=True)

    def _review(self, session_id, project_id=None, manual=False, job=None):
        self._enabled(project_id)
        settings = self.settings()
        experiences = self.store.list("experiences", session_id=session_id, project_id=project_id,
                                      limit=settings["max_events_per_session"])
        # project_id=None is not an all-project review capability.
        experiences = [e for e in experiences if e.get("project_id") == project_id]
        fingerprint = digest(sorted(e["id"] for e in experiences))
        key = digest([session_id, project_id, fingerprint])
        existing = self.store.by_key("jobs", key)
        if existing and existing["status"] in {"completed", "failed", "interrupted", "cancelled"}:
            return {**existing, "deduplicated": True}
        if job is None:
            job = {"id": identifier("review"), "session_id": session_id, "project_id": project_id,
                "idempotency_key": key, "status": "running", "owner_pid": os.getpid(), "created_at": now(),
                "fingerprint": fingerprint, "manual": manual}
            if not self.store.put("jobs", job):
                return {**self.store.by_key("jobs", key), "deduplicated": True}
        token = self._cancelled.setdefault(job["id"], threading.Event())
        started = time.monotonic()
        try:
            if token.is_set() or not self.project_enabled(project_id):
                raise InterruptedError("Review cancelled")
            related = self.store.list("experiences", project_id=project_id, limit=2000) if project_id else experiences
            job.update(status="running", updated_at=now())
            self.store.put("jobs", job, replace=True)
            result = self.reflection.reflect(experiences, related=related, maximum=settings["max_proposals_per_review"])
            if token.is_set() or not self.project_enabled(project_id) or time.monotonic() - started > settings["max_review_seconds"]:
                raise InterruptedError("Review cancelled or budget exhausted")
            proposed = []
            for opportunity in result.pop("proposals"):
                if token.is_set() or not self.project_enabled(project_id) or time.monotonic() - started > settings["max_review_seconds"]:
                    raise InterruptedError("Review cancelled or budget exhausted")
                if opportunity["kind"] in {"memory", "skill"} and not project_id:
                    continue
                candidate = self._new_proposal(opportunity["kind"], opportunity["title"], opportunity["payload"],
                    project_id=project_id, evidence_ids=opportunity["evidence_ids"],
                    source_type=opportunity.get("source_type", "reflection"), source_reference=job["id"],
                    idempotency_key=digest([project_id, opportunity["kind"], opportunity["payload"], opportunity["evidence_ids"]]))
                proposed.append(candidate["id"])
            job.update(status="completed", result={**result, "proposal_ids": proposed}, updated_at=now(),
                       duration_ms=round((time.monotonic() - started) * 1000, 3), tokens=0, cost_usd=0.0,
                       method="deterministic_offline", tools_granted=[])
        except InterruptedError:
            job.update(status="cancelled", error="Cancelled or resource budget exhausted", updated_at=now())
        except Exception:
            job.update(status="failed", error="Reflection failed; source evidence retained; no automatic retry", updated_at=now())
        finally:
            self.store.put("jobs", job, replace=True)
            self.store.audit("review_" + job["status"], details={"job_id": job["id"], "session_id": session_id})
            self._cancelled.pop(job["id"], None)
        return job

    def submit_reflection(self, session_id, project_id=None):
        if not self.automatic_reflection or not self.project_enabled(project_id):
            return {"status": "disabled"}
        with self._lock:
            settings = self.settings()
            evidence = self.store.list("experiences", session_id=session_id, project_id=project_id,
                                       limit=settings["max_events_per_session"])
            if not any(e["outcome"] == "failed" or e["verified"] or e["event_type"] == "user_feedback" for e in evidence):
                return {"status": "insufficient_evidence"}
            recent = self.store.list("jobs", session_id=session_id, project_id=project_id, limit=1)
            if recent:
                import datetime as dt
                elapsed = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(recent[0]["created_at"])).total_seconds()
                if elapsed < settings["review_cooldown_seconds"]:
                    return {"status": "cooldown", "job_id": recent[0]["id"]}
            key = digest([session_id, project_id, digest(sorted(e["id"] for e in evidence))])
            with self.store.transaction() as conn:
                previous = self.store.by_key("jobs", key, conn=conn)
                if previous:
                    return {**previous, "deduplicated": True}
                pending = conn.execute("SELECT COUNT(*) FROM improvement_jobs WHERE status IN ('queued','running')").fetchone()[0]
                if pending >= settings["max_pending_jobs"]:
                    return {"status": "queue_full"}
                job = {"id": identifier("review"), "session_id": session_id, "project_id": project_id,
                       "idempotency_key": key, "status": "queued", "owner_pid": os.getpid(), "created_at": now(), "manual": False}
                self.store.put("jobs", job, conn=conn)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="firik-learning")
            self._cancelled[job["id"]] = threading.Event()
            future = self._executor.submit(self._review, session_id, project_id, False, job)
            self._futures[job["id"]] = future
            future.add_done_callback(lambda f, key=job["id"]: self._futures.pop(key, None))
            return job

    def cancel(self, job_id=None):
        cancelled = []
        with self._lock:
            for key, token in list(self._cancelled.items()):
                if job_id is None or job_id == key:
                    token.set()
                    cancelled.append(key)
                    future = self._futures.get(key)
                    if future and future.cancel():
                        job = self.store.get("jobs", key)
                        job.update(status="cancelled", updated_at=now())
                        self.store.put("jobs", job, replace=True)
                        self._cancelled.pop(key, None)
        return {"cancelled": cancelled}

    def close(self):
        self.cancel()
        self._closed = True
        if self._periodic_timer is not None:
            self._periodic_timer.cancel()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def retrieve(self, query, project_id=None, token_budget=None):
        if not self.project_enabled(project_id):
            return []
        maximum = self.settings()["memory_token_budget"]
        budget = maximum if token_budget is None else min(maximum, max(0, int(token_budget)))
        return self.memory.retrieve(query, project_id=project_id, token_budget=budget)

    def memories(self, project_id=None, limit=50, offset=0):
        return self.store.list("memories", project_id=project_id, limit=limit, offset=offset)

    def pin_memory(self, key, pinned=True):
        if type(pinned) is not bool:
            raise ValueError("Pinned must be boolean")
        with self.store.transaction() as conn:
            memory = self.store.get("memories", key, conn=conn)
            if not memory or memory["status"] != "active":
                raise ValueError("Only active approved memories can be pinned")
            memory.update(pinned=pinned, revision=memory["revision"] + 1, updated_at=now())
            self.store.put("memories", memory, replace=True, conn=conn)
            self.store.audit("memory_pinned" if pinned else "memory_unpinned", actor="user", details={"memory_id": key}, conn=conn)
        return memory

    def prune(self):
        settings = self.settings()
        return self.store.prune(settings["retention_days"], settings["max_records"])

    def remove_memory(self, key):
        with self.store.transaction() as conn:
            memory = self.store.get("memories", key, conn=conn)
            if not memory:
                raise ValueError("Memory not found")
            # Forget the complete correction chain, including learned copies in
            # proposal titles and historical audit payloads. The source coding
            # journal is independently owned and is not rewritten here.
            records = self.store.list("memories", limit=100000, conn=conn)
            chain = {key}
            changed = True
            while changed:
                changed = False
                for row in records:
                    if row["memory_id"] in chain or row.get("supersedes_id") in chain:
                        for related in (row["memory_id"], row.get("supersedes_id")):
                            if related and related not in chain:
                                chain.add(related)
                                changed = True
            for row in records:
                if row["memory_id"] in chain:
                    row.update(content="", status="removed", user_approved=False)
                    self.store.put("memories", row, replace=True, conn=conn)
            proposals = set()
            for row in self.store.list("proposals", limit=100000, conn=conn):
                if row.get("applied", {}).get("memory_id") in chain or row.get("payload", {}).get("supersedes_id") in chain:
                    proposals.add(row["id"])
                    row.update(payload={"content": "", "removed": True}, title="Removed knowledge", status="removed",
                               revision=row["revision"] + 1, updated_at=now())
                    row["digest"] = self._proposal_digest(row)
                    self.store.put("proposals", row, replace=True, conn=conn)
            for row in self.store.list("audit", limit=100000, conn=conn):
                if row.get("proposal_id") in proposals:
                    row["details"] = {k: v for k, v in row.get("details", {}).items()
                                      if k in {"revision", "digest", "kind", "passed"}}
                    row["details"]["knowledge_removed"] = True
                    self.store.put("audit", row, replace=True, conn=conn)
            self.store.audit("memory_removed", actor="user", details={"memory_id": key}, conn=conn)
        return {"memory_id": key, "status": "removed"}

    def proposals(self, status=None, limit=50, offset=0):
        return self.store.list("proposals", status=status, limit=limit, offset=offset)

    def history(self, limit=50, offset=0):
        return self.store.list("audit", limit=limit, offset=offset)

    def metrics(self):
        run = RunMetrics()
        experiences = self.store.list("experiences", limit=10000)
        for row in reversed(experiences):
            run.observe({"type": row["event_type"], "duration_ms": row["facts"].get("duration_ms"),
                "metadata": {**row["facts"], "agent_id": row["session_id"] + ":" + row["facts"].get("run_id", "chat")}})
        recalls = self.store.list("retrievals", limit=10000)
        generations = [job for job in self.store.list("jobs", limit=10000) if job.get("kind") == "code_generation"]
        usages = [job.get("generation", {}).get("metrics", {}).get("usage", {}) for job in generations]
        requests = [job.get("generation", {}).get("requests") for job in generations]
        complete = all(job["status"] == "completed" for job in generations)
        def total(key):
            values = [usage.get(key) for usage in usages]
            if not values:
                return 0
            return sum(values) if complete and all(value is not None for value in values) else None
        request_total = sum(requests) if complete and all(type(value) is int for value in requests) else None
        snapshot = run.snapshot()
        settings = self.settings()
        sessions = {}
        for row in experiences:
            sessions[row["session_id"]] = sessions.get(row["session_id"], 0) + 1
        if len(experiences) >= min(10000, settings["max_records"]) or any(count >= settings["max_events_per_session"] for count in sessions.values()):
            snapshot.update(accounting_limited=True, usage_complete=False, cost_complete=False)
            snapshot["usage"] = {key: None for key in snapshot["usage"]}
        return {"execution": snapshot, "retrievals": len(recalls),
                "retrieval_token_estimate": sum(r["token_estimate"] for r in recalls), "estimate_only": True,
                "learning_model_requests": request_total, "learning_tokens": total("total_tokens"), "learning_cost_usd": total("cost"),
                "reported_learning_request_lower_bound": sum(value for value in requests if type(value) is int),
                "scope": "bounded retained observations", "performance_improvement": None}

    def status(self):
        return {"schema_version": 1, "settings": self.settings(), "mode": self.mode,
                "automatic_reflection": self.automatic_reflection, "counts": self.store.counts(),
                "jobs": self.store.list("jobs", limit=20), "metrics": self.metrics(),
                "recent_errors": [j for j in self.store.list("jobs", limit=100) if j["status"] in {"failed", "interrupted"}][:10],
                "fts5_available": self.store.fts_available, "worker_tools": [],
                "external_model_digest_disclosure": "No auxiliary model receives data; reflection is deterministic and offline",
                "scheduler": "in-process end-of-task and opt-in periodic review; stops when the application exits"}
