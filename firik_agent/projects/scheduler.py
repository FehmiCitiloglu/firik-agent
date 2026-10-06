"""Application-level scheduler and smart event trigger orchestrator for Projects."""
from __future__ import annotations

import datetime
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from firik_agent.projects.events import ProjectEventBus, ProjectEventType
from firik_agent.projects.pm_service import AIProjectManager

logger = logging.getLogger(__name__)


INTERVAL_SECONDS: Dict[str, Optional[int]] = {
    "Disabled": None,
    "Every 15 minutes": 15 * 60,
    "Every 30 minutes": 30 * 60,
    "Hourly": 60 * 60,
    "Every 3 hours": 3 * 60 * 60,
    "Daily": 24 * 60 * 60,
    "Manual": None,
}


class ProjectScheduler:
    """Manages periodic and event-driven triggers for Project Manager checks."""

    def __init__(self, pm_service: AIProjectManager, event_bus: Optional[ProjectEventBus] = None):
        self.pm = pm_service
        self.bus = event_bus or ProjectEventBus.get_instance()
        self._running = False
        self._timer_thread: Optional[threading.Thread] = None
        self._check_timestamps: Dict[str, list[float]] = {}
        self._active_project_id: Optional[str] = None
        self._interval_setting: str = "Every 30 minutes"
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        self._setup_event_listeners()

    def set_active_project(self, project_id: Optional[str], interval_setting: str = "Every 30 minutes") -> None:
        with self._lock:
            self._active_project_id = project_id
            self._interval_setting = interval_setting

    def _setup_event_listeners(self) -> None:
        """Hooks event bus events to trigger project checks when configured."""
        self.bus.subscribe(ProjectEventType.TASK_COMPLETED, self._on_task_completed)
        self.bus.subscribe(ProjectEventType.FEATURE_UPDATED, self._on_feature_updated)
        self.bus.subscribe(ProjectEventType.GIT_COMMIT_CREATED, self._on_git_commit)
        self.bus.subscribe(ProjectEventType.TEST_FAILED, self._on_test_failed)

    def _can_run_check(self, project_id: str, max_per_hour: int = 10) -> bool:
        now = time.time()
        one_hour_ago = now - 3600
        timestamps = self._check_timestamps.setdefault(project_id, [])
        # Clean older than 1h
        self._check_timestamps[project_id] = [t for t in timestamps if t > one_hour_ago]
        return len(self._check_timestamps[project_id]) < max_per_hour

    def trigger_check(self, project_id: str, trigger_name: str, force: bool = False) -> None:
        """Triggers a check in a non-blocking background thread."""
        if not self._can_run_check(project_id) and not force:
            logger.info("Check throttled for project %s (max checks per hour reached)", project_id)
            return

        def worker():
            try:
                self.pm.run_project_check(project_id, trigger=trigger_name, force=force)
                self._check_timestamps.setdefault(project_id, []).append(time.time())
            except Exception as e:
                logger.exception("Error executing project check for %s: %s", project_id, e)

        threading.Thread(target=worker, daemon=True).start()

    def _on_task_completed(self, payload: Dict[str, Any]) -> None:
        pid = payload.get("project_id") or self._active_project_id
        if pid:
            self.trigger_check(pid, trigger_name="task_completed")

    def _on_feature_updated(self, payload: Dict[str, Any]) -> None:
        pid = payload.get("project_id") or self._active_project_id
        if pid:
            self.trigger_check(pid, trigger_name="feature_updated")

    def _on_git_commit(self, payload: Dict[str, Any]) -> None:
        pid = payload.get("project_id") or self._active_project_id
        if pid:
            self.trigger_check(pid, trigger_name="git_commit")

    def _on_test_failed(self, payload: Dict[str, Any]) -> None:
        pid = payload.get("project_id") or self._active_project_id
        if pid:
            self.trigger_check(pid, trigger_name="test_failed", force=True)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._timer_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._timer_thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._timer_thread and self._timer_thread.is_alive():
            self._timer_thread.join(timeout=1.0)

    def _run_loop(self) -> None:
        elapsed = 0
        while self._running and not self._stop_event.is_set():
            if self._stop_event.wait(timeout=1.0):
                break
            elapsed += 1

            with self._lock:
                pid = self._active_project_id
                interval_str = self._interval_setting

            seconds = INTERVAL_SECONDS.get(interval_str)
            if pid and seconds and elapsed >= seconds:
                elapsed = 0
                self.trigger_check(pid, trigger_name="periodic_timer")
