from __future__ import annotations
import asyncio
from concurrent.futures import Future
import logging
import threading
from typing import Any, Callable, Coroutine, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AsyncRuntime:
    """
    Dedicated, long-running asyncio event loop hosted in a daemon thread.

    Solves the problem of MCP subprocess pipes, HTTP connection pools,
    and agent streaming sessions being tied to a single persistent event loop,
    preventing 'future belongs to a different loop' or broken pipe errors.
    """

    _instance: Optional[AsyncRuntime] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready_event = threading.Event()
        self._running = False
        self._start_thread()

    @classmethod
    def get_instance(cls) -> AsyncRuntime:
        """Singleton accessor for application-wide runtime."""
        with cls._lock:
            if cls._instance is None or not cls._instance.is_running:
                cls._instance = cls()
            return cls._instance

    @property
    def is_running(self) -> bool:
        return self._running and self._loop is not None and self._loop.is_running()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if not self._loop:
            raise RuntimeError("AsyncRuntime event loop is not initialized.")
        return self._loop

    def _start_thread(self) -> None:
        self._running = True
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="FirikAsyncRuntime", daemon=True)
        self._thread.start()
        self._ready_event.wait(timeout=5.0)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        # Signal from inside the running loop. Signalling before run_forever
        # lets the constructor return while is_running is still false, so the
        # desktop's first MCP autostart task can be rejected on a fast caller.
        self._loop.call_soon(self._ready_event.set)
        logger.info("AsyncRuntime thread started.")
        try:
            self._loop.run_forever()
        finally:
            # Cancel all remaining tasks
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()
            self._running = False
            logger.info("AsyncRuntime thread stopped.")

    def run_coroutine(self, coro: Coroutine[Any, Any, T]) -> Future[T]:
        """Submit coroutine to the background loop and return a concurrent.futures.Future."""
        if not self.is_running or not self._loop:
            raise RuntimeError("AsyncRuntime is not running.")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run_sync(self, coro: Coroutine[Any, Any, T], timeout: Optional[float] = None) -> T:
        """Submit coroutine and block calling thread until complete or timeout."""
        future = self.run_coroutine(coro)
        return future.result(timeout=timeout)

    def submit(
        self,
        coro: Coroutine[Any, Any, T],
        on_done: Optional[Callable[[T], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> Future[T]:
        """
        Execute coroutine in background loop and safely dispatch on_done or on_error callbacks.
        """
        future = self.run_coroutine(coro)

        def _callback(f: Future[T]) -> None:
            try:
                res = f.result()
                if on_done:
                    on_done(res)
            except Exception as e:
                if on_error:
                    on_error(e)
                else:
                    logger.error("AsyncRuntime unhandled exception in background task: %s", e)

        future.add_done_callback(_callback)
        return future

    def shutdown(self, wait: bool = True) -> None:
        """Gracefully stop the background event loop and its thread."""
        if not self._running or not self._loop:
            return

        self._running = False

        def _stop():
            self._loop.stop()

        self._loop.call_soon_threadsafe(_stop)
        if wait and self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("AsyncRuntime shutdown complete.")
