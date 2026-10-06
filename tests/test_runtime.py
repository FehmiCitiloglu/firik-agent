"""The desktop may submit its first task as soon as the runtime is returned."""
import asyncio
import threading

from firik_agent.core.runtime import AsyncRuntime


def test_constructor_waits_for_loop_to_accept_first_task(monkeypatch):
    factory = asyncio.new_event_loop
    loop_created = threading.Event()
    allow_start = threading.Event()
    caller_finished = threading.Event()
    runtimes, results, errors = [], [], []

    def delayed_loop():
        loop = factory()
        run = loop.run_forever

        def run_forever():
            loop_created.set()
            if not allow_start.wait(2):
                raise RuntimeError("test did not allow event loop to start")
            run()

        loop.run_forever = run_forever
        return loop

    monkeypatch.setattr(asyncio, "new_event_loop", delayed_loop)

    async def first_task():
        return "ready"

    def caller():
        try:
            runtime = AsyncRuntime()
            runtimes.append(runtime)
            assert runtime.is_running
            results.append(runtime.run_sync(first_task(), timeout=2))
        except BaseException as exc:
            errors.append(exc)
        finally:
            caller_finished.set()

    thread = threading.Thread(target=caller)
    thread.start()
    try:
        assert loop_created.wait(1)
        # Loop creation alone must not release the constructor's caller.
        assert not caller_finished.wait(0.05)
        allow_start.set()
        assert caller_finished.wait(2)
        assert not errors
        assert results == ["ready"]
    finally:
        allow_start.set()
        thread.join(timeout=3)
        for runtime in runtimes:
            runtime.shutdown()
