"""Bounded deterministic reflection with no shell, network or model tools.

Rules identify reproducible opportunities; hypotheses are staged explicitly and
are never mislabeled as user-confirmed knowledge or performance improvements.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from .models import digest


class ReflectionEngine:
    def digest(self, experiences: list[dict]) -> dict:
        events = list(reversed(experiences))
        return {"evidence_ids": [e["id"] for e in events],
                "facts": {"events": len(events), "verified_checks": sum(e["verified"] for e in events),
                          "failures": sum(e["outcome"] == "failed" for e in events),
                          "outcomes": dict(Counter(e["outcome"] for e in events)),
                          "tools": dict(Counter(e["facts"].get("tool_name", "") for e in events
                                                if e["facts"].get("tool_name")))},
                "hypotheses": [], "fingerprint": digest(sorted(e["id"] for e in events)),
                "authority": "recommendations_only", "origin": "self_improvement"}

    def reflect(self, experiences: list[dict], *, related: list[dict] | None = None, maximum=3) -> dict:
        summary = self.digest(experiences)
        proposals = []
        if not experiences:
            return {**summary, "proposals": [], "reason": "No permitted evidence"}
        chronological = list(reversed(experiences))
        for item in chronological:
            facts = item["facts"]
            if item["event_type"] == "user_feedback" and facts.get("user_confirmed") is True and facts.get("feedback"):
                # Only the host's explicit-feedback API writes these fields;
                # arbitrary runtime metadata cannot assert user confirmation.
                proposals.append({"kind": "memory", "title": "Explicit user feedback",
                    "payload": {"content": facts["feedback"], "kind": "preference" if facts.get("correction") else "episodic",
                                "confidence": 1.0, "verification_status": "user_confirmed"},
                    "evidence_ids": [item["id"]], "source_type": "user"})
        failed_commands = set()
        for item in chronological:
            facts = item["facts"]
            command = facts.get("command")
            if item["event_type"] == "verification" and command:
                if item["outcome"] == "failed":
                    failed_commands.add(command)
                elif item["verified"] and command in failed_commands:
                    proposals.append({"kind": "memory", "title": "Verified validation recovery",
                        "payload": {"content": f"Project validation command `{command}` was verified successfully after an earlier failure. "
                                               "Inspect the current diagnostic and source state before retrying; prior success does not verify new changes.",
                                    "kind": "failure", "confidence": 0.8, "verification_status": "observed",
                                    "fact_key": "validation:" + digest(command)},
                        "evidence_ids": [e["id"] for e in chronological if e["facts"].get("command") == command],
                        "source_type": "host_evidence"})
        errors = Counter(e["facts"].get("tool_name") for e in chronological
                         if e["event_type"] == "tool_call_error" and e["facts"].get("tool_name"))
        for tool, count in errors.items():
            if count >= 3:
                proposals.append({"kind": "tool", "title": f"Review repeated {tool} failures",
                    "payload": {"problem": f"{count} observed tool errors for {tool}; root cause is unverified.",
                                "existing_capability_comparison": ["Check existing tool arguments and authorization",
                                  "Check existing skills", "Review description before adding a tool"],
                                "tool_name": tool, "side_effects": "none", "required_permissions": [],
                                "hypothesis": "A typed adapter or clearer metadata may reduce misuse.",
                                "schema": {"type": "object", "properties": {}, "additionalProperties": False},
                                "timeout_seconds": 10, "maintenance_cost": "unknown",
                                "tests": [], "risks": ["Selection mistakes may be provider or input failures"]},
                    "evidence_ids": [e["id"] for e in chronological if e["facts"].get("tool_name") == tool]})
        overflows = [e for e in chronological if e["facts"].get("failure_kind") in {"context_overflow", "context_length"}]
        if len(overflows) >= 2:
            proposals.append({"kind": "harness", "title": "Measure context selection overhead",
                "payload": {"hypothesis": "A smaller retrieved memory budget may reduce overflow; correctness must be compared.",
                            "settings": {"memory_token_budget": 300}, "primary_metric": "verified_success_rate",
                            "report": None}, "evidence_ids": [e["id"] for e in overflows]})
        # Generalize only an identical multi-step workflow in distinct runs with
        # actual host-verified completion, rather than counting repeated chat turns.
        sessions = defaultdict(list)
        for event in (related or experiences):
            sessions[event["facts"].get("run_id") or event["session_id"]].append(event)
        workflows = defaultdict(list)
        for sid, entries in sessions.items():
            entries = sorted(entries, key=lambda x: x["created_at"])
            completed = any(e["verified"] and e["facts"].get("completion_classification") == "completed_verified" for e in entries)
            tools = list(dict.fromkeys(e["facts"].get("tool_name") for e in entries
                                      if e["event_type"] == "tool_call_started" and e["facts"].get("tool_name")))
            commands = list(dict.fromkeys(e["facts"]["command"] for e in entries if e["verified"] and e["facts"].get("command")))
            if completed and len(tools) >= 2 and commands:
                workflows[digest([tools, commands])].append((sid, tools, commands, entries))
        for fingerprint, runs in workflows.items():
            current_runs = {e["facts"].get("run_id") or e["session_id"] for e in experiences}
            if len(runs) < 2 or not any(r[0] in current_runs for r in runs):
                continue
            _, tools, commands, _ = runs[0]
            procedure = ("## Triggers\nUse for this project's recurring verified validation workflow.\n\n"
                         "## Permissions\nInherit current session permissions; request each required approval.\n\n"
                         "## Procedure\n" + "\n".join(f"{i+1}. Use the existing `{tool}` tool when appropriate to the current task."
                                                                        for i, tool in enumerate(tools)) +
                         "\n\n## Validation\nRun the project-confirmed validation commands through the approved executor:\n" +
                         "\n".join(f"- `{command}`" for command in commands) +
                         "\n\n## Example\nAfter a scoped source fix, inspect the diff and obtain fresh test evidence.\n\n"
                         "## Failure handling and pitfalls\nStop on authorization errors or unknown execution outcomes. "
                         "Prior success is not current verification; do not blindly replay a failed mutation.\n")
            proposals.append({"kind": "skill", "title": "Recurring project validation workflow",
                "payload": {"name": "learned-validation-" + fingerprint[:10], "description": "Project validation workflow from repeated verified runs",
                            "instructions": procedure, "required_permissions": [], "version": "1.0.0"},
                "evidence_ids": [e["id"] for run in runs[:3] for e in run[3]], "source_type": "host_evidence"})
        return {**summary, "proposals": proposals[:maximum],
                "reason": "Evidence-backed opportunities" if proposals else "Insufficient reusable evidence; no speculative fact saved"}
