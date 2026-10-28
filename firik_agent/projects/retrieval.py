"""Inspectable deterministic repository retrieval. Returned content is untrusted data.

No embeddings or language-server dependency is required. Every graph edge is
resolved against the current safe index, never against arbitrary filesystem paths.
"""
from __future__ import annotations

import posixpath
import re
import time
from collections import deque
from pathlib import PurePosixPath
from typing import Any, Callable

from firik_agent.projects.indexer import CodebaseIndexer, safe_project_path, sanitize_secrets

STAGES = ("intent_classification", "candidate_retrieval", "dependency_expansion",
          "relevance_scoring", "budget_allocation", "context_assembly", "inspection_metadata")
STOPWORDS = {"the", "and", "for", "this", "that", "with", "from", "please", "file", "files",
             "bir", "ile", "için", "olan", "bunu", "şunu", "dosya", "dosyayı", "görev", "görevi",
             "kodu", "incele", "açıkla", "uygula", "fix", "bug", "test", "review", "plan"}


def classify_intent(query: str) -> str:
    text = query.casefold()
    rules = (("deep-debug", r"traceback|compiler|derleme|build|diagnostic|hata izi|stack trace"),
             ("review", r"\breview\b|kod incele|gözden geçir"),
             ("research", r"\bresearch\b|araştır|investigat"),
             ("planning", r"\bplan\b|planla|tasarla"),
             ("bug-fix", r"\bbug\b|\bfix\b|düzelt|hata|failing|failed|başarısız"))
    return next((intent for intent, pattern in rules if re.search(pattern, text)), "coding")


def query_tokens(query: str) -> list[str]:
    # Retain identifiers, splitting common punctuation; short prose is not a signal.
    return list(dict.fromkeys(token.casefold() for token in re.findall(r"[\w$]+", query)
                              if len(token) > 2 and token.casefold() not in STOPWORDS))[:64]


def dependency_graph(indexed: list[dict[str, Any]]) -> dict[str, list[str]]:
    paths = {item["file_path"] for item in indexed}
    graph = {}
    for item in indexed:
        source = item["file_path"]
        parent = posixpath.dirname(source)
        dependencies = []
        for imported in item.get("imports", [])[:128]:
            if not isinstance(imported, str):
                continue
            bases = []
            if source.endswith(".py"):
                dots = len(imported) - len(imported.lstrip("."))
                module = imported.lstrip(".").replace(".", "/")
                if dots:
                    base = parent
                    for _ in range(dots - 1):
                        base = posixpath.dirname(base)
                    bases.append(posixpath.join(base, module))
                else:
                    bases.extend([module, posixpath.join(parent, module), "src/" + module])
                suffixes = (".py", "/__init__.py")
            else:
                if not imported.startswith("."):
                    continue  # No guessed node_modules or unconfigured TS path aliases.
                bases.append(posixpath.normpath(posixpath.join(parent, imported)))
                suffixes = ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js")
            for base in bases:
                for suffix in suffixes:
                    candidate = posixpath.normpath(base + suffix)
                    if candidate in paths and candidate != source:
                        dependencies.append(candidate)
        graph[source] = sorted(set(dependencies))
    return graph


def matching_tests(source: str, paths: list[str]) -> list[str]:
    stem = PurePosixPath(source).stem
    if stem.startswith("test_") or stem.endswith((".test", ".spec", "_test")):
        return []
    names = {"test_" + stem, stem + "_test", stem + ".test", stem + ".spec"}
    return [path for path in paths if PurePosixPath(path).stem in names][:8]


