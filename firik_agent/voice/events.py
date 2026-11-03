"""Coalesce high-rate worker telemetry before entering the Tk dispatch queue."""
from __future__ import annotations

import threading


class LatestEvent:
    """At most one dispatch job; the latest level replaces older measurements."""

    def __init__(self, dispatch, callback):
        self._dispatch, self._callback = dispatch, callback
        self._lock = threading.Lock()
        self._pending = False
        self._value = None
        self._closed = False

    def publish(self, value):
        with self._lock:
            if self._closed:
                return
            self._value = value
            if self._pending:
                return
            self._pending = True
        self._dispatch(self._deliver)

    def _deliver(self):
        with self._lock:
            value = self._value
            self._pending = False
            callback = None if self._closed else self._callback
        if callback is not None:
            callback(value)

    def close(self):
        with self._lock:
            self._closed = True
            self._callback = self._dispatch = lambda *args: None
            self._value = None
