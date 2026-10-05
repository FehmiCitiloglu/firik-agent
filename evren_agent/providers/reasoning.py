"""Reasoning effort from explicit provider capabilities, without model-name guesses."""
from collections.abc import Mapping

DEFAULT_EFFORTS = ("low", "medium", "high")


def effort_levels(metadata: Mapping | None) -> list[str]:
    metadata = metadata or {}
    caps = metadata.get("capabilities", metadata)
    if not isinstance(caps, Mapping) or caps.get("reasoning_effort") is not True:
        return []
    values = caps.get("reasoning_effort_values")
    if values is None:
        values = DEFAULT_EFFORTS
    if not isinstance(values, (list, tuple)):
        return []
    return list(dict.fromkeys(v for v in values if isinstance(v, str) and v.strip() == v and v))


def effort_payload(metadata: Mapping | None, selected: str | None) -> dict:
    if selected is None or selected not in effort_levels(metadata):
        return {}
    caps = (metadata or {}).get("capabilities", metadata or {})
    aliases = caps.get("reasoning_effort_aliases")
    value = aliases.get(selected, selected) if isinstance(aliases, Mapping) else selected
    if not isinstance(value, str) or not value:
        return {}
    return {"reasoning_effort": value}
