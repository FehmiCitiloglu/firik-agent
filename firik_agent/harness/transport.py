"""Bounded JSONL subprocess transport for documented agent protocols.

The caller supplies an argv list, private cwd and an explicitly built environment.
This transport never invokes a shell, inherits stdin or copies host credentials.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.details = code, details


class JsonlProcess:
    def __init__(self, *, max_frames: int = 256, max_frame_bytes: int = 1_048_576,
                 max_total_bytes: int = 16_777_216, shutdown_timeout: float = 1.0):
        self.process: asyncio.subprocess.Process | None = None
        self.frames: asyncio.Queue = asyncio.Queue(maxsize=max_frames)
        self.max_frame_bytes, self.max_total_bytes = max_frame_bytes, max_total_bytes
        self.shutdown_timeout = shutdown_timeout
        self.stderr = bytearray()
        self.stderr_truncated = False
        self._readers: list[asyncio.Task] = []
        self._closed = False
        self._cleanup_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self.failure: TransportError | None = None

    async def start(self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str]) -> None:
        if self.process is not None:
            raise TransportError("already_started", "Transport already started")
        if not argv or any(not isinstance(value, str) or "\x00" in value for value in argv):
            raise TransportError("invalid_argv", "Invalid backend executable arguments")
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        try:
            self.process = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=dict(env), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=self.max_frame_bytes + 1, **options)
        except (OSError, ValueError) as exc:
            raise TransportError("startup_failed", str(exc), exception_type=type(exc).__name__) from exc
        self._readers = [asyncio.create_task(self._stdout()), asyncio.create_task(self._stderr())]

    def _signal_failure(self, error: TransportError) -> None:
        if self.failure is None:
            self.failure = error
        # Wake consumers even if the producer exceeded the bounded queue.
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(None)

    async def _stdout(self) -> None:
        total = 0
        try:
            assert self.process and self.process.stdout
            while True:
                try:
                    line = await self.process.stdout.readuntil(b"\n")
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        raise TransportError("partial_frame", "Backend exited with an incomplete JSONL frame") from exc
                    break
                except asyncio.LimitOverrunError as exc:
                    raise TransportError("frame_too_large", "Backend JSONL frame exceeds limit") from exc
                total += len(line)
                if len(line) > self.max_frame_bytes or total > self.max_total_bytes:
                    raise TransportError("output_limit", "Backend protocol output exceeds limit")
                try:
                    value = json.loads(line.rstrip(b"\r\n"))
                except (UnicodeError, ValueError) as exc:
                    raise TransportError("malformed_frame", "Backend emitted malformed JSON", diagnostic=str(exc)) from exc
                if not isinstance(value, dict):
                    raise TransportError("malformed_frame", "Backend protocol records must be objects")
                if self.frames.full():
                    raise TransportError("event_overflow", "Backend event consumer exceeded its bounded buffer")
                self.frames.put_nowait(value)
            self._signal_failure(TransportError("disconnected", "Backend protocol disconnected"))
        except TransportError as exc:
            self._signal_failure(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._signal_failure(TransportError("transport_failure", str(exc)))

    async def _stderr(self) -> None:
        assert self.process and self.process.stderr
        while chunk := await self.process.stderr.read(4096):
            remaining = max(0, 16384 - len(self.stderr))
            self.stderr.extend(chunk[:remaining])
            self.stderr_truncated |= len(chunk) > remaining

    async def send(self, value: Mapping[str, Any]) -> None:
        encoded = (json.dumps(dict(value), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > self.max_frame_bytes:
            raise TransportError("request_too_large", "Backend command exceeds frame limit")
        async with self._write_lock:
            if self._closed or self._cleanup_task is not None or not self.process or self.process.returncode is not None or not self.process.stdin:
                raise TransportError("disconnected", "Backend process is not writable")
            try:
                self.process.stdin.write(encoded)
                await asyncio.wait_for(self.process.stdin.drain(), timeout=5)
            except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError) as exc:
                raise TransportError("disconnected", "Backend command channel disconnected") from exc

    async def read(self, timeout: float | None = None) -> dict[str, Any]:
        if self.failure is not None and self.frames.empty():
            raise self.failure
        frame = await asyncio.wait_for(self.frames.get(), timeout=timeout) if timeout is not None else await self.frames.get()
        if frame is None:
            raise self.failure or TransportError("disconnected", "Backend protocol closed")
        return frame

    async def close(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._close_impl())
        # Every caller observes the same disposal; cancelling one waiter cannot
        # abort cleanup or make another caller return before descriptors close.
        await asyncio.shield(self._cleanup_task)

    async def _close_impl(self) -> None:
        process = self.process
        if process:
            if process.stdin:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.shutdown_timeout)
            except asyncio.TimeoutError:
                if os.name == "nt":
                    # Windows process-tree containment requires Job Objects and is
                    # deliberately unsupported by the production external adapter.
                    if process.returncode is None:
                        process.kill()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await asyncio.wait_for(process.wait(), timeout=5)
            finally:
                # A leader may exit while a descendant still holds its pipes.
                if os.name != "nt":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        for reader in self._readers:
            if not reader.done():
                reader.cancel()
        if self._readers:
            await asyncio.gather(*self._readers, return_exceptions=True)
        if process:
            # A malformed/oversized frame can stop the protocol reader before
            # EOF. Drain the now-terminated child's pipes so asyncio releases
            # their descriptors before the owning event loop closes.
            async def drain(stream):
                if stream:
                    while await stream.read(65536):
                        pass
            await asyncio.wait_for(asyncio.gather(drain(process.stdout), drain(process.stderr)), timeout=2)
            if process.stdin:
                try:
                    await asyncio.wait_for(process.stdin.wait_closed(), timeout=1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        self._closed = True

    def inspect(self) -> dict[str, Any]:
        return {"pid": self.process.pid if self.process else None,
                "returncode": self.process.returncode if self.process else None,
                "closed": self._closed, "stderr_truncated": self.stderr_truncated}
