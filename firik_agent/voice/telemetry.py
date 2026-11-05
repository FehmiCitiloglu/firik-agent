"""Voice timing diagnostics with no request, user, or device identifiers."""

import logging
import threading
import time

from firik_agent.core.events import AgentEventType

logger = logging.getLogger(__name__)


def log_voice_timing(stage: str, status: str, started_at: float, *, finished_at: float | None = None) -> None:
    if logger.isEnabledFor(logging.INFO):
        finished = time.monotonic() if finished_at is None else finished_at
        logger.info("voice stage=%s status=%s duration_ms=%.1f", stage, status,
                    max(0.0, finished - started_at) * 1000)


class VoiceAgentTiming:
    """Measure a voice-origin submission without retaining any event content."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._first_text = self._failed = self._finished = False

    def observe(self, event) -> None:
        with self._lock:
            if self._finished:
                return
            if event.type == AgentEventType.ERROR:
                self._failed = True
            elif event.type == AgentEventType.TEXT_DELTA and event.content and not self._first_text:
                self._first_text = True
                log_voice_timing("agent_first_token", "ok", self.started)

    def finish(self, status: str) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            if status == "ok" and self._failed:
                status = "failed"
            log_voice_timing("agent", status, self.started)
