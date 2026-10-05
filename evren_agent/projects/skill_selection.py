"""Progressive skill context: catalogue first, explicit selections only."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from evren_agent.projects.indexer import sanitize_secrets


def _definition(skill: Any, include_instructions: bool = True) -> dict[str, Any]:
    if isinstance(skill, Mapping):
        return dict(skill)
    fields = ("name", "description", "tags", "trigger", "resources")
    if include_instructions:
        fields += ("instructions",)
    return {name: getattr(skill, name, None) for name in fields}


def skill_catalogue(skills: Iterable[Any]) -> list[dict[str, Any]]:
    """Return metadata without loading or injecting any instruction body."""
    catalogue = []
    seen = set()
    for skill in skills:
        item = _definition(skill, include_instructions=False)
        name = str(item.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        catalogue.append({"name": name, "description": sanitize_secrets(str(item.get("description") or ""))[:300],
                          "trigger": sanitize_secrets(str(item.get("trigger") or ""))[:200],
                          "tags": list(item.get("tags") or []), "resources": list(item.get("resources") or [])})
    return catalogue


def select_skill_instructions(skills: Iterable[Any], names: Iterable[str]) -> list[dict[str, str]]:
    selected = {name.replace("-", "_") for name in names}
    result = []
    for skill in skills:
        item = _definition(skill)
        name = str(item.get("name") or "")
        if name.replace("-", "_") in selected:
            instructions = item.get("instructions")
            if callable(instructions):
                instructions = instructions()
            result.append({"name": name, "description": sanitize_secrets(str(item.get("description") or "")),
                           "instructions": sanitize_secrets(str(instructions or ""))})
    return result
