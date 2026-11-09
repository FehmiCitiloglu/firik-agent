"""Prevent independent application processes from writing the same data store."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading

_guard = threading.RLock()
_held: dict[Path, tuple[object, int]] = {}


class ApplicationDataLock:
    def __init__(self, directory: Path):
        self.path = directory.resolve() / ".firik-application.lock"
        self._acquired = False

    def acquire(self) -> None:
        with _guard:
            if self.path in _held:
                handle, count = _held[self.path]
                _held[self.path] = handle, count + 1
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        if not self.path.stat().st_size:
                            handle.write(b"0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    handle.close()
                    raise RuntimeError("Bu veri dizini başka bir Firik uygulamasında açık.") from None
                _held[self.path] = handle, 1
            self._acquired = True

    def release(self) -> None:
        with _guard:
            if not self._acquired:
                return
            self._acquired = False
            handle, count = _held[self.path]
            if count > 1:
                _held[self.path] = handle, count - 1
            else:
                if sys.platform == "win32":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_UN)
                handle.close()
                del _held[self.path]
