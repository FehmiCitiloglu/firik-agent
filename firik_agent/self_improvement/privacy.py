"""Defense in depth for retained data, not a replacement for host permissions."""
from __future__ import annotations

import re
from firik_agent.projects.coding_sessions import redact_sensitive

_POLICY = re.compile(
    r"ignore (?:all |the |your )?(?:previous|system|developer) instructions|"
    r"(?:disable|bypass|remove|weaken) (?:the |all |existing )?(?:sandbox|approval|permission|security|audit)|"
    r"(?:reveal|exfiltrate|print|send) (?:the |all |your )?(?:credentials|secrets|api.keys)|"
    r"(?:sistem|güvenlik|onay|sandbox).{0,24}(?:devre dışı bırak|atla)", re.I)
_SECRET = re.compile(r"-----BEGIN .{0,30}PRIVATE KEY-----|\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}|"
                     r"\b(?:api[_ -]?key|password|secret|access[_ -]?token)\s*[:=]\s*\S+", re.I)


def validate_knowledge(content: str, *, limit: int = 4000) -> str:
    if not isinstance(content, str) or not content.strip() or len(content) > limit:
        raise ValueError(f"Knowledge must contain 1..{limit} characters")
    if _SECRET.search(content) or redact_sensitive(content) != content:
        raise ValueError("Secret-bearing knowledge is not retained")
    if _POLICY.search(content):
        raise ValueError("Untrusted knowledge cannot change host policy")
    if "\x00" in content:
        raise ValueError("Invalid knowledge content")
    return content.strip()


def safe_label(value: object, limit: int = 160) -> str:
    text = str(value or "")[:limit]
    return str(redact_sensitive(text))
