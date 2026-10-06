"""Provider-declared capabilities; unknown values remain unknown."""
from __future__ import annotations

import math
from typing import Any, Mapping


def model_capabilities(metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Accept explicit verified provider data, never infer from a model name."""
    metadata = dict(metadata or {})
    source = metadata.get("source") or metadata.get("capability_source")
    verified = metadata.get("verified") is True or source in {"provider", "verified_probe"}
    result: dict[str, Any] = {"source": source if verified else None, "context_window": None,
                            "supports_tools": None, "supports_streaming": None,
                            "supports_structured_output": None, "supports_vision": None,
                            "input_cost_per_million": None, "output_cost_per_million": None}
    if not verified:
        return result
    window = metadata.get("context_window")
    if isinstance(window, int) and not isinstance(window, bool) and window > 0:
        result["context_window"] = window
    for key in ("supports_tools", "supports_streaming", "supports_structured_output", "supports_vision"):
        if isinstance(metadata.get(key), bool):
            result[key] = metadata[key]
    for key in ("input_cost_per_million", "output_cost_per_million"):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 and math.isfinite(value):
            result[key] = value
    return result


def context_budget(capabilities: Mapping[str, Any], local_budget: int = 6000,
                   reserved_tokens: int = 4000) -> dict[str, Any]:
    window = capabilities.get("context_window")
    budget = max(1, int(local_budget))
    if isinstance(window, int) and window > 0:
        budget = min(budget, max(1, window - max(0, reserved_tokens)))
    return {"token_budget": budget, "context_window": window,
            "capacity_known": window is not None, "estimate_only": True,
            "label": "Yerel bağlam tahmini; model kapasitesi bilinmiyor" if window is None
            else "Yerel bağlam tahmini; sağlayıcı kapasitesi doğrulandı"}
