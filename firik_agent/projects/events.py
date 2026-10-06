"""Project Event Bus and Smart Trigger definitions."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


class ProjectEventType:
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_OPENED = "PROJECT_OPENED"
    FILE_CHANGED = "FILE_CHANGED"
    GIT_COMMIT_CREATED = "GIT_COMMIT_CREATED"
    TASK_CREATED = "TASK_CREATED"
    TASK_UPDATED = "TASK_UPDATED"
    TASK_COMPLETED = "TASK_COMPLETED"
    FEATURE_UPDATED = "FEATURE_UPDATED"
    SESSION_COMPLETED = "SESSION_COMPLETED"
    BUILD_FAILED = "BUILD_FAILED"
    TEST_FAILED = "TEST_FAILED"
    TODO_DETECTED = "TODO_DETECTED"
    CHECK_REQUESTED = "CHECK_REQUESTED"


class ProjectEventBus:
    """Thread-safe publish/subscribe event bus for project lifecycle events."""

    _instance: Optional[ProjectEventBus] = None
    _lock = threading.Lock()

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
        self._sub_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> ProjectEventBus:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def subscribe(self, event_type: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._sub_lock:
            self._subscribers.setdefault(event_type, []).append(callback)

    def unsubscribe(self, event_type: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._sub_lock:
            if event_type in self._subscribers:
                self._subscribers[event_type] = [cb for cb in self._subscribers[event_type] if cb != callback]

    def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        with self._sub_lock:
            callbacks = list(self._subscribers.get(event_type, []))
            all_callbacks = list(self._subscribers.get("*", []))

        for cb in callbacks + all_callbacks:
            try:
                cb(payload)
            except Exception as e:
                logger.exception("Error in ProjectEventBus subscriber for %s: %s", event_type, e)
