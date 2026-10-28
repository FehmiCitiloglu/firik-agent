"""Deterministic execution-efficiency defaults, independent of authorization.

Profiles are host configuration. A model, tool result, or retrieved source cannot
select a profile. The normal permission gate always remains authoritative.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


PROFILES = {
    "minimal": {"context_tokens": 3000, "max_output_tokens": 1024, "max_iterations": 6,
                "max_parallel_agents": 1, "per_agent_tokens": 4096, "max_elapsed_seconds": 180, "orchestration": "minimal"},
    "coding": {"context_tokens": 8000, "max_output_tokens": 4096, "max_iterations": 20,
               "max_parallel_agents": 2, "per_agent_tokens": 16384, "max_elapsed_seconds": 900, "orchestration": "focused_verification"},
    "research": {"context_tokens": 14000, "max_output_tokens": 4096, "max_iterations": 24,
                 "max_parallel_agents": 2, "per_agent_tokens": 24576, "max_elapsed_seconds": 1200, "orchestration": "durable_notes"},
    "deep-debug": {"context_tokens": 12000, "max_output_tokens": 4096, "max_iterations": 28,
                   "max_parallel_agents": 2, "per_agent_tokens": 24576, "max_elapsed_seconds": 1200, "orchestration": "hypothesis_verification"},
    "multi-agent": {"context_tokens": 10000, "max_output_tokens": 4096, "max_iterations": 24,
                    "max_parallel_agents": 3, "per_agent_tokens": 16384, "max_elapsed_seconds": 900, "orchestration": "owned_delegation"},
    "local-efficient": {"context_tokens": 4000, "max_output_tokens": 1536, "max_iterations": 10,
                        "max_parallel_agents": 1, "per_agent_tokens": 6144, "max_elapsed_seconds": 300, "orchestration": "minimal"},
}
_BOUNDS = {"context_tokens": (512, 262144), "max_output_tokens": (128, 65536),
           "max_iterations": (1, 100), "max_parallel_agents": (1, 6), "per_agent_tokens": (512, 2000000),
           "max_elapsed_seconds": (10, 3600)}


def resolve_harness_profile(project_preferences: Mapping[str, Any] | None = None,
                            session_override: str | Mapping[str, Any] | None = None,
                            user_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Resolve user -> project -> session host configuration without mutating it.

Numeric overrides are explicit host choices and may expand a default. They
never modify permission, selected MCP tools, or a provider capability claim.
"""
    preferences, config = dict(project_preferences or {}), dict(user_config or {})
    name = config.get("harness_profile", "coding")
    if preferences.get("harness_profile"):
        name = preferences["harness_profile"]
    overrides = {**dict(config.get("harness_overrides") or {}),
                 **dict(preferences.get("harness_overrides") or {})}
    if isinstance(session_override, str):
        name = session_override
    elif isinstance(session_override, Mapping):
        name = session_override.get("name", session_override.get("harness_profile", name))
        overrides.update({key: value for key, value in session_override.items() if key in _BOUNDS or key == "tool_selection"})
    elif session_override is not None:
        raise ValueError("Çalışma profili metin veya yapılandırma nesnesi olmalıdır.")
    if name not in PROFILES:
        raise ValueError("Bilinmeyen çalışma profili: " + str(name))
    result = {"name": name, **PROFILES[name], "tool_selection": "dynamic",
              "fallback_policy": "permitted_inventory_on_explicit_discovery",
              "permission_effect": "none"}
    for key, (low, high) in _BOUNDS.items():
        if key in overrides:
            value = overrides[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Çalışma profili sınırı geçersiz: " + key)
            result[key] = value
    if "tool_selection" in overrides:
        if overrides["tool_selection"] not in {"dynamic", "all_permitted"}:
            raise ValueError("Araç seçimi dynamic veya all_permitted olmalıdır.")
        result["tool_selection"] = overrides["tool_selection"]
    return result


def runtime_profile(control: Any = None, session: Any = None) -> dict[str, Any] | None:
    """No implicit change to legacy call sites without host profile configuration."""
    value = getattr(control, "harness_profile", None) or getattr(session, "harness_profile", None)
    return resolve_harness_profile(session_override=value) if value else None


def output_limit(requested: int, profile: Mapping[str, Any] | None,
                 capabilities: Mapping[str, Any] | None = None) -> int:
    limit = max(1, requested)
    for value in ((profile or {}).get("max_output_tokens"), (capabilities or {}).get("max_output_tokens")):
        if type(value) is int and value > 0:
            limit = min(limit, value)
    return limit


def profile_instructions(profile: Mapping[str, Any] | None) -> str:
    """Host instructions refer to durable existing plan/evidence tools."""
    if not profile:
        return ""
    advice = {
        "minimal": "Kısa bağlamla ve gerekli en az araç çağrısıyla ilerle.",
        "local-efficient": "Küçük ve açık araç çağrıları kullan; tek düzenleme ve odaklı doğrulamayla ilerle. Gereken ek araçları discover_tools ile bul.",
        "coding": "İlgili kaynakları getir, hedefli düzenleme yap ve etkilediğin davranışı odaklı testle doğrula.",
        "research": "Araştırma notlarını ve deney adımlarını update_plan açıklamalarında, sonuç ve kanıtlarını record_verification ile kalıcılaştır; varsayımları belirt.",
        "deep-debug": "Hata izini takip et. Hipotezleri update_plan adımlarına, deney ve tanı sonuçlarını record_verification kayıtlarına bağla; başarısız hipotezleri açıkça belirt.",
        "multi-agent": "Bağımsız işleri açık dosya sahipliği ve write_paths ile delege et; alt ajanların kanıtlarını ana görev kabul kriterlerinde birleştir.",
    }
    return "Çalışma profili: " + str(profile["name"]) + ". " + advice.get(profile["name"], "") + " Profil hiçbir izin vermez."
