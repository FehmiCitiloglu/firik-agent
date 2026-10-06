"""Codebase indexing, symbol parsing, TODO extractor, and secret sanitizer."""
from __future__ import annotations

import ast
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# Files and directories to always exclude from indexing and LLM context
IGNORED_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "dist", "build", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".tox", ".idea", ".vscode",
    "target", "vendor", "bin", "obj", ".next", ".nuxt", "coverage",
}

IGNORED_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".mp3", ".wav", ".mp4", ".mov",
    ".ttf", ".woff", ".woff2", ".eot", ".db", ".sqlite", ".sqlite3",
}

# Secret detection patterns
SECRET_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-./+=]+"),
    re.compile(r"(?i)(?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s/@:]+:[^\s/@]+@"),
    re.compile(r"(?i)\b(?:[\w-]*[_-])?(?:api[_-]?key|secret(?:[_-]?(?:access[_-]?)?key)?|access[_-]?key|private[_-]?key|(?:access|refresh|auth)[_-]?token|token|password|passwd|authorization|credentials?)[\"']?\s*[:=]\s*(?:[\"'][^\"'\r\n]+[\"']|[^\s,;\r\n]+)"),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z ]+ )?PRIVATE KEY-----|\Z)"),
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)ghp_[A-Za-z0-9]{36}"),
    re.compile(r"(?i)glpat-[A-Za-z0-9_\-]{20}"),
    re.compile(r"(?i)xox[baprs]-[A-Za-z0-9]{10,48}"),
]

SENSITIVE_FILENAMES = {
    ".env", ".env.local", ".env.production", ".env.development", ".env.staging",
    "id_rsa", "id_dsa", "id_ed25519", "credentials.json", "service-account.json",
    "secret.key", "secrets.yaml", "secrets.json",
}
SENSITIVE_DIRECTORIES = {".git", ".ssh", ".aws", ".azure", ".gcloud", ".credentials", ".codex", ".agents"}


def is_sensitive_file(file_path: str | Path, *, project_root: str | Path | None = None) -> bool:
    """Checks if a file should NEVER be read or sent to an LLM."""
    p = Path(file_path)
    if project_root is not None:
        root = Path(project_root).resolve()
        candidate = p if p.is_absolute() else root / p
        try:
            p = candidate.resolve().relative_to(root)
        except (ValueError, OSError):
            return True
    if any(part.lower() in SENSITIVE_DIRECTORIES for part in p.parts):
        return True
    name = p.name.lower()
    if name in SENSITIVE_FILENAMES or name.startswith(".env"):
        return True
    if p.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".pkcs12"}:
        return True
    return False


def safe_project_path(project_root: str | Path, file_path: str | Path) -> Path:
    """Resolve a project path while rejecting escapes and symbolic links."""
    root = Path(project_root).resolve()
    if root.parent == root:
        raise ValueError("Dosya sistemi kökü proje klasörü olarak kullanılamaz.")
    # A managed checkout may live below .codex/.agents, but a credentials
    # directory or the protected container itself cannot become a project root.
    if root.name.lower() in SENSITIVE_DIRECTORIES or is_sensitive_file(root.name):
        raise ValueError("Hassas klasör proje kökü olarak kullanılamaz.")
    if any(part.lower() in SENSITIVE_DIRECTORIES - {".codex", ".agents"} for part in root.parts):
        raise ValueError("Hassas klasör proje kökü olarak kullanılamaz.")
    candidate = Path(file_path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        # macOS /var and /tmp are aliases of /private paths. Resolve the root
        # alias only, then inspect every project-local component for symlinks.
        alias = next((parent for parent in candidate.parents if parent.resolve() == root), None)
        if alias is None:
            raise ValueError("Dosya proje klasörünün dışında.") from exc
        relative = candidate.relative_to(alias)
        candidate = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Sembolik bağlantılara proje bağlamından erişilemez.")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Dosya proje klasörünün dışında.")
    if is_sensitive_file(relative) or is_sensitive_file(resolved, project_root=root):
        raise ValueError("Hassas dosyalar proje bağlamına alınamaz.")
    return resolved


