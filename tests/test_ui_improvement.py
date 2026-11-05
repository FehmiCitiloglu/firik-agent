"""Learning UI delivery runs on the main thread and approvals use exact revisions."""
import asyncio
from concurrent.futures import Future
import queue
import threading
from types import SimpleNamespace

import pytest

from firik_agent.ui.improvement_bridge import ImprovementBridge
from firik_agent.ui.views.improvement_view import ImprovementView


class WorkerRuntime:
    def __init__(self):
        self.threads = []

    def submit(self, coro, on_done=None, on_error=None):
        result = Future()
        def worker():
            try:
                value = asyncio.run(coro)
                if on_done:
                    on_done(value)
                result.set_result(value)
            except BaseException as error:
                if on_error:
                    on_error(error)
                result.set_exception(error)
        thread = threading.Thread(target=worker)
        self.threads.append(thread)
        thread.start()
        return result


def test_bridge_runs_storage_off_main_thread_and_dispatches_callbacks():
    callbacks = queue.SimpleQueue()
    main_thread = threading.get_ident()
    observed = []
    operation_threads = []
    class Service(ImprovementBridge):
        pass
    service = Service()
    service._init_improvement_bridge()
    service.runtime = WorkerRuntime()
    service._dispatch = lambda callback, *args: callbacks.put((callback, args))
    def status():
        operation_threads.append(threading.get_ident())
        return {"mode": "observe"}
    service._improvement_coordinator = SimpleNamespace(status=status, close=lambda: None)
    future = service.improvement_async("status", on_success=lambda value: observed.append((threading.get_ident(), value)),
                                       on_error=lambda error: pytest.fail(error))
    assert future.result(timeout=3) == {"mode": "observe"}
    assert not observed
    callback, args = callbacks.get_nowait()
    callback(*args)
    assert operation_threads[0] != main_thread
    assert observed == [(main_thread, {"mode": "observe"})]
    service._close_improvement_bridge()


def test_bridge_rejects_unregistered_authority_and_closed_work():
    class Service(ImprovementBridge):
        pass
    service = Service()
    service._init_improvement_bridge()
    service._dispatch = lambda callback, *args: callback(*args)
    with pytest.raises(ValueError):
        service.improvement_async("load_plugin_from_code", on_success=lambda _: None, on_error=lambda _: None)
    service._close_improvement_bridge()
    errors = []
    assert service.improvement_async("reflect", "session", on_success=lambda _: None, on_error=errors.append) is None
    assert errors


def test_bridge_queue_is_bounded_and_shutdown_closes_coordinator():
    class Service(ImprovementBridge):
        pass
    service = Service()
    service._init_improvement_bridge()
    service._dispatch = lambda callback, *args: callback(*args)
    closed = []
    service._improvement_coordinator = SimpleNamespace(close=lambda: closed.append(True))
    service._improvement_pending = 16
    errors = []
    assert service.improvement_async("status", on_success=lambda _: None, on_error=errors.append) is None
    assert errors and service._improvement_pending == 16
    service._close_improvement_bridge()
    assert closed == [True]


def test_desktop_agents_use_the_same_projects_database(monkeypatch):
    from firik_agent.ui.service import FirikService
    agents = []
    class Agent:
        def __init__(self, **kwargs):
            agents.append(self)
    monkeypatch.setattr("firik_agent.ui.service.Agent", Agent)
    database = object()
    registry = object()
    service = SimpleNamespace(_session_agents={}, config={"mcp_servers": {}}, config_path=None,
        projects=SimpleNamespace(db=database), agent=None,
        mcp_manager=SimpleNamespace(tool_registry=registry), session_tool_filter=SimpleNamespace(tool_registry=None))
    regular = FirikService.get_agent(service, "session")
    singleton = FirikService.get_agent(service)
    assert regular._learning_database is database
    assert singleton._learning_database is database
    assert singleton.tools is registry


def test_approval_uses_the_reviewed_revision_and_refreshes_review(monkeypatch):
    calls = []
    reset = []
    view = SimpleNamespace(selected={"id": "p_1", "revision": 4, "kind": "memory"},
        _call=lambda *args, **kwargs: calls.append((args, kwargs)), _set_detail=lambda value: None,
        _reset_selection=lambda: reset.append(True), status_label=SimpleNamespace(configure=lambda **kwargs: None))
    monkeypatch.setattr("firik_agent.ui.views.improvement_view.messagebox.askyesno", lambda *args, **kwargs: True)
    ImprovementView._decision(view, "approve")
    assert calls[0][0] == ("approve", "p_1")
    assert calls[0][1]["expected_revision"] == 4
    assert calls[0][1]["actor"] == "user"
    calls[0][1]["callback"]({"status": "applied", "revision": 5})
    assert reset


