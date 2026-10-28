"""Provider-declared capabilities; unknown values remain unknown."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping


def model_capabilities(metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Accept explicit verified provider data, never infer from a model name."""
    metadata = dict(metadata or {})
    source = metadata.get("source") or metadata.get("capability_source")
    verified_sources = {"provider", "verified_probe", "verified_config"}
    verified = (metadata.get("verified") is True or source in verified_sources) and metadata.get("verified") is not False
    provenance = source if source in verified_sources else "verified_config" if metadata.get("verified") is True else None
    result: dict[str, Any] = {"source": provenance if verified else None, "context_window": None,
                            "supports_tools": None, "supports_streaming": None,
                            "supports_structured_output": None, "supports_vision": None,
                            "supports_reasoning": None, "max_output_tokens": None,
                            "input_cost_per_million": None, "output_cost_per_million": None,
                            "cached_input_cost_per_million": None,
                            "evidence_source": (metadata.get("evidence_source")
                                                if metadata.get("evidence_source") in verified_sources
                                                else provenance if verified else source if source in verified_sources else None),
                            "evidence_url": None, "timestamp": None,
                            "verification_status": "unknown", "confidence": "unknown",
                            "verified": False if metadata.get("verified") is False else None,
                            "supports_usage_accounting": None,
                            "supports_native_caching": None,
                            "tokenizer": None, "native_caching": None}
    if not verified:
        return result
    nested = metadata.get("capabilities")
    if isinstance(nested, Mapping):
        metadata = {**metadata, **nested}
    # Keep verification strength stable across persistence/re-normalization.
    # A provider declaration remains distinct from an explicit verification;
    # never serialize a declared capability as verified=False (which revokes it).
    result["verified"] = True if metadata.get("verified") is True or source in {"verified_probe", "verified_config"} else None
    result["verification_status"] = "verified" if metadata.get("verified") is True or source in {"verified_probe", "verified_config"} else "provider_declared"
    result["confidence"] = result["verification_status"]
    # Preserve evidence time; do not invent a verification date for old data.
    timestamp = metadata.get("timestamp") or metadata.get("verified_at") or metadata.get("observed_at")
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                result["timestamp"] = timestamp
        except ValueError:
            pass
    evidence_url = metadata.get("evidence_url")
    if isinstance(evidence_url, str):
        from urllib.parse import urlsplit
        parsed = urlsplit(evidence_url)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment:
            result["evidence_url"] = evidence_url[:2048]
    for key in ("context_window", "max_output_tokens"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            result[key] = value
    for key in ("supports_tools", "supports_streaming", "supports_structured_output", "supports_vision", "supports_reasoning",
                "supports_usage_accounting", "supports_native_caching"):
        if isinstance(metadata.get(key), bool):
            result[key] = metadata[key]
    if result["supports_reasoning"] is None and isinstance(metadata.get("reasoning_effort"), bool):
        result["supports_reasoning"] = metadata["reasoning_effort"]
    tokenizer = metadata.get("tokenizer")
    if isinstance(tokenizer, Mapping) and tokenizer.get("verified") is True:
        # An explicit encoding is required; the model's name is never consulted.
        encoding = tokenizer.get("encoding")
        if tokenizer.get("library") == "tiktoken" and isinstance(encoding, str) and 0 < len(encoding) <= 100:
            from importlib.util import find_spec
            library_available = find_spec("tiktoken") is not None
            result["tokenizer"] = {"library": "tiktoken", "encoding": encoding, "verified": True,
                                   "library_available": library_available,
                                   "available": None if library_available else False,
                                   "evidence_url": result["evidence_url"]}
    caching = metadata.get("native_caching")
    if isinstance(caching, Mapping) and result["supports_native_caching"] is True:
        result["native_caching"] = {key: caching[key] for key in ("automatic", "supports_cache_control", "reports_cached_tokens")
                                    if isinstance(caching.get(key), bool)}
    for key in ("input_cost_per_million", "output_cost_per_million", "cached_input_cost_per_million"):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 and math.isfinite(value):
            result[key] = value
    return result


def usage_tokens(usage: Mapping[str, Any] | None) -> dict[str, int | None]:
    """Normalize reported usage, retaining absent values as absent."""
    usage = usage or {}
    def count(*values):
        return next((value for value in values if isinstance(value, int)
                     and not isinstance(value, bool) and value >= 0), None)
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    if not isinstance(details, Mapping):
        details = {}
    incoming = count(usage.get("prompt_tokens"), usage.get("input_tokens"))
    outgoing = count(usage.get("completion_tokens"), usage.get("output_tokens"))
    cached = count(details.get("cached_tokens"), usage.get("cached_tokens"), usage.get("cache_read_input_tokens"))
    total = count(usage.get("total_tokens"))
    if total is None and incoming is not None and outgoing is not None:
        total = incoming + outgoing
    return {"input_tokens": incoming, "output_tokens": outgoing, "cached_tokens": cached, "total_tokens": total}


def estimate_usage_cost(usage: Mapping[str, Any] | None, capabilities: Mapping[str, Any] | None) -> dict[str, Any]:
    """Price reported tokens with verified prices; never present missing usage as zero."""
    usage, capabilities = usage or {}, capabilities or {}
    tokens = usage_tokens(usage)
    reported = usage.get("cost")
    if isinstance(reported, (int, float)) and not isinstance(reported, bool) and reported >= 0 and math.isfinite(reported):
        return {**tokens, "cost": reported, "currency": usage.get("currency", "USD"), "source": "provider",
                "estimate_only": False, "label": "Sağlayıcının bildirdiği maliyet"}
    incoming, outgoing = tokens["input_tokens"], tokens["output_tokens"]
    in_rate, out_rate = capabilities.get("input_cost_per_million"), capabilities.get("output_cost_per_million")
    valid_rate = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 and math.isfinite(value)
    verified = capabilities.get("source") in {"provider", "verified_probe", "verified_config"}
    if not verified or not valid_rate(in_rate) or not valid_rate(out_rate):
        return {**tokens, "cost": None, "currency": "USD", "source": None, "estimate_only": True,
                "label": "Fiyat bilgisi sağlayıcı tarafından sunulmuyor"}
    if incoming is None or outgoing is None:
        return {**tokens, "cost": None, "currency": "USD", "source": "verified_prices", "estimate_only": True,
                "label": "Maliyet için giriş ve çıkış token kullanımı sağlayıcıdan bekleniyor"}
    cached = min(tokens["cached_tokens"] or 0, incoming)
    cached_rate = capabilities.get("cached_input_cost_per_million")
    # If no cache price is given, show the explicit standard-price estimate.
    cache_priced = valid_rate(cached_rate)
    cost = ((incoming - cached) * in_rate + cached * (cached_rate if cache_priced else in_rate) + outgoing * out_rate) / 1_000_000
    return {**tokens, "cost": cost, "currency": "USD", "source": "verified_prices", "estimate_only": True,
            "cache_discount_known": cache_priced,
            "label": "Doğrulanmış fiyatlarla tahmini maliyet" + ("; önbellek indirimi bildirilmedi" if cached and not cache_priced else "")}


def context_budget(capabilities: Mapping[str, Any], local_budget: int = 6000,
                   reserved_tokens: int = 4000) -> dict[str, Any]:
    window = capabilities.get("context_window")
    if type(window) is not int or window <= 0:
        window = None
    budget = max(1, int(local_budget))
    if isinstance(window, int) and not isinstance(window, bool) and window > 0:
        budget = min(budget, max(1, window - max(0, reserved_tokens)))
    return {"token_budget": budget, "context_window": window,
            "capacity_known": window is not None, "estimate_only": True,
            "label": "Yerel bağlam tahmini; model kapasitesi bilinmiyor" if window is None
            else "Yerel bağlam tahmini; sağlayıcı kapasitesi doğrulandı"}