class ContextRetrievalPipeline:
    def __init__(self, indexer: CodebaseIndexer, indexed: list[dict[str, Any]], *,
                 excluded: Callable[[str], bool] | None = None):
        self.indexer = indexer
        self.indexed = []
        for entry in indexed:
            path = entry.get("file_path", "")
            if not path or (excluded and excluded(path)):
                continue
            try:
                safe_project_path(indexer.root, path)
            except (ValueError, OSError):
                continue
            self.indexed.append(entry)
        self.excluded = excluded or (lambda path: False)

    def run(self, query: str, *, pinned: list[str] | None = None, always: list[str] | None = None,
            selected: list[str] | None = None, task_paths: list[str] | None = None,
            verified_edits: list[dict[str, Any]] | None = None, diagnostics: list[dict[str, Any]] | None = None,
            limit: int = 24, max_distance: int = 2) -> dict[str, Any]:
        started = time.perf_counter()
        tokens = query_tokens(query)
        lookup = {entry["file_path"]: entry for entry in self.indexed}
        rows: dict[str, dict[str, Any]] = {}
        groups = (("pinned", pinned or [], 1200), ("always", always or [], 1100),
                  ("selected", selected or [], 1000), ("task", task_paths or [], 500))

        def add(path, factor, score, source="retrieved", **metadata):
            if path not in lookup or self.excluded(path):
                return
            row = rows.setdefault(path, {"file_path": path, "source": source, "score": 0, "factors": [],
                                         "file_hash": lookup[path].get("file_hash"), "matched_symbols": []})
            priorities = {"pinned": 6, "always": 5, "selected": 4, "task": 3, "retrieved": 2, "test": 1, "dependency": 0}
            if priorities.get(source, 0) > priorities.get(row["source"], 0):
                row["source"] = source
            if factor not in [entry["factor"] for entry in row["factors"]]:
                row["factors"].append({"factor": factor, "weight": score, **metadata})
                row["score"] += score

        normalized = {}
        for source, paths, priority in groups:
            for raw in paths:
                try:
                    path = self.indexer.root.joinpath(raw) if not str(raw).startswith("/") else None
                    # Caller validation is authoritative; this normalization does not read files.
                    value = str(raw).replace("\\", "/").rstrip("/")
                    if path is None:
                        value = str(raw).removeprefix(str(self.indexer.root) + "/")
                    normalized[value] = source
                    for candidate in lookup:
                        if value == "." or candidate == value or candidate.startswith(value + "/"):
                            add(candidate, "user_" + source, priority, source=source)
                except (TypeError, ValueError):
                    continue
        content_matches = self.indexer.search_content(" ".join(tokens), self.indexed, limit=16,
                                                       max_files=300, max_bytes=4 * 1024 * 1024) if tokens and self.indexed else []
        for entry in self.indexed:
            path = entry["file_path"]
            symbols = [str(symbol) for symbol in entry.get("symbols", [])]
            path_matches = [token for token in tokens if token in path.casefold()]
            symbol_matches = [symbol for symbol in symbols if any(token in symbol.casefold() for token in tokens)]
            if path_matches:
                add(path, "path_match", min(80, len(path_matches) * 16), matches=path_matches[:8])
            if symbol_matches:
                add(path, "symbol_overlap", min(120, len(symbol_matches) * 24), matches=symbol_matches[:8])
                rows[path]["matched_symbols"] = symbol_matches[:8]
            references = [name for name in entry.get("symbol_references", {}) if any(token == name.casefold() for token in tokens)]
            if references:
                add(path, "symbol_reference", min(40, len(references) * 10), matches=references[:8])
            if re.search(r"(?<![\w/])" + re.escape(path) + r"(?![\w/])", query):
                add(path, "explicit_path_reference", 1000, source="selected")
        for match in content_matches:
            add(match["file_path"], "lexical_content", 8, line=match["line"])
            if match["file_path"] in rows:
                rows[match["file_path"]]["line"] = match["line"]
        for change in (verified_edits or [])[-30:]:
            path = change.get("path", "")
            # Freshness alone cannot introduce an unrelated file. Require matching bytes.
            if path in rows and change.get("after_hash") == lookup[path].get("file_hash"):
                add(path, "recent_verified_edit", 12)
        for diagnostic in (diagnostics or [])[:100]:
            path = diagnostic.get("path", diagnostic.get("file_path", ""))
            if diagnostic.get("source") == "lsp" and (not diagnostic.get("file_hash") or
                    diagnostic["file_hash"] != lookup.get(path, {}).get("file_hash")):
                continue  # Old server versions do not establish a current compiler failure.
            if diagnostic.get("severity") in {1, "error", "failed"}:
                add(path, "compiler_diagnostic", 90, line=diagnostic.get("line"))
        graph = dependency_graph(self.indexed)
        seeds = sorted(rows, key=lambda path: (-rows[path]["score"], path))[:12]
        queue = deque((path, 0) for path in seeds)
        visited = set(seeds)
        while queue and len(visited) < 64:
            source, distance = queue.popleft()
            if distance >= max(0, min(3, max_distance)):
                continue
            for target in graph.get(source, [])[:12]:
                add(target, "dependency_distance", max(4, 32 // (distance + 1)), source="dependency",
                    distance=distance + 1, via=source)
                if target not in visited:
                    visited.add(target)
                    queue.append((target, distance + 1))
        for source in seeds:
            for target in matching_tests(source, list(lookup)):
                add(target, "matching_test", 45, source="test", via=source)
        ordered = sorted(rows.values(), key=lambda row: (-row["score"], row["file_path"]))
        reason_labels = {"user_pinned": "Kullanıcı sabitledi", "user_always": "Her isteğe dahil",
                         "user_selected": "Kullanıcı seçimi", "user_task": "Görev dosyası",
                         "path_match": "Dosya yolu eşleşmesi", "symbol_overlap": "Sembol eşleşmesi",
                         "symbol_reference": "Sembol referansı", "lexical_content": "Kaynak içeriği eşleşmesi",
                         "explicit_path_reference": "İstekte açık dosya yolu", "dependency_distance": "İçe aktarma bağımlılığı",
                         "matching_test": "İlgili test", "recent_verified_edit": "Hash ile doğrulanan güncel değişiklik",
                         "compiler_diagnostic": "Derleyici hatası"}
        for row in ordered:
            row["reason"] = "; ".join(reason_labels[factor["factor"]] for factor in row["factors"])
        report = {"stages": list(STAGES), "intent": classify_intent(query), "ranker": "deterministic_v1",
                  "semantic_index_available": False, "lsp": "optional_not_requested",
                  "candidate_count": len(ordered), "selected_count": min(limit, len(ordered)),
                  "candidate_limit": limit, "max_dependency_distance": max_distance,
                  "retrieval_latency_ms": round((time.perf_counter() - started) * 1000, 3),
                  "warnings": (["İndeks taraması kaynak sınırına ulaştı; bağlam eksik olabilir."] if self.indexer.last_refresh.get("limited") else []),
                  "candidates": ordered[:max(0, limit)],
                  "omitted_candidates": [{"path": row["file_path"], "reason": "Aday sayısı sınırı", "score": row["score"]}
                                         for row in ordered[max(0, limit):max(0, limit) + 100]]}
        return {"candidates": ordered[:max(0, limit)], "content_matches": content_matches, "report": report,
                "dependency_graph": graph}