def test_declined_desktop_confirmation_does_not_submit(monkeypatch):
    view = SimpleNamespace(selected={"id": "p", "revision": 2, "kind": "skill"},
                           _call=lambda *args, **kwargs: pytest.fail("Declined proposal was submitted"))
    monkeypatch.setattr("firik_agent.ui.views.improvement_view.messagebox.askyesno", lambda *args, **kwargs: False)
    ImprovementView._decision(view, "approve")


def test_pending_proposal_status_is_displayed_without_fabricated_success():
    from firik_agent.ui.views.improvement_view import display_json
    result = display_json({"status": "pending_approval", "cost": None, "tokens": None})
    assert '"pending_approval"' in result and result.count("null") == 2


def test_bridge_generation_awaits_existing_agent_on_runtime_with_explicit_consent():
    callbacks = queue.SimpleQueue()
    threads = []
    main_thread = threading.get_ident()
    class Service(ImprovementBridge):
        pass
    service = Service()
    service._init_improvement_bridge()
    service.runtime = WorkerRuntime()
    service._dispatch = lambda callback, *args: callbacks.put((callback, args))
    class Agent:
        _is_initialized = False
        async def initialize(self):
            threads.append(threading.get_ident())
            self._is_initialized = True
    agent = Agent()
    service.get_agent = lambda: agent
    async def generate(pinned_agent, *args, **kwargs):
        assert pinned_agent is agent and pinned_agent._is_initialized
        assert args == ("/project", ["parser.py"], "Fix parser")
        assert kwargs == {"allow_model_digest": True, "project_id": "p"}
        threads.append(threading.get_ident())
        return {"status": "draft"}
    service._improvement_coordinator = SimpleNamespace(settings=lambda: {"mode": "experiment"},
        generate_code=generate, close=lambda: None)
    observed = []
    future = service.improvement_async("generate_code", "/project", ["parser.py"], "Fix parser",
        allow_model_digest=True, project_id="p", on_success=observed.append, on_error=lambda error: pytest.fail(error))
    assert future.result(timeout=3) == {"status": "draft"}
    assert not observed and all(thread != main_thread for thread in threads)
    callback, args = callbacks.get_nowait()
    callback(*args)
    assert observed == [{"status": "draft"}] and service._improvement_pending == 0
    service._close_improvement_bridge()


@pytest.mark.parametrize("settings,consent", [({"mode": "observe"}, True), ({"mode": "experiment"}, False)])
def test_bridge_generation_refuses_missing_mode_or_consent_before_agent(settings, consent):
    class Service(ImprovementBridge):
        pass
    service = Service()
    service._init_improvement_bridge()
    service.runtime = WorkerRuntime()
    service._dispatch = lambda callback, *args: callback(*args)
    service.get_agent = lambda: pytest.fail("Unauthorized model initialization")
    service._improvement_coordinator = SimpleNamespace(settings=lambda: settings, close=lambda: None)
    errors = []
    future = service.improvement_async("generate_code", "/project", ["parser.py"], "Fix parser",
        allow_model_digest=consent, on_success=lambda _: pytest.fail("Unauthorized generation"), on_error=errors.append)
    with pytest.raises(PermissionError):
        future.result(timeout=3)
    assert errors and service._improvement_pending == 0
    service._close_improvement_bridge()


def test_other_operations_routes_experiment_and_code_sections():
    observed = []
    view = SimpleNamespace(section=SimpleNamespace(get=lambda: "Deneyler"),
        _experiment_actions=lambda: observed.append("experiment"))
    ImprovementView._other_operations(view)
    view.section.get = lambda: "Kod İyileştirmeleri"
    view._code_actions = lambda: observed.append("code")
    ImprovementView._other_operations(view)
    assert observed == ["experiment", "code"]


def test_stop_learning_remains_available_during_busy_generation():
    calls = []
    shown = []
    view = SimpleNamespace(_busy=True,
        service=SimpleNamespace(improvement_async=lambda *args, **kwargs: calls.append((args, kwargs))),
        _alive=lambda: True, _set_detail=shown.append,
        status_label=SimpleNamespace(configure=lambda **kwargs: None))
    ImprovementView._stop_learning(view)
    assert calls[0][0] == ("configure", {"mode": "off", "automatic_reflection": False})
    calls[0][1]["on_success"]({"mode": "off"})
    assert shown == [{"mode": "off"}]