def read_project_text(project_root: str | Path, file_path: str | Path,
                      max_bytes: int = 64 * 1024, *, sanitize: bool = True) -> str:
    """Bounded text read shared by indexing, search, and prompt preparation."""
    path = safe_project_path(project_root, file_path)
    if not path.is_file() or path.suffix.lower() in IGNORED_EXTENSIONS:
        raise ValueError("Dosya okunabilir bir kaynak metni değil.")
    descriptors = []
    try:
        parent = None
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            parent = os.open(Path(project_root).resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(parent)
            for part in path.relative_to(Path(project_root).resolve()).parts[:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(parent)
        descriptor = os.open(path.name if parent is not None else path,
                             os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise ValueError("Yalnızca bağımsız normal proje dosyaları okunabilir.")
            data = stream.read(max(0, max_bytes) + 1)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if b"\x00" in data:
        raise ValueError("İkili dosyalar proje bağlamına alınamaz.")
    text = data[:max(0, max_bytes)].decode("utf-8", errors="replace")
    return sanitize_secrets(text) if sanitize else text


def sanitize_git_diff(text: str) -> str:
    """Drop entire sensitive-file diff sections, then mask inline credentials."""
    sections = re.split(r"(?m)(?=^diff --git )", text)
    safe = []
    for section in sections:
        paths = re.findall(r"(?m)^(?:---|\+\+\+) (.+)$", section)
        first = section.splitlines()[0] if section.splitlines() else ""
        paths += re.findall(r"[ab]/([^\s]+)", first)
        unsafe = False
        for raw in paths:
            path = raw.strip().strip('"').split("\t", 1)[0]
            if path.startswith(("a/", "b/")):
                path = path[2:]
            if is_sensitive_file(path):
                unsafe = True
                break
        if not unsafe:
            safe.append(section)
    return sanitize_secrets("".join(safe))


def sanitize_secrets(text: str) -> str:
    """Masks secrets and sensitive credentials in content before context inclusion."""
    sanitized = text
    for pat in SECRET_PATTERNS:
        sanitized = pat.sub(lambda match: "[SECRET_FILTERED]" + "\n" * match.group().count("\n"), sanitized)
    return sanitized


class CodebaseIndexer:
    """Scans and indexes files, symbols, and TODOs from a project workspace."""

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()

    def scan_files(self, max_files: int = 1500) -> List[Path]:
        """Collects relevant source and text files in the project."""
        found: List[Path] = []
        if not self.root.exists() or max_files <= 0:
            return found
        try:
            safe_project_path(self.root, ".")
        except (ValueError, OSError):
            return found

        for root, dirs, files in os.walk(self.root):
            # Prune ignored directories in-place
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")
                             and not (Path(root) / d).is_symlink())

            for f in sorted(files):
                p = Path(root) / f
                if p.is_symlink() or p.suffix.lower() in IGNORED_EXTENSIONS or is_sensitive_file(p, project_root=self.root):
                    continue
                try:
                    # Skip files larger than 1MB
                    if p.stat().st_size > 1024 * 1024:
                        continue
                except OSError:
                    continue
                found.append(p)
                if len(found) >= max_files:
                    return found
        return found

    def index_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Extracts symbols, classes, functions, imports, and TODOs from a single file."""
        try:
            file_path = safe_project_path(self.root, file_path)
            if file_path.stat().st_size > 1024 * 1024:
                return None
            rel_path = file_path.relative_to(self.root).as_posix()
            content = read_project_text(self.root, file_path, max_bytes=1024 * 1024, sanitize=False)
        except (ValueError, OSError):
            return None

        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        ext = file_path.suffix.lower()

        classes: List[str] = []
        functions: List[str] = []
        imports: List[str] = []
        todos: List[Dict[str, Any]] = []

        # 1. Extract TODO / FIXME comments across any text file
        todo_regex = re.compile(r"(?i)\b(TODO|FIXME|HACK|XXX|BUG)\b:?\s*(.*)")
        for idx, line in enumerate(content.splitlines(), start=1):
            m = todo_regex.search(line)
            if m:
                tag = m.group(1).upper()
                msg = m.group(2).strip()
                todos.append({
                    "type": tag,
                    "line": idx,
                    "text": sanitize_secrets(msg)[:150],
                    "file": rel_path,
                })

        # 2. Extract AST symbols for Python
        if ext == ".py":
            try:
                tree = ast.parse(content, filename=rel_path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        classes.append(node.name)
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        functions.append(node.name)
                    elif isinstance(node, ast.Import):
                        for n in node.names:
                            imports.append(n.name)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.append(node.module)
            except SyntaxError:
                pass

        # 3. Regex fallback for JS/TS/Go/Rust/Java
        elif ext in {".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cpp", ".cs"}:
            class_pat = re.compile(r"\bclass\s+([A-Za-z0-9_]+)")
            fn_pat = re.compile(r"\b(?:function|fn|func|def)\s+([A-Za-z0-9_]+)")
            import_pat = re.compile(r"(?:import|from)\s+['\"]([^'\"]+)['\"]")

            for m in class_pat.finditer(content):
                classes.append(m.group(1))
            for m in fn_pat.finditer(content):
                functions.append(m.group(1))
            for m in import_pat.finditer(content):
                imports.append(m.group(1))

        symbols = list(dict.fromkeys(classes + functions))

        return {
            "file_path": rel_path,
            "file_type": ext,
            "symbols": symbols,
            "classes": classes,
            "functions": functions,
            "imports": list(dict.fromkeys(imports)),
            "todos": todos,
            "file_hash": file_hash,
        }

    def search_codebase(
        self,
        query: str,
        indexed_data: List[Dict[str, Any]],
        limit: int = 15,
    ) -> List[Dict[str, Any]]:
        """Matches a search query across indexed symbols, paths, and content."""
        q_lower = query.lower().strip()
        tokens = list(dict.fromkeys(t for t in re.split(r"\W+", q_lower) if t))[:64]
        results = []

        for entry in indexed_data:
            if is_sensitive_file(entry["file_path"]):
                continue
            path = entry["file_path"].lower()
            symbols = [s.lower() for s in entry.get("symbols", [])]
            imports = [i.lower() for i in entry.get("imports", [])]

            score = 0
            matched_symbols = []

            for token in tokens:
                if token in path:
                    score += 5
                for s in symbols:
                    if token in s:
                        score += 8
                        matched_symbols.append(s)
                for i in imports:
                    if token in i:
                        score += 3

            if score > 0:
                try:
                    safe_project_path(self.root, entry["file_path"])
                except (ValueError, OSError):
                    continue
                results.append({
                    "file_path": entry["file_path"],
                    "score": score,
                    "matched_symbols": list(set(matched_symbols))[:5],
                    "file_type": entry.get("file_type", ""),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def search_content(self, query: str, indexed_data: Optional[List[Dict[str, Any]]] = None,
                       limit: int = 20, max_files: int = 300,
                       max_bytes: int = 4 * 1024 * 1024) -> List[Dict[str, Any]]:
        """Bounded lexical search with line citations; no full-project prompt loading."""
        tokens = list(dict.fromkeys(word.casefold() for word in re.findall(r"\w+", query) if len(word) > 2))[:64]
        if not tokens or limit <= 0:
            return []
        paths = [entry.get("file_path", "") for entry in (indexed_data or [])]
        if not paths:
            paths = [path.relative_to(self.root).as_posix() for path in self.scan_files(max_files=max_files)]
        found: List[Dict[str, Any]] = []
        used_bytes = 0
        for relative in dict.fromkeys(paths[:max_files]):
            remaining = min(64 * 1024, max_bytes - used_bytes)
            if remaining <= 0:
                break
            try:
                content = read_project_text(self.root, relative, max_bytes=remaining)
            except (ValueError, OSError):
                continue
            used_bytes += len(content.encode("utf-8"))
            for number, line in enumerate(content.splitlines(), 1):
                if any(word in line.casefold() for word in tokens):
                    found.append({"file_path": relative, "path": relative, "line": number,
                                  "preview": line[:240], "score": 1, "matched_symbols": []})
                    break
            if len(found) >= limit:
                break
        return found
