"""Explicit composer references and command metadata shared by GUI and context service."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from firik_agent.projects.indexer import is_sensitive_file, sanitize_secrets

COMMANDS = {"plan": ("plan", "İstek için uygulanabilir bir görev planı oluştur."),
            "test": (None, "Projenin testlerini ve kalite kontrollerini çalıştır; gerçek sonuçları raporla."),
            "review": ("ask", "Değişiklikleri incele; bulguları dosya ve satırlarla raporla."),
            "fix": (None, "Belirtilen sorunu incele ve uygun düzeltmeyi uygula."),
            "agent": (None, "Belirtilen görevi uygun bir alt ajana devret."),
            "skill": (None, "Seçilen beceriyi bu istekte kullan."),
            "mcp": (None, "Seçilen MCP aracını mevcut izinler içinde kullan.")}
REFERENCE = re.compile(r'(?<![\w@])@(file|folder|memory|agent|terminal|commit|skill|mcp):(?:"([^"\r\n]+)"|([^\s,;]+))')


def parse_composer(text: str) -> dict[str, Any]:
    """Parsing never grants permissions or runs commands; references resolve separately."""
    text = str(text)
    match = re.match(r"^\s*/(plan|test|review|fix|agent|skill|mcp)(?=\s|$)\s*(.*)$", text, re.DOTALL)
    command = match.group(1) if match else None
    arguments = match.group(2).strip() if match else ""
    body = arguments if match else text
    references = [{"kind": item.group(1), "value": item.group(2) or item.group(3)} for item in REFERENCE.finditer(body)]
    clean = REFERENCE.sub("", body).strip()
    if command:
        clean = COMMANDS[command][1] + ("\n" + clean if clean else "")
    # Keep the user's concrete references visible to the model without pretending they resolved.
    if references:
        clean += "\nKullanıcının bağlam referansları: " + ", ".join(f"@{item['kind']}:{item['value']}" for item in references)
    return {"user_prompt": sanitize_secrets(clean), "command": command, "arguments": arguments,
            "mode": COMMANDS[command][0] if command else None, "references": references}


def composer_suggestions(query: str, *, files: Iterable[Any] = (), memories: Iterable[Any] = (),
                         agents: Iterable[Any] = (), terminals: Iterable[Any] = (), commits: Iterable[Any] = (),
                         skills: Iterable[Any] = (), mcp_tools: Iterable[Any] = (), limit: int = 12) -> list[dict[str, str]]:
    """Suggest only supplied scoped entities. Files are metadata, never content reads."""
    query = str(query).casefold().strip()
    if query.startswith("/"):
        return [{"kind": "command", "id": name, "label": "/" + name, "insert": "/" + name + " "}
                for name in COMMANDS if name.startswith(query[1:])][:max(0, limit)]
    if not query.startswith("@"):
        return []
    needle = query[1:]
    result = []
    def add(kind, identifier, label):
        identifier = str(identifier or "")
        if not identifier or any(character in identifier for character in ('"', '\n', '\r')):
            return
        qualified = f"{kind}:{identifier}"
        if needle and needle not in qualified.casefold() and needle not in str(label).casefold():
            return
        value = f'"{identifier}"' if any(character.isspace() for character in identifier) else identifier
        result.append({"kind": kind, "id": identifier, "label": sanitize_secrets(str(label))[:200], "insert": f"@{kind}:{value} "})
    for raw in files:
        item = raw if isinstance(raw, Mapping) else {"path": str(raw)}
        path = item.get("path") or item.get("file_path")
        if path and not is_sensitive_file(path) and ".." not in str(path).replace("\\", "/").split("/"):
            add("folder" if item.get("is_dir") else "file", path, path)
    for kind, items in (("memory", memories), ("agent", agents), ("terminal", terminals), ("commit", commits), ("skill", skills), ("mcp", mcp_tools)):
        for raw in items:
            item = raw if isinstance(raw, Mapping) else (raw.model_dump() if hasattr(raw, "model_dump") else {"id": str(raw)})
            add(kind, item.get("hash") if kind == "commit" else item.get("id") or item.get("name"),
                item.get("title") or item.get("name") or item.get("message") or item.get("content", "")[:80])
    unique = {(item["kind"], item["id"]): item for item in result}
    return list(unique.values())[:max(0, limit)]
