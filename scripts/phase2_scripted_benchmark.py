#!/usr/bin/env python3
"""Paired native runtime fault probes: disabled policy versus adaptive recovery.

No provider/network/credential factory or shell tool is used. This supplements,
not replaces, the frozen Phase 1 before/after suite.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from firik_agent.benchmarks.runner import _agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.failures import FailureKind, RequestPhase, RuntimeFailure
from firik_agent.core.recovery import AdaptiveRecoveryEngine, RecoveryLimits
from firik_agent.core.session import ChatSession
from firik_agent.core.types import StreamChunk, ToolCall, ToolDefinition, ToolResult
from firik_agent.providers.base import BaseProvider

CASES = ("rate-limit", "overloaded", "connect-not-submitted", "submitted-timeout", "partial",
         "authentication", "policy-refusal", "cancel-backoff", "unknown-external", "confirmed-external-then-overload")
RECOVERABLE = {"rate-limit", "overloaded", "connect-not-submitted", "confirmed-external-then-overload"}


class ProbeProvider(BaseProvider):
    def __init__(self, case):
        super().__init__("phase2-scripted", "offline://phase2", default_model="scripted-v1")
        self.case, self.requests = case, 0

    async def list_models(self):
        return []

    def _fault(self, name):
        if name == "rate-limit" or name == "cancel-backoff":
            return RuntimeFailure(FailureKind.RATE_LIMITED, retry_after=.01)
        if name in {"overloaded", "confirmed-external-then-overload"}:
            return RuntimeFailure(FailureKind.PROVIDER_OVERLOADED)
        if name == "connect-not-submitted":
            return httpx.ConnectError("Scripted connection could not open; no socket exists.")
        if name == "submitted-timeout":
            return httpx.ReadTimeout("Scripted outcome after submission is unknown.")
        if name == "authentication":
            return RuntimeFailure(FailureKind.AUTHENTICATION_FAILED)
        if name == "policy-refusal":
            return RuntimeFailure(FailureKind.POLICY_REFUSAL)
        raise AssertionError(name)

    async def chat_stream(self, **kwargs):
        self.requests += 1
        if self.case in {"unknown-external", "confirmed-external-then-overload"}:
            if self.requests == 1:
                yield StreamChunk(tool_calls=[ToolCall(id="effect-1", function={"name": "remote_action", "arguments": "{}"})])
                return
            if self.case == "unknown-external":
                # If the runtime wrongly continues it receives the same mutation.
                yield StreamChunk(tool_calls=[ToolCall(id="effect-2", function={"name": "remote_action", "arguments": "{}"})])
                return
            if self.requests == 2:
                raise self._fault(self.case)
        elif self.case == "partial":
            yield StreamChunk(delta_content="Visible partial result")
            raise httpx.ReadError("Scripted partial interruption")
        elif self.requests == 1:
            raise self._fault(self.case)
        yield StreamChunk(delta_content="Confirmed result", usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})

    async def chat(self, **kwargs):
        raise AssertionError("No interface fallback is authorized by this probe.")


class NoPlugins:
    async def dispatch_user_message(self, prompt):
        return prompt

    async def dispatch_model_request(self, *args): pass
    async def dispatch_model_response(self, *args): pass
    async def dispatch_tool_call(self, *args): pass
    async def dispatch_tool_result(self, *args): pass
    async def dispatch_error(self, *args): pass


async def trial(case: str, adaptive: bool, repeat: int):
    provider = ProbeProvider(case)
    agent = _agent(provider, Path.cwd(), 8)
    agent._build_full_system_prompt = lambda: "Scripted offline reliability probe."
    agent.plugins = NoPlugins()
    agent.recovery_engine = AdaptiveRecoveryEngine(RecoveryLimits(
        request_retries=2 if adaptive else 0, run_retries=6 if adaptive else 0,
        max_requests=8, max_elapsed=5, base_delay=.001, max_delay=.1), jitter=lambda: .5)
    session, cancel = ChatSession(), asyncio.Event()
    effects = []
    def remote_action():
        effects.append("confirmed external ledger entry")
        if case == "unknown-external":
            return ToolResult(tool_call_id="", name="remote_action", content="Scripted remote result was not recorded.", is_error=True,
                metadata={"failure_kind": "tool_outcome_unknown", "request_phase": "tool_dispatched", "reconciliation_status": "required"})
        return "Confirmed remote operation completed."
    agent.tools.register(ToolDefinition(name="remote_action", description="Scripted external action", source="mcp:offline"), remote_action)
    trace, cancelled = [], False
    started = time.monotonic()
    try:
        async for event in agent.run_stream("Execute the scripted probe.", session=session, cancel_event=cancel):
            trace.append(asdict(event))
            if case == "cancel-backoff" and event.type == AgentEventType.RECOVERY:
                cancel.set()
    except asyncio.CancelledError:
        cancelled = True
    duration = (time.monotonic() - started) * 1000
    done = any(event["type"] == "done" and event.get("metadata", {}).get("completion_classification") not in {"cancelled", "failed", "blocked"} for event in trace)
    request_events = [e for e in trace if e["type"] == "model_request_started"]
    retries = [e for e in request_events if e["metadata"].get("attempt", 0) > 0]
    policy_stopped = not retries and provider.requests == 1 and not done
    if case in RECOVERABLE:
        passed = (done == adaptive) and (case != "confirmed-external-then-overload" or len(effects) == 1)
    elif case == "unknown-external":
        passed = not done and len(effects) == 1 and provider.requests == 1 and not retries
    elif case == "cancel-backoff":
        passed = not done and not retries and provider.requests == 1
    else:
        passed = policy_stopped
    return {"case": case, "repeat": repeat, "profile": "adaptive" if adaptive else "disabled",
            "passed": passed, "completed": done, "cancelled_exception": cancelled,
            "requests": provider.requests, "retry_requests": len(retries), "external_effects": len(effects),
            "elapsed_ms": duration, "recoverable_opportunity": case in RECOVERABLE, "trace": trace}


async def run(repeats):
    rows = []
    for repeat in range(1, repeats + 1):
        for case in CASES:
            for adaptive in (False, True):
                rows.append(await trial(case, adaptive, repeat))
    return rows


def summarize(rows):
    result = {}
    for profile in ("disabled", "adaptive"):
        selected = [row for row in rows if row["profile"] == profile]
        eligible = [row for row in selected if row["recoverable_opportunity"]]
        forbidden = [row for row in selected if not row["recoverable_opportunity"]]
        result[profile] = {"trials": len(selected), "checks_passed": sum(r["passed"] for r in selected),
            "recoverable_opportunities": len(eligible), "recovered": sum(r["completed"] for r in eligible),
            "recovery_success_rate": sum(r["completed"] for r in eligible) / len(eligible),
            "false_retry_count": sum(r["retry_requests"] for r in forbidden),
            "requests": sum(r["requests"] for r in selected),
            "external_effects": sum(r["external_effects"] for r in selected),
            "repeated_external_effects": sum(max(0, r["external_effects"] - 1) for r in selected),
            "mean_elapsed_ms": statistics.mean(r["elapsed_ms"] for r in selected), "cost": None}
    result["delta"] = {"added_requests": result["adaptive"]["requests"] - result["disabled"]["requests"],
                       "added_mean_elapsed_ms": result["adaptive"]["mean_elapsed_ms"] - result["disabled"]["mean_elapsed_ms"], "cost_change": None}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repeat", type=int, default=3, choices=range(1, 11))
    args = parser.parse_args()
    def source_fingerprint():
        source_paths = sorted(Path("firik_agent").rglob("*.py"))
        return hashlib.sha256(b"".join(str(p).encode() + b"\0" + p.read_bytes() for p in source_paths)).hexdigest()
    source_hash = source_fingerprint()
    rows = asyncio.run(run(args.repeat))
    source_after = source_fingerprint()
    if source_after != source_hash:
        raise RuntimeError("Runtime source changed during probes; rerun against a stable checkout.")
    result = {"schema_version": 1, "method": "same-source paired scripted native runtime probes",
        "source_sha256": source_hash, "source_sha256_after": source_after, "summary": summarize(rows), "results": rows,
        "limitations": ["Synthetic offline fault probes; no actual service or network call.",
            "Disabled and adaptive profiles use the candidate source; this is not a reconstructed legacy baseline.",
            "Latency includes event construction and scheduler noise; backoff is deliberately 1–10ms for bounded probes.",
            "The external ledger is a deterministic in-memory side effect, not proof of remote-service idempotency.",
            "Usage is synthetic on successful terminal requests, missing on injected failures; monetary cost remains unknown."]}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    summary = result["summary"]
    lines = ["# Scripted native recovery probes", "", "| Measure | Recovery disabled | Adaptive |", "| --- | ---: | ---: |"]
    for key in ("checks_passed", "recoverable_opportunities", "recovered", "recovery_success_rate", "false_retry_count", "requests", "external_effects", "repeated_external_effects", "mean_elapsed_ms"):
        lines.append(f"| {key} | {summary['disabled'][key]} | {summary['adaptive'][key]} |")
    lines.extend(["", "These profiles run the same candidate source. The frozen Phase 1 before/after comparison is separate.", ""])
    lines.extend("- " + item for item in result["limitations"])
    (args.output / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if all(row["passed"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
