"""Bounded, replayable projections of real coding-run events for the desktop cockpit.

The event journal is the durable source; this projection never executes an action.
Provider usage frames replace the same request's totals instead of double counting.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict

from firik_agent.projects.coding_sessions import redact_sensitive
from firik_agent.core.metrics import RunMetrics


class AgentStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    NEEDS_APPROVAL = "needs_approval"
    REVIEWING = "reviewing"
    TESTING = "testing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActivityEvent(TypedDict, total=False):
    id: str
    event_type: str
    agent_id: str
    timestamp: float
    status: str
    title: str
    tool: str
    file: str
    command: str
    result: str
    error: str
    duration_ms: float | None
    arguments: dict[str, Any]
    server: str
    metadata: dict[str, Any]


class AgentRecord(TypedDict, total=False):
    id: str
    parent_id: str | None
    name: str
    role: str
    status: str
    task: str
    model: str
    provider: str
    usage: dict[str, Any]
    cost: float | None
    elapsed_sec: float
    files_touched: list[str]
    last_activity: float
    context: dict[str, Any]


STATE_ALIASES = {
    "understanding": "planning", "implementing": "running", "applying": "running",
    "verifying": "testing", "correcting": "running", "repairing": "running", "paused": "waiting",
    "completed_verified": "completed", "completed_unverified": "completed",
    "waiting_approval": "needs_approval", "awaiting_approval": "needs_approval",
    "interrupted": "cancelled", "stopping": "waiting",
}
TOOL_TITLES = {
    "read_file": "Dosya okunuyor", "list_files": "Dosyalar listeleniyor",
    "search_project": "Proje aranıyor", "search_symbols": "Semboller aranıyor",
    "edit_file": "Dosya düzenleniyor", "write_file": "Dosya yazılıyor",
    "create_file": "Dosya oluşturuluyor", "delete_file": "Dosya siliniyor",
    "run_command": "Komut çalışıyor", "run_shell": "Komut çalışıyor",
    "run_tests": "Doğrulama çalışıyor", "run_quality_gate": "Kalite kapısı çalışıyor",
    "spawn_subagent": "Alt ajan oluşturuluyor", "wait_subagent": "Alt ajan bekleniyor",
    "send_message": "Ajan mesajı", "update_plan": "Plan güncelleniyor",
}
TERMINAL_TOOLS = {"run_shell", "run_command", "run_tests", "run_quality_gate"}


def benchmark_report_summary(report: dict[str, Any]) -> dict[str, Any]:
    """Read-only desktop summary using the same validated native run evidence."""
    from copy import deepcopy
    from firik_agent.benchmarks.comparison import validate_report
    from firik_agent.benchmarks.metrics import enrich_report
    from firik_agent.projects.coding_sessions import redact_sensitive
    validate_report(report)
    safe = redact_sensitive(deepcopy(report))
    summary = enrich_report(safe)["summary"]
    return {"label": safe.get("label", "candidate"), "profile": safe["metadata"].get("profile"),
            "trials": summary["trials"], "fixtures_passed": summary["fixtures_passed"],
            "verified_tasks": summary["completed_verified_tasks"], "pass_at_1": summary["pass_at_1"],
            "p50_ms": summary["wall_time_ms"]["p50"], "p95_ms": summary["wall_time_ms"]["p95"],
            "false_completions": summary["false_completions"], "safety_violations": summary["safety_violations"],
            "cost": summary["cost"], "cost_sources": summary["cost_sources"], "method": safe["method"]}


def _kind(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _json_result(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    try:
        result = json.loads(content or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


@dataclass
class CockpitState:
    timeline: list[ActivityEvent] = field(default_factory=list)
    terminal: list[dict[str, Any]] = field(default_factory=list)
    tests: list[dict[str, Any]] = field(default_factory=list)
    agents: dict[str, AgentRecord] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    plan: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    revision: int = 0
    timeline_limit: int = 300
    terminal_limit: int = 100
    _sequence: int = 0
    _requests: dict[tuple[str, str | int], dict[str, Any]] = field(default_factory=dict, repr=False)
    _iterations: dict[str, int] = field(default_factory=dict, repr=False)
    _started: float = 0.0
    _run_metrics: RunMetrics = field(default_factory=RunMetrics, repr=False)
    _persisted_metrics: dict[str, Any] | None = field(default=None, repr=False)
    _metric_protocol: bool = field(default=False, repr=False)

    def start(self, model: str = "", provider: str = "", task: str = "", run_id: str = "") -> None:
        self.timeline.clear()
        self.terminal.clear()
        self.tests.clear()
        self.agents.clear()
        self.approvals.clear()
        self.context.clear()
        self.summary.clear()
        self._requests.clear()
        self._run_metrics = RunMetrics()
        self._persisted_metrics = None
        self._metric_protocol = False
        self._iterations.clear()
        self.plan = []
        self.run_id = run_id
        self._started = time.time()
        self._sequence = 0
        self._agent("orchestrator", model=model, provider=provider, task=task, status="created")
        self.revision += 1

    def _agent(self, agent_id: str, **fields: Any) -> AgentRecord:
        if agent_id not in self.agents:
            # A run can spawn at most sixteen children; historical malformed
            # events do not create an unbounded inspector or usage map.
            if len(self.agents) >= 17:
                agent_id = "orchestrator"
            self.agents.setdefault(agent_id, {
                "id": agent_id, "parent_id": None if agent_id == "orchestrator" else "orchestrator",
                "name": "Koordinatör" if agent_id == "orchestrator" else agent_id,
                "role": "orchestrator" if agent_id == "orchestrator" else "worker",
                "status": "created", "task": "", "model": "", "provider": "",
                "usage": {}, "cost": None, "elapsed_sec": 0.0, "files_touched": [],
                "last_activity": 0.0, "context": {},
            })
        agent = self.agents[agent_id]
        agent.update({k: v for k, v in fields.items() if v is not None})
        agent["status"] = STATE_ALIASES.get(agent["status"], agent["status"])
        return agent

    def _append(self, kind: str, agent_id: str, stamp: float, **fields: Any) -> ActivityEvent:
        self._sequence += 1
        entry: ActivityEvent = {"id": str(fields.pop("id", "") or f"event_{self._sequence}"),
            "event_type": kind, "agent_id": agent_id, "timestamp": stamp,
            "status": "completed", "title": kind, "duration_ms": None,
            "result": "", "error": "", "arguments": {}, "metadata": {}, **fields}
        self.timeline.append(entry)
        del self.timeline[:-self.timeline_limit]
        return entry

    def apply(self, event: Any) -> None:
        """Apply a live AgentEvent or a passive CodingRunEvent from SQLite."""
        if hasattr(event, "data"):
            data = dict(event.data)
            data.setdefault("type", event.event_type)
        elif isinstance(event, dict):
            data = dict(event)
        else:
            data = {name: getattr(event, name, None) for name in
                    ("type", "timestamp", "tool_name", "server_name", "content", "arguments", "duration_ms", "metadata")}
        data = redact_sensitive(data)
        kind = _kind(data.get("type", data.get("event_type")))
        # Subagent envelopes recurse below; observe only the normalized child
        # event so starts, usage frames and terminal metrics count once.
        if kind != "subagent_event":
            self._run_metrics.observe(data)
            self._metric_protocol = self._metric_protocol or bool((data.get("metadata") or {}).get("request_id")) or kind == "model_request_finished"
        metadata = data.get("metadata") or {}
        agent_id = str(metadata.get("agent_id") or "orchestrator")
        stamp = data.get("timestamp") or time.time()
        if not isinstance(stamp, (int, float)):
            stamp = time.time()
        if kind == "subagent_event":
            fields = {key: metadata[key] for key in
                      ("name", "role", "status", "task", "model", "provider", "elapsed_sec") if key in metadata}
            parent = metadata.get("parent_agent_id", "orchestrator")
            fields["parent_id"] = parent if parent in self.agents else "orchestrator"
            agent = self._agent(agent_id, **fields)
            agent_id = agent["id"]
            agent["last_activity"] = stamp
            child_kind = metadata.get("child_event_type")
            if child_kind:
                detail = metadata.get("child_metadata") or {}
                nested = {"type": child_kind, "timestamp": stamp,
                    "tool_name": metadata.get("child_tool_name", metadata.get("tool_name")),
                    "server_name": metadata.get("child_server_name"),
                    "arguments": metadata.get("child_arguments", metadata.get("arguments")),
                    "duration_ms": metadata.get("child_duration_ms", data.get("duration_ms")),
                    "content": data.get("content"),
                    "metadata": {**detail, "agent_id": agent_id,
                                 "iteration": detail.get("iteration", metadata.get("request_iteration", 1))}}
                self.apply(nested)
            else:
                self._append("agent.state", agent_id, stamp, status=agent["status"],
                    title=f"{agent['name']} · {agent['role']}", result=str(data.get("content") or "")[:16000], metadata=metadata)
            self.revision += 1
            return
        agent = self._agent(agent_id)
        agent_id = agent["id"]
        agent["last_activity"] = stamp
        content = str(data.get("content") or "")[:32768]
        tool = data.get("tool_name") or metadata.get("tool_name") or ""
        arguments = data.get("arguments") or metadata.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = str(metadata.get("tool_call_id") or metadata.get("call_id") or "")
        if kind in {"text_delta", "reasoning_delta", "tool_call_arguments"}:
            return
        if kind == "model_request_started":
            self._iterations[agent_id] = int(metadata.get("iteration") or self._iterations.get(agent_id, 0) + 1)
            if isinstance(metadata.get("context_inspector"), dict):
                agent["context"] = metadata["context_inspector"]
                if agent_id == "orchestrator":
                    self.context = metadata["context_inspector"]
            agent["status"] = "running"
        elif kind in {"model_usage", "model_request_finished"}:
            iteration = int(metadata.get("iteration") or self._iterations.get(agent_id, 1))
            values = metadata.get("usage") or {}
            request_key = (agent_id, metadata.get("request_id") or iteration)
            self._requests[request_key] = {**self._requests.get(request_key, {}), **values}
            if len(self._requests) > 512:
                self._requests.pop(next(iter(self._requests)))
            totals: dict[str, Any] = {}
            for (owner, _), usage in self._requests.items():
                if owner != agent_id:
                    continue
                for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "cached_tokens", "total_tokens", "cost", "cost_usd"):
                    value = usage.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        totals[key] = totals.get(key, 0) + value
            agent["usage"] = totals
            agent["cost"] = totals.get("cost_usd", totals.get("cost"))
            agent["metrics"] = {**agent.get("metrics", {}), **metadata.get("metrics", {})}
        elif kind == "tool_call_started":
            path = arguments.get("path", arguments.get("file_path", ""))
            command = arguments.get("command", "")
            entry = self._append(kind, agent_id, stamp, id=call_id, status="running", tool=tool,
                title=TOOL_TITLES.get(tool, "MCP çağrısı" if data.get("server_name") else tool),
                file=path, command=command, arguments=arguments, server=data.get("server_name") or "", metadata=metadata)
            if tool in TERMINAL_TOOLS:
                self.terminal.append({"id": entry["id"], "agent_id": agent_id, "command": command,
                    "cwd": arguments.get("cwd", "."), "status": "running", "exit_code": None,
                    "duration_ms": None, "output": "", "timestamp": stamp})
                del self.terminal[:-self.terminal_limit]
        elif kind in {"tool_call_result", "tool_call_error"}:
            entry = next((item for item in reversed(self.timeline) if item["agent_id"] == agent_id
                          and (item["id"] == call_id if call_id else item.get("tool") == tool and item["status"] == "running")), None)
            result = _json_result(content)
            if not result:
                exit_match = re.match(r"(?:Exit code|Çıkış kodu) (-?\d+):", content)
                if exit_match:
                    result = {"exit_code": int(exit_match[1]), "output": content}
            error = kind == "tool_call_error" or metadata.get("is_error") or result.get("passed") is False
            if content.startswith(("Command timed out", "Command execution error:")):
                error = True
            if result.get("exit_code") is not None and result["exit_code"] != 0:
                error = True
            if entry is None:
                entry = self._append(kind, agent_id, stamp, id=call_id, tool=tool, title=tool)
            entry.update(status="failed" if error else "completed", result=content, error=content if error else "",
                         duration_ms=data.get("duration_ms"), metadata=metadata)
            terminal = next((item for item in reversed(self.terminal) if item["id"] == entry["id"] and item["agent_id"] == agent_id), None)
            if terminal is not None:
                terminal.update(status="failed" if error else "completed", output=result.get("output", content),
                    exit_code=result.get("exit_code"), duration_ms=result.get("duration_ms", data.get("duration_ms")))
        elif kind == "verification":
            result = {**_json_result(content), **metadata}
            if result.get("status") == "stale" and not result.get("evidence_id"):
                for test in self.tests:
                    test.update(fresh=False, stale=True, status="stale", stale_reason=content)
                self._append(kind, agent_id, stamp, status="stale", title="Doğrulama güncelliğini yitirdi",
                             result=content, metadata=result)
                self.revision += 1
                return
            identifier = (f"criterion:{agent_id}:{result.get('criterion', '')}" if result.get("kind") == "criterion"
                          else result.get("evidence_id") or result.get("id") or f"verification_{self._sequence}")
            test = next((item for item in self.tests if item["id"] == identifier), None)
            if result.get("stale") or result.get("fresh") is False or result.get("status") == "stale":
                status = "stale"
            elif result.get("executed") is False:
                status = "blocked" if result.get("status") == "blocked" else "unexecuted"
            elif result.get("passed") is True or result.get("status") == "passed" or result.get("exit_code") == 0:
                status = "passed"
            elif result.get("passed") is False or result.get("exit_code") is not None or result.get("status") == "failed":
                status = "failed"
            else:
                status = "unexecuted"
            values = {**result, "id": identifier, "agent_id": agent_id, "timestamp": stamp,
                      "status": status}
            if test is None:
                self.tests.append(values)
                del self.tests[:-100]
            else:
                test.update(values)
            self._append(kind, agent_id, stamp, status=values["status"], title="Doğrulama sonucu",
                command=result.get("command", ""), result=content or json.dumps(result, ensure_ascii=False), metadata=result)
        elif kind == "file_changed":
            path = metadata.get("path", "")
            if path and path not in agent["files_touched"]:
                agent["files_touched"].append(path)
                del agent["files_touched"][:-2000]
            for test in self.tests:
                if test.get("passed"):
                    test.update(stale=True, status="stale")
            self._append(kind, agent_id, stamp, title="Dosya değişti", file=path,
                         result=metadata.get("diff", content), metadata=metadata)
        elif kind == "plan_updated":
            self.plan = list(metadata.get("plan", metadata.get("steps", [])))[:50]
        elif kind == "approval_required":
            identifier = metadata.get("request_id", "")
            if identifier:
                self.approvals[identifier] = {**metadata, "arguments": arguments, "tool_name": tool}
            agent["status"] = "needs_approval"
            self._append(kind, agent_id, stamp, status="needs_approval", title="İşlem onayı gerekiyor",
                         tool=tool, arguments=arguments, metadata=metadata)
        elif kind == "approval_decided":
            self.approvals.pop(metadata.get("request_id"), None)
            agent["status"] = "running"
            self._append(kind, agent_id, stamp, title="İzin verildi" if metadata.get("allowed", metadata.get("approved")) else "İzin reddedildi", metadata=metadata)
        elif kind == "recovery":
            agent["recovery"] = dict(metadata)
            status = "blocked" if metadata.get("reconciliation_status") == "required" else (
                "waiting" if metadata.get("retry_eligible") else "failed")
            self._append(kind, agent_id, stamp, status=status, title="Kurtarma kararı",
                         result=content, metadata=metadata)
        elif kind == "task_state":
            agent["status"] = STATE_ALIASES.get(metadata.get("state", metadata.get("status", "running")), metadata.get("state", metadata.get("status", "running")))
            if metadata.get("lifecycle_state"):
                agent["lifecycle_state"] = metadata["lifecycle_state"]
            if metadata.get("completion_classification"):
                self.summary["completion_classification"] = metadata["completion_classification"]
            self._append(kind, agent_id, stamp, status=agent["status"], title=content or agent["status"], metadata=metadata)
        elif kind in {"done", "error"}:
            if metadata.get("completion_classification"):
                self.summary["completion_classification"] = metadata["completion_classification"]
            classification = metadata.get("completion_classification")
            agent["status"] = classification if classification in {"blocked", "failed", "cancelled"} else (
                agent["status"] if agent["status"] in {"blocked", "failed", "cancelled"} else "failed" if kind == "error" else "completed")
            self._append(kind, agent_id, stamp, status=agent["status"], title="Çalışma hatası" if kind == "error" else "Ajan yanıtı tamamlandı",
                         result=content, error=content if kind == "error" else "", metadata=metadata)
        else:
            # Extension events (network, messages, checkpoints, context, conflicts)
            # remain observable without introducing a second event bus.
            if kind == "quality_gate" and metadata.get("evidence_id"):
                test = next((item for item in self.tests if item["id"] == metadata["evidence_id"]), None)
                if test is not None:
                    test.update(metadata)
            failed = kind == "mcp_error" or metadata.get("status") in {"failed", "blocked"}
            self._append(kind, agent_id, stamp, title=metadata.get("title", content or kind),
                         status="failed" if kind == "mcp_error" else metadata.get("status", "completed"),
                         server=data.get("server_name") or metadata.get("server", ""),
                         command=metadata.get("command", ""), file=metadata.get("path", ""),
                         result=content, error=content if failed else "", metadata=metadata)
        self.revision += 1

    def finish(self, status: str, changes: list[dict[str, Any]] | None = None) -> None:
        changes = changes or []
        completion_classification = self.summary.get("completion_classification")
        self._agent("orchestrator", status=status)
        for item in self.timeline + self.terminal:
            if item.get("status") == "running":
                item["status"] = "cancelled" if status == "cancelled" else "failed"
        totals: dict[str, Any] = {}
        for agent in self.agents.values():
            for key, value in agent["usage"].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
        self.summary = {"status": status, "files_changed": len(changes),
            "insertions": sum(c.get("added", 0) for c in changes),
            "deletions": sum(c.get("removed", 0) for c in changes), "agents": len(self.agents),
            "usage": totals, "cost": totals.get("cost_usd", totals.get("cost")),
            "duration_sec": max(0, time.time() - self._started) if self._started else 0,
            "tests": self.tests.copy(), "completed_steps": sum(s.get("status") == "completed" for s in self.plan)}
        if completion_classification:
            self.summary["completion_classification"] = completion_classification
        metrics = self.metrics_snapshot()
        self.summary["harness_metrics"] = metrics
        # Old event streams may contain usage without request lifecycle events;
        # keep their existing projection while new streams use complete totals.
        if self._metric_protocol or self._persisted_metrics:
            self.summary["usage"] = metrics["usage"]
            self.summary["cost"] = metrics["usage"].get("cost")
        self.revision += 1

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return complete durable totals when only recent events were replayed."""
        return self._persisted_metrics or self._run_metrics.snapshot()

    @property
    def has_request_metrics(self) -> bool:
        return self._metric_protocol or self._persisted_metrics is not None

    def restore(self, record: Any, events: list[Any] | None = None) -> None:
        """Replay passive history; absent old evidence stays absent."""
        self.start(record.model, record.provider, record.user_request, record.run_id)
        for event in events or []:
            self.apply(event)
        persisted = record.usage.get("harness_metrics")
        if isinstance(persisted, dict) and isinstance(persisted.get("usage"), dict):
            # A bounded journal replay cannot reconstruct the full run. The
            # durable aggregate was captured before history was truncated.
            self._persisted_metrics = persisted
        if not events:
            for result in record.tests_executed:
                self.apply({"type": "verification", "metadata": result})
            for decision in record.decisions:
                if decision.get("kind") == "subagent":
                    self.apply({"type": "subagent_event", "metadata": decision, "content": decision.get("result", "")})
            self._agent("orchestrator", usage=record.usage)
        try:
            self.plan = json.loads(record.plan) if record.plan else []
        except (ValueError, TypeError):
            self.plan = []
        self.finish(record.status)
        if record.result in {"completed_verified", "completed_unverified", "blocked", "failed", "cancelled"}:
            self.summary["completion_classification"] = record.result
        self.summary.update(duration_sec=record.duration_sec, files_changed=len(record.files_changed), usage=record.usage)
        if self._persisted_metrics:
            self.summary["usage"] = self._persisted_metrics.get("usage", {})
            self.summary["cost"] = self.summary["usage"].get("cost")
        self.approvals.clear()
