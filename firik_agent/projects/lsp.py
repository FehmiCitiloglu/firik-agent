"""Optional bounded stdio LSP adapter for an explicitly approved installed server.

The adapter never installs software, executes a shell, or accepts server-requested
edits. It is an intelligence adapter, not an authorization or sandbox boundary.
When unavailable, callers continue using the deterministic index and AST parser.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import url2pathname

from firik_agent.projects.indexer import read_project_text, safe_project_path, sanitize_secrets


class LSPUnavailable(RuntimeError):
    pass


def _signal_process(process, signum):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signum)
        else:
            process.send_signal(signum)
    except OSError:
        # macOS may return EPERM for a group that has just exited. A restricted
        # host may also allow the owned child PID but not a process-group signal.
        try:
            process.send_signal(signum)
        except ProcessLookupError:
            pass


def _process_limits():
    """Child-only OS limits, in addition to parent framing and timeout limits."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass


class LSPAdapter:
    def __init__(self, project_root: str | Path, command: list[str] | None = None, *,
                 approved: bool = False, excluded: Callable[[str], bool] | None = None,
                 timeout: float = 5, max_message_bytes: int = 1024 * 1024):
        self.root = Path(project_root).resolve()
        self.command = list(command or [])
        self.approved = approved is True
        self.excluded = excluded or (lambda path: False)
        self.timeout = min(30, max(.05, float(timeout)))
        self.max_message_bytes = max(1024, min(4 * 1024 * 1024, int(max_message_bytes)))
        self.process = None
        self._reader_task = None
        self._stderr_task = None
        self._pending: dict[int, asyncio.Future] = {}
        self._sequence = 0
        self._write_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._opened: set[str] = set()
        self._versions: dict[str, int] = {}
        self._document_hashes: dict[str, str] = {}
        self.status = "disabled" if not self.command or not self.approved else "stopped"
        self.failure: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {"status": self.status, "available": self.status == "ready", "fallback": "ast_and_search",
                "diagnostics": [dict(item) for items in list(self._diagnostics.values()) for item in items][:500],
                "failure": self.failure}

    def _path(self, value: str) -> Path:
        parsed = urlparse(value)
        if parsed.scheme and parsed.scheme != "file":
            raise ValueError("LSP yalnızca proje dosyalarını okuyabilir.")
        if parsed.scheme == "file" and parsed.netloc not in {"", "localhost"}:
            raise ValueError("Uzak LSP dosya adresi reddedildi.")
        path = safe_project_path(self.root, url2pathname(parsed.path) if parsed.scheme else value)
        relative = path.relative_to(self.root).as_posix()
        if self.excluded(relative):
            raise ValueError("LSP dosyası kullanıcı tarafından hariç tutuldu.")
        return path

    async def start(self) -> bool:
        async with self._start_lock:
            if self.status == "ready" and self.process and self.process.returncode is None:
                return True
            if not self.command or not self.approved:
                self.status = "disabled"
                return False
            executable = self.command[0]
            # Commands that may install or interpret shell text are never automatic.
            if Path(executable).name.casefold() in {"npx", "npm", "pnpm", "yarn", "uvx", "pip", "pip3", "curl", "wget", "sh", "bash", "zsh", "cmd", "powershell"}:
                self.status, self.failure = "unavailable", "Kurulum veya kabuk komutu LSP sunucusu olarak kullanılamaz."
                return False
            resolved = shutil.which(executable)
            if not resolved:
                self.status, self.failure = "unavailable", "Yapılandırılan LSP sunucusu kurulu değil."
                return False
            await self._stop_process()
            self._diagnostics.clear()
            self._opened.clear()
            self._versions.clear()
            self._document_hashes.clear()
            environment = {key: value for key, value in os.environ.items()
                           if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SystemRoot"}}
            self.status = "starting"
            try:
                safe_project_path(self.root, ".")
                self.process = await asyncio.create_subprocess_exec(
                    resolved, *self.command[1:], cwd=str(self.root), env=environment,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, limit=self.max_message_bytes + 8192,
                    **({"preexec_fn": _process_limits, "start_new_session": True} if os.name == "posix" else {}))
                self._reader_task = asyncio.create_task(self._read_loop())
                self._stderr_task = asyncio.create_task(self._drain_stderr())
                await self._request("initialize", {"processId": os.getpid(), "rootUri": self.root.as_uri(),
                    "capabilities": {"workspace": {"applyEdit": False}, "textDocument": {"publishDiagnostics": {}}},
                    "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name}]})
                await self._send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                self.status, self.failure = "ready", None
                return True
            except asyncio.CancelledError:
                await self._stop_process()
                self.status = "stopped"
                raise
            except (OSError, ValueError, LSPUnavailable, asyncio.TimeoutError) as exc:
                self.status, self.failure = "unavailable", sanitize_secrets(str(exc))[:300]
                await self._stop_process()
                return False

    async def _send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(data) > self.max_message_bytes:
            raise LSPUnavailable("LSP iletisi kaynak sınırını aşıyor.")
        async with self._write_lock:
            if not self.process or self.process.returncode is not None or not self.process.stdin:
                raise LSPUnavailable("LSP sunucusu çalışmıyor.")
            try:
                self.process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
                await asyncio.wait_for(self.process.stdin.drain(), timeout=self.timeout)
            except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError) as exc:
                raise LSPUnavailable("LSP sunucusu iletişimi kesildi.") from exc

    async def _request(self, method: str, params: Any) -> Any:
        self._sequence += 1
        identifier = self._sequence
        future = asyncio.get_running_loop().create_future()
        self._pending[identifier] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
            response = await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout)
            if "error" in response:
                raise LSPUnavailable("LSP isteği sunucu tarafından reddedildi: " + sanitize_secrets(str(response["error"]))[:200])
            return response.get("result")
        except (asyncio.TimeoutError, asyncio.CancelledError):
            try:
                await self._send({"jsonrpc": "2.0", "method": "$/cancelRequest", "params": {"id": identifier}})
            except (LSPUnavailable, OSError):
                pass
            raise
        finally:
            self._pending.pop(identifier, None)
            if not future.done():
                future.cancel()

    async def _read_loop(self):
        try:
            while self.process and self.process.stdout:
                header = await self.process.stdout.readuntil(b"\r\n\r\n")
                if len(header) > 8192:
                    raise LSPUnavailable("LSP başlığı kaynak sınırını aşıyor.")
                fields = dict(line.split(b":", 1) for line in header.strip().split(b"\r\n") if b":" in line)
                length = int(next((value for key, value in fields.items() if key.lower() == b"content-length"), b"0").strip())
                if not 0 < length <= self.max_message_bytes:
                    raise LSPUnavailable("LSP yanıtı kaynak sınırını aşıyor.")
                payload = json.loads(await self.process.stdout.readexactly(length))
                if not isinstance(payload, dict):
                    raise LSPUnavailable("Geçersiz LSP JSON-RPC yanıtı.")
                identifier = payload.get("id")
                if "method" in payload:
                    if identifier is not None:
                        # Server initiated edits/config execution are never performed.
                        await self._send({"jsonrpc": "2.0", "id": identifier,
                                          "error": {"code": -32601, "message": "Client operation not supported"}})
                    elif payload["method"] == "textDocument/publishDiagnostics":
                        self._record_diagnostics(payload.get("params", {}))
                elif identifier in self._pending and not self._pending[identifier].done():
                    self._pending[identifier].set_result(payload)
        except asyncio.CancelledError:
            return
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError, ValueError, TypeError, OSError, LSPUnavailable) as exc:
            self.status, self.failure = "crashed", "LSP sunucusu durdu veya geçersiz yanıt verdi: " + sanitize_secrets(type(exc).__name__)
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(LSPUnavailable(self.failure))
            if self.process and self.process.returncode is None:
                _signal_process(self.process, signal.SIGTERM)

    def _record_diagnostics(self, params):
        if not isinstance(params, dict) or not isinstance(params.get("diagnostics", []), list):
            return
        try:
            path = self._path(params.get("uri", ""))
            relative = path.relative_to(self.root).as_posix()
        except (ValueError, OSError, TypeError):
            return
        if len(self._diagnostics) >= 100 and relative not in self._diagnostics:
            return
        rows = []
        for item in params.get("diagnostics", [])[:50]:
            if not isinstance(item, dict):
                continue
            location = item.get("range", {})
            start = location.get("start", {}) if isinstance(location, dict) else {}
            line = start.get("line", 0) if isinstance(start, dict) else 0
            line = line if isinstance(line, int) and not isinstance(line, bool) and line >= 0 else 0
            rows.append({"path": relative, "severity": item.get("severity"),
                         "message": sanitize_secrets(str(item.get("message", "")))[:1000],
                         "line": line + 1,
                         "source": "lsp", "version": params.get("version"),
                         "file_hash": self._document_hashes.get(path.as_uri()) if
                             params.get("version", self._versions.get(path.as_uri())) == self._versions.get(path.as_uri()) else None})
        self._diagnostics[relative] = rows

    async def _drain_stderr(self):
        # Discard output continuously; diagnostics arrive through bounded JSON-RPC.
        try:
            while self.process and self.process.stderr and await self.process.stderr.read(8192):
                pass
        except asyncio.CancelledError:
            return

    async def open_document(self, path: str, language_id: str | None = None) -> str:
        target = self._path(path)
        content = read_project_text(self.root, target, max_bytes=256 * 1024, sanitize=False)
        if target.stat().st_size > 256 * 1024:
            raise LSPUnavailable("LSP belgesi kaynak sınırını aşıyor.")
        if not await self.start():
            raise LSPUnavailable(self.failure or "LSP etkin değil; AST ve arama kullanılabilir.")
        uri = target.as_uri()
        self._document_hashes[uri] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._versions[uri] = self._versions.get(uri, 0) + 1
        content = sanitize_secrets(content)
        language_id = language_id or {".py": "python", ".ts": "typescript", ".tsx": "typescriptreact", ".js": "javascript", ".jsx": "javascriptreact"}.get(target.suffix, "plaintext")
        if uri in self._opened:
            await self._send({"jsonrpc": "2.0", "method": "textDocument/didChange", "params": {
                "textDocument": {"uri": uri, "version": self._versions[uri]}, "contentChanges": [{"text": content}]}})
        else:
            await self._send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
                "textDocument": {"uri": uri, "languageId": language_id, "version": self._versions[uri], "text": content}}})
            self._opened.add(uri)
        return uri

    async def locations(self, method: str, path: str, line: int, character: int = 0) -> list[dict[str, Any]]:
        if method not in {"textDocument/definition", "textDocument/references"}:
            raise ValueError("Desteklenmeyen LSP sorgusu.")
        uri = await self.open_document(path)
        params = {"textDocument": {"uri": uri}, "position": {"line": max(0, int(line)), "character": max(0, int(character))}}
        if method.endswith("references"):
            params["context"] = {"includeDeclaration": True}
        result = await self._request(method, params)
        rows = []
        for item in (result if isinstance(result, list) else [result])[:100]:
            if not isinstance(item, dict):
                continue
            try:
                target = self._path(item.get("uri", item.get("targetUri", "")))
            except (ValueError, TypeError, OSError):
                continue
            rows.append({"path": target.relative_to(self.root).as_posix(),
                         "range": item.get("range", item.get("targetSelectionRange", {})), "source": "lsp"})
        return rows

    async def definitions(self, path: str, line: int, character: int = 0):
        return await self.locations("textDocument/definition", path, line, character)

    async def references(self, path: str, line: int, character: int = 0):
        return await self.locations("textDocument/references", path, line, character)

    async def _stop_process(self):
        process = self.process
        for task in (self._reader_task, self._stderr_task):
            if task and task is not asyncio.current_task():
                task.cancel()
        await asyncio.gather(*(task for task in (self._reader_task, self._stderr_task)
                               if task and task is not asyncio.current_task()), return_exceptions=True)
        self._reader_task = self._stderr_task = None
        if process and process.returncode is None:
            try:
                _signal_process(process, signal.SIGTERM)
                await asyncio.wait_for(process.wait(), timeout=1)
            except (ProcessLookupError, asyncio.TimeoutError):
                if process.returncode is None:
                    _signal_process(process, signal.SIGKILL)
                    await process.wait()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(LSPUnavailable("LSP oturumu kapatıldı."))
        self._pending.clear()
        self.process = None

    async def close(self):
        if self.status == "ready":
            try:
                await self._request("shutdown", None)
                await self._send({"jsonrpc": "2.0", "method": "exit"})
            except (LSPUnavailable, asyncio.TimeoutError, OSError):
                pass
        await self._stop_process()
        self.status = "stopped"
        self._opened.clear()
        self._versions.clear()
        self._document_hashes.clear()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()
