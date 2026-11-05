"""Narrow direct-user learning commands. No approval or execution operations.

Only the original user request is parsed; tool output, retrieved data and model
messages never enter this router. Unrecognized conversation follows normal chat.
"""
from __future__ import annotations

import json
import re

_REMEMBER = re.compile(r"^(?:remember that\s+|remember:\s*|şunu hatırla:\s*|bunu hatırla:\s*)(.+)$", re.I | re.S)
_STOP = {"stop automatic learning", "stop automatic learning.", "otomatik öğrenmeyi durdur", "öğrenmeyi durdur"}
_REVIEW = {"remember how we solved this problem", "turn this successful workflow into a skill",
           "why did you fail this task?", "bu sorunu nasıl çözdüğümüzü hatırla",
           "bu başarılı iş akışını beceriye dönüştür", "bu görevde neden başarısız oldun?"}
_CODE = {"show every pending change to your own code", "kendi kodundaki bekleyen tüm değişiklikleri göster"}


def recognized(prompt: str) -> bool:
    text = prompt.strip()
    return bool(_REMEMBER.fullmatch(text) or text.casefold() in _STOP | _REVIEW | _CODE)


def handle(coordinator, prompt: str, *, session_id: str, project_id=None) -> str | None:
    text = prompt.strip()
    lower = text.casefold()
    match = _REMEMBER.fullmatch(text)
    if match:
        result = coordinator.propose_memory(match.group(1), project_id=project_id, source_reference="user:chat:" + session_id)
        return "Knowledge staged for review; it is not active.\n" + json.dumps(result, ensure_ascii=False, indent=2)
    if lower in _STOP:
        coordinator.configure({"mode": "off", "automatic_reflection": False})
        return "Automatic learning stopped. Saved knowledge and proposals remain inspectable."
    if lower in _REVIEW:
        result = coordinator.reflect(session_id, project_id=project_id)
        return "Learning review from recorded evidence; no proposed change was applied.\n" + json.dumps(result, ensure_ascii=False, indent=2)
    if lower in _CODE:
        result = [row for row in coordinator.proposals(limit=200) if row["kind"] == "code"
                  and row["status"] in {"draft", "blocked", "pending_approval", "unknown", "deferred"}
                  and (project_id is None or row["project_id"] == project_id)]
        return json.dumps(result, ensure_ascii=False, indent=2)
    return None
