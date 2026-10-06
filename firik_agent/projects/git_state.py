"""One live Git working-tree snapshot shared by project and cockpit views."""
from __future__ import annotations

import threading
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from firik_agent.projects.git_service import GitService


class GitStateStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._listeners: dict[str, tuple[str, Callable]] = {}

    @staticmethod
    def _key(root):
        return str(Path(root).resolve())

    def get(self, root):
        with self._lock:
            return deepcopy(self._states.get(self._key(root)))

    def subscribe(self, root, callback):
        identifier = uuid4().hex
        with self._lock:
            self._listeners[identifier] = (self._key(root), callback)
        return identifier

    def unsubscribe(self, identifier):
        with self._lock:
            self._listeners.pop(identifier, None)

    def refresh(self, root):
        key = self._key(root)
        # Serialize collection and publication: a slow old observation cannot
        # replace a newer one or notify subscribers out of order.
        with self._lock:
            state = GitService.get_status(key)
            state.update(repository=key, is_repository=not bool(state.get("error")),
                         changed_files=len(state.get("files", [])), dirty=not state.get("clean", False),
                         revision=self._states.get(key, {}).get("revision", 0) + 1)
            self._states[key] = state
            listeners = [callback for path, callback in self._listeners.values() if path == key]
            for callback in listeners:
                callback(deepcopy(state))
            return deepcopy(state)
