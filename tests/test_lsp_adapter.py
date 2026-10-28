"""Real local stdio processes exercise LSP framing, cancellation and failure."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from firik_agent.projects.lsp import LSPAdapter, LSPUnavailable


SERVER = r'''
import json, os, sys

def send(value):
    payload = json.dumps(value).encode()
    sys.stdout.buffer.write(("Content-Length: %d\r\n\r\n" % len(payload)).encode() + payload)
    sys.stdout.buffer.flush()

def read():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise EOFError
        if line == b"\r\n":
            break
        key, value = line.decode().split(":", 1)
        headers[key.lower()] = value.strip()
    return json.loads(sys.stdin.buffer.read(int(headers["content-length"])))

opened = None
while True:
    try:
        payload = read()
    except EOFError:
        break
    method = payload.get("method")
    identifier = payload.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": identifier, "result": {"capabilities": {}}})
    elif method in ("textDocument/didOpen", "textDocument/didChange"):
        opened = payload["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {
            "uri": opened, "version": 1, "diagnostics": [{"severity": 1, "message": "password='hidden-secret'", "range": {"start": {"line": 2, "character": 0}}}]}})
    elif method == "textDocument/definition":
        send({"jsonrpc": "2.0", "id": identifier, "result": [
            {"uri": opened, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 2}}},
            {"uri": "file:///etc/passwd", "range": {}},
            {"uri": opened.rsplit("/", 1)[0] + "/private.py", "range": {}}]})
    elif method == "textDocument/references":
        # Never answer; parent timeout/cancellation must retain a healthy process.
        continue
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": identifier, "result": None})
    elif method == "exit":
        break
'''


def server(tmp_path, timeout=.5, source=SERVER):
    script = tmp_path / "lsp_server.py"
    script.write_text(source, encoding="utf-8")
    (tmp_path / "main.py").write_text("class Example: pass\n", encoding="utf-8")
    (tmp_path / "private.py").write_text("private = 1\n", encoding="utf-8")
    return LSPAdapter(tmp_path, [sys.executable, "-u", str(script)], approved=True,
                      excluded=lambda path: path == "private.py", timeout=timeout)


@pytest.mark.asyncio
async def test_missing_unapproved_and_installer_servers_are_optional(tmp_path):
    for adapter, expected in ((LSPAdapter(tmp_path, ["definitely_missing_firik_lsp"], approved=True), "unavailable"),
                              (LSPAdapter(tmp_path, [sys.executable]), "disabled"),
                              (LSPAdapter(tmp_path, ["npx", "some-server"], approved=True), "unavailable")):
        assert await adapter.start() is False
        assert adapter.snapshot()["status"] == expected
        assert adapter.snapshot()["fallback"] == "ast_and_search"
        assert adapter.process is None


@pytest.mark.asyncio
async def test_real_stdio_initialize_diagnostics_definitions_and_shutdown(tmp_path):
    adapter = server(tmp_path)
    try:
        definitions = await adapter.definitions("main.py", 0)
        assert [row["path"] for row in definitions] == ["main.py"]
        assert adapter.status == "ready"
        diagnostics = adapter.snapshot()["diagnostics"]
        assert diagnostics and diagnostics[0]["line"] == 3
        assert "hidden-secret" not in json.dumps(diagnostics)
        with pytest.raises(ValueError, match="hariç"):
            await adapter.open_document("private.py")
        with pytest.raises(ValueError):
            await adapter.open_document("../external.py")
        with pytest.raises(ValueError):
            await adapter.open_document(".env")
        process = adapter.process
    finally:
        await adapter.close()
    assert process.returncode is not None and adapter.status == "stopped"


@pytest.mark.asyncio
async def test_timeout_and_cancellation_remove_pending_requests(tmp_path):
    adapter = server(tmp_path, timeout=.1)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await adapter.references("main.py", 0)
        assert not adapter._pending
        task = asyncio.create_task(adapter.references("main.py", 0))
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not adapter._pending
        assert await adapter.definitions("main.py", 0)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_crashed_server_can_restart_without_reusing_pending_state(tmp_path):
    adapter = server(tmp_path)
    try:
        assert await adapter.start()
        original = adapter.process
        original.kill()
        await original.wait()
        for _ in range(20):
            if adapter.status == "crashed":
                break
            await asyncio.sleep(.01)
        assert adapter.status == "crashed"
        assert await adapter.start()
        assert adapter.process.pid != original.pid
        assert await adapter.definitions("main.py", 0)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_oversized_and_malformed_rpc_frames_fail_closed(tmp_path):
    source = 'import sys, time\nsys.stdout.buffer.write(b"Content-Length: 99999999\\r\\n\\r\\n"); sys.stdout.buffer.flush(); time.sleep(10)\n'
    adapter = server(tmp_path, source=source)
    try:
        assert await adapter.start() is False
        assert adapter.status == "unavailable" and adapter.process is None
    finally:
        await adapter.close()
