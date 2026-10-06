"""Bounded metadata about the actual model request, without copying prompt contents."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

LABELS = {"system": "Sistem talimatları", "agent_instructions": "Ajan talimatları", "project_rules": "Proje kuralları", "task": "Görev ve kabul kriterleri",
          "memory": "Hafıza ve mimari kararlar", "previous_task_summary": "Önceki görev özeti",
          "selected_files": "Seçili dosyalar", "retrieved_files": "Otomatik getirilen dosyalar",
          "git": "Git farkları", "documents": "Belgeler", "skills": "Etkin beceriler",
          "conversation": "Sohbet ve araç sonuçları", "tool_schemas": "Araç şemaları", "mcp_context": "MCP şemaları"}


def inspect_request_context(messages: Sequence[Any], schemas: Sequence[Mapping[str, Any]] | None = None,
                            capabilities: Mapping[str, Any] | None = None, *,
                            visibility_report: Sequence[Mapping[str, Any]] | None = None,
                            context_items: Sequence[Mapping[str, Any]] | None = None,
                            agent_instructions: str | None = None,
                            output_tokens: int = 2048) -> dict[str, Any]:
    """Count serialized messages and schemas after compaction; all numbers are estimates."""
    capabilities, schemas = capabilities or {}, list(schemas or [])
    def estimate(value):
        return math.ceil(len(json.dumps(value, ensure_ascii=False, default=str)) / 4)
    rows = [message.to_dict() if hasattr(message, "to_dict") else dict(message) for message in messages]
    system_tokens = sum(estimate(row) for row in rows if row.get("role") == "system")
    conversation_tokens = sum(estimate(row) for row in rows if row.get("role") != "system")
    counts = dict.fromkeys(LABELS, 0)
    mapping = {"instructions": "project_rules", "feature": "task", "memory": "memory", "session": "previous_task_summary",
               "git": "git", "documents": "documents", "skills": "skills"}
    remaining = system_tokens
    for item in visibility_report or []:
        category = mapping.get(item.get("key"))
        value = item.get("tokens_used", 0)
        if category and isinstance(value, int) and not isinstance(value, bool):
            used = min(remaining, max(0, value))
            counts[category] += used
            remaining -= used
    file_tokens = {key: sum(max(0, int(item.get("tokens_used", 0))) for item in context_items or []
                          if item.get("category") == key and item.get("included", True))
                   for key in ("selected_files", "retrieved_files")}
    # Compatibility for callers that only have the original slice report.
    if not any(file_tokens.values()):
        file_tokens["retrieved_files"] = sum(max(0, int(item.get("tokens_used", 0))) for item in visibility_report or [] if item.get("key") == "code")
    for key, value in file_tokens.items():
        counts[key] = min(remaining, value)
        remaining -= counts[key]
    if agent_instructions and any(agent_instructions in row.get("content", "") for row in rows
                                  if row.get("role") == "system" and isinstance(row.get("content"), str)):
        counts["agent_instructions"] = min(remaining, math.ceil(len(agent_instructions) / 4))
        remaining -= counts["agent_instructions"]
    counts["system"], counts["conversation"] = remaining, conversation_tokens
    schema_tokens = estimate(schemas) if schemas else 0
    # Session-filtered MCP names use the canonical server separator.
    mcp = [schema for schema in schemas if str(schema.get("function", {}).get("name", "")).startswith("mcp_")
           or "__" in str(schema.get("function", {}).get("name", ""))]
    mcp_tokens = min(schema_tokens, estimate(mcp)) if mcp else 0
    counts["mcp_context"], counts["tool_schemas"] = mcp_tokens, schema_tokens - mcp_tokens
    total = sum(counts.values())
    window = capabilities.get("context_window")
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        window = None
    reserved = max(0, int(output_tokens))
    return {"categories": [{"key": key, "label": LABELS[key], "tokens": count, "estimate_only": True} for key, count in counts.items()],
            "estimated_input_tokens": total, "context_window": window, "output_reserved_tokens": reserved,
            "fill_ratio": min(1.0, total / window) if window else None,
            "remaining_tokens": max(0, window - total - reserved) if window else None,
            "over_capacity": total + reserved > window if window else None,
            "estimate_only": True, "count_method": "serialized_characters_divided_by_four",
            "label": "Tam isteğin yerel token tahmini; sağlayıcı token ölçümü değildir",
            "capacity_label": "Model kapasitesi sağlayıcı tarafından bildirilmedi" if window is None else "Doğrulanmış model kapasitesi"}
