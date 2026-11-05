"""Learning dashboard operations using the existing runtime and Tk dispatch."""
from __future__ import annotations

import asyncio
import threading


class ImprovementBridge:
    """Host-only management surface. These methods are never model tools."""

    _IMPROVEMENT_ACTIONS = frozenset({"status", "settings", "configure", "history", "proposals", "show", "diff",
        "reflect", "evaluate", "approve", "reject", "rollback", "metrics", "memories", "remove_memory",
        "retrieve", "propose_memory", "propose_skill", "propose_tool", "propose_harness", "stage_code",
        "experiment", "monitor", "generate_code", "revise", "disable_skill", "pin_memory", "prune", "cancel"})

    def _init_improvement_bridge(self):
        self._improvement_lock = threading.RLock()
        self._improvement_coordinator = None
        self._improvement_closed = False
        self._improvement_pending = 0

    @property
    def improvement(self):
        with self._improvement_lock:
            if self._improvement_closed:
                raise RuntimeError("Learning service is closed.")
            if self._improvement_coordinator is None:
                from firik_agent.self_improvement.coordinator import coordinator_for
                self._improvement_coordinator = coordinator_for(self.projects.db, config=self.config,
                    config_path=getattr(self, "config_path", None))
            return self._improvement_coordinator

    def get_improvement_coordinator(self):
        return self.improvement

    def improvement_async(self, action, *args, on_success, on_error, **kwargs):
        """Bound queued UI operations; all storage/reflection runs off Tk."""
        if action not in self._IMPROVEMENT_ACTIONS:
            raise ValueError("Unknown learning operation.")
        with self._improvement_lock:
            if self._improvement_closed:
                self._dispatch(on_error, "Öğrenme servisi kapalı.")
                return None
            if self._improvement_pending >= 16:
                self._dispatch(on_error, "Öğrenme kuyruğu dolu; mevcut işlemin tamamlanmasını bekleyin.")
                return None
            self._improvement_pending += 1

        def operation():
            return getattr(self.improvement, action)(*args, **kwargs)

        async def run():
            try:
                if action == "generate_code":
                    if kwargs.get("allow_model_digest") is not True:
                        raise PermissionError("Model kaynağına görev ve seçili dosyaları gönderme onayı gerekiyor.")
                    coordinator = await asyncio.to_thread(lambda: self.improvement)
                    settings = await asyncio.to_thread(coordinator.settings)
                    if settings.get("mode") != "experiment":
                        raise PermissionError("Kod üretmek için deney modu açıkça etkinleştirilmelidir.")
                    # Reuse the service's initialized Agent on its own runtime;
                    # no model/provider switch and no Tk calls occur here.
                    agent = self.get_agent()
                    if not getattr(agent, "_is_initialized", False):
                        await agent.initialize()
                    return await coordinator.generate_code(agent, *args, **kwargs)
                return await asyncio.to_thread(operation)
            finally:
                with self._improvement_lock:
                    self._improvement_pending -= 1

        def failed(error):
            from firik_agent.projects.coding_sessions import redact_sensitive
            from pydantic import ValidationError
            message = ("Öğrenme ayarları veya giriş bilgileri doğrulanamadı." if isinstance(error, ValidationError)
                       else str(redact_sensitive(str(error))))
            self._dispatch(on_error, message)

        coroutine = run()
        try:
            return self.runtime.submit(coroutine, on_done=lambda result: self._dispatch(on_success, result), on_error=failed)
        except BaseException:
            coroutine.close()
            with self._improvement_lock:
                self._improvement_pending -= 1
            raise

    def _close_improvement_bridge(self):
        if not hasattr(self, "_improvement_lock"):
            # Shutdown is also valid after partial construction or when another
            # service capability is exercised without starting learning.
            return
        with self._improvement_lock:
            self._improvement_closed = True
            coordinator = self._improvement_coordinator or getattr(getattr(self, "projects", None), "db", None)
            if coordinator is not None and not hasattr(coordinator, "close"):
                coordinator = getattr(coordinator, "_improvement_coordinator", None)
        if coordinator is not None:
            coordinator.close()
