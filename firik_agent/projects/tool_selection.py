"""Inspectable schema narrowing after the runtime permission filter.

Selection is an efficiency policy only. An omitted schema can be discovered;
the independently enforced permission owner still validates every execution.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_READ = {"read_file", "list_files", "search_project", "search_symbols", "git_status", "git_diff"}
_EDIT = {"edit_file", "apply_patch", "write_file", "undo_change"}
_VERIFY = {"discover_quality_gates", "run_quality_gate", "run_tests", "record_verification"}
_DELEGATE = {"spawn_subagent", "wait_subagent", "cancel_subagent", "send_message"}


def classify_intent(task: str) -> str:
    words = set(re.findall(r"[\w-]+", task.casefold()))
    if words & {"review", "audit", "incele", "denetle", "değerlendir"}:
        return "review"
    if words & {"build", "compile", "compiler", "derleme", "tsc", "typecheck"}:
        return "build-repair"
    if words & {"debug", "crash", "exception", "traceback", "hata", "düzelt", "fix", "bug", "repair"}:
        return "bug-fix"
    if words & {"research", "araştır", "araştırma", "investigate"}:
        return "research"
    if words & {"plan", "planning", "planla", "tasarla"}:
        return "planning"
    return "coding"


def select_tool_schemas(schemas: Sequence[Mapping[str, Any]], *, task: str = "",
                        profile: Mapping[str, Any] | None = None,
                        discovered: set[str] | None = None,
                        explicit_mcp: set[str] | None = None) -> tuple[list[dict], dict[str, Any]]:
    """`schemas` MUST be the already permission-filtered inventory.

    Unknown external tools require explicit host selection or discovery. An
    all_permitted override is the documented fallback for unreliable selectors.
    """
    profile = profile or {}
    intent = classify_intent(task)
    allowed = _READ | {"discover_tools"}
    if intent in {"coding", "bug-fix", "build-repair"}:
        allowed |= _EDIT | _VERIFY | {"update_plan"}
    if intent == "build-repair":
        allowed.add("run_command")
    if intent in {"planning", "research"}:
        allowed |= {"update_plan", "record_verification"}
    if profile.get("name") == "deep-debug":
        allowed |= _VERIFY | {"run_command", "update_plan"}
    if profile.get("name") == "multi-agent":
        allowed |= _DELEGATE | {"update_plan", "record_verification"}
    if profile.get("name") in {"minimal", "local-efficient"}:
        # Keep one editing route; other allowed routes are explicitly discoverable.
        allowed -= {"apply_patch", "write_file", "undo_change", "run_quality_gate", "git_status", "list_files"}
    allowed |= set(discovered or ()) | set(explicit_mcp or ())
    rows, selected, omitted = [], [], []
    for schema in schemas:
        name = str(schema.get("function", {}).get("name", ""))
        include = name in allowed or profile.get("tool_selection") == "all_permitted"
        reason = ("Açık keşif veya kullanıcı araç seçimi" if name in set(discovered or ()) | set(explicit_mcp or ())
                  else "İzinli araç envanteri yedeği" if profile.get("tool_selection") == "all_permitted"
                  else "Görev amacı ve çalışma profili" if include else "Gerektiğinde discover_tools ile keşfedilebilir")
        (selected if include else omitted).append({"name": name, "reason": reason})
        if include:
            rows.append(dict(schema))
    return rows, {"intent": intent, "profile": profile.get("name"), "selected": selected, "omitted": omitted,
                  "permission_effect": "none", "fallback": "all_permitted host override or explicit discover_tools"}
