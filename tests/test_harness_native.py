"""The lifecycle adapter must preserve delivery, permission and cleanup boundaries."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import threading
import subprocess
import sys

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.tools import ToolRegistry
from firik_agent.harness.base import HarnessBackend
from firik_agent.harness.models import HarnessError, HarnessExecutionPolicy, HarnessTask, HarnessUsage
from firik_agent.harness.native import NativeHarnessBackend


def runtime(script):
    return SimpleNamespace(_run_native_stream=script,
        providers=SimpleNamespace(get_active=lambda: SimpleNamespace(name="test", current_model="model-v1")),
        tools=ToolRegistry(), _is_initialized=True)


def policy(**options):
    return HarnessExecutionPolicy(tool_mode="native", **options)


@pytest.mark.asyncio
async def test_native_preserves_original_event_and_consumer_permission_boundary():
    allowed = True
    original = AgentEvent(AgentEventType.TEXT_DELTA, "s", content="answer")
    async def script(**kwargs):
        yield original
        assert not allowed  # Consumer must observe event before runtime continues.
        yield AgentEvent(AgentEventType.DONE, "s", content="complete")
    backend = NativeHarnessBackend(runtime(script))
    assert isinstance(backend, HarnessBackend)
    session = await backend.initialize_session(policy=policy(), session_id="s")
    rid = await backend.execute_task(session, HarnessTask("task", model="model-v1"))
    events = []
    async for event in backend.stream_events(session, rid):
        events.append(event)
        allowed = False
    result = await backend.inspect_run(session, rid)
    assert events[0].native_event is original
    assert [event.sequence for event in events] == [1, 2]
    assert result.status == "completed" and result.text == "complete"
    assert result.usage.input_tokens is None and result.usage.cost_usd is None
    assert not result.completion.verified
    assert original.metadata["harness_backend"] == "native"
    await backend.close_session(session)


@pytest.mark.asyncio
async def test_normal_close_does_not_cancel_finished_host_run():
    cancellation = threading.Event()
    async def script(**kwargs):
        yield AgentEvent(AgentEventType.DONE, "s", content="ok")
    backend = NativeHarnessBackend(runtime(script), runtime_options={"cancel_event": cancellation})
    session = await backend.initialize_session(policy=policy())
    rid = await backend.execute_task(session, HarnessTask("task"))
    assert len([event async for event in backend.stream_events(session, rid)]) == 1
    await backend.close_session(session)
    await backend.close_session(session)
    assert not cancellation.is_set()


@pytest.mark.asyncio
async def test_cancel_runtime_that_emits_done_during_cancel_does_not_deadlock():
    entered, finalized = asyncio.Event(), asyncio.Event()
    async def script(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            yield AgentEvent(AgentEventType.DONE, "s", content="cancelled")
        finally:
            finalized.set()
    backend = NativeHarnessBackend(runtime(script))
    session = await backend.initialize_session(policy=policy())
    rid = await backend.execute_task(session, HarnessTask("task"))
    await entered.wait()
    await asyncio.wait_for(backend.request_cancellation(session, rid), 1)
    assert finalized.is_set()
    assert (await backend.inspect_run(session, rid)).status == "cancelled"
    assert not [event async for event in backend.stream_events(session, rid)]
    await backend.close_session(session)


@pytest.mark.asyncio
async def test_timeout_closes_generator_and_retains_error():
    finalized = asyncio.Event()
    async def script(**kwargs):
        try:
            await asyncio.Event().wait()
            yield
        finally:
            finalized.set()
    backend = NativeHarnessBackend(runtime(script))
    session = await backend.initialize_session(policy=policy(timeout_seconds=0.02))
    rid = await backend.execute_task(session, HarnessTask("task"))
    with pytest.raises(TimeoutError):
        _ = [event async for event in backend.stream_events(session, rid)]
    result = await backend.inspect_run(session, rid)
    assert result.status == "failed" and result.errors[0].code == "timeout"
    assert finalized.is_set()
    await backend.close_session(session)


@pytest.mark.asyncio
async def test_repeated_cancellation_and_concurrent_close_preserve_awaited_cleanup():
    entered, cleaning, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def script(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
            yield
        finally:
            cleaning.set()
            await asyncio.sleep(0.03)
            cleaned.set()
    backend = NativeHarnessBackend(runtime(script))
    session = await backend.initialize_session(policy=policy())
    rid = await backend.execute_task(session, HarnessTask("task"))
    await entered.wait()
    first = asyncio.create_task(backend.request_cancellation(session, rid))
    await cleaning.wait()
    await asyncio.gather(first, backend.request_cancellation(session, rid),
                         backend.close_session(session), backend.close_session(session))
    assert cleaned.is_set()
    assert not backend._runs and not backend._sessions


@pytest.mark.asyncio
async def test_errors_retain_details_and_original_exception():
    problem = ValueError("specific runtime error")
    async def script(**kwargs):
        yield AgentEvent(AgentEventType.ERROR, "s", content="provider refused",
                         metadata={"failure_kind": "authorization_error", "status_code": 403})
        raise problem
    backend = NativeHarnessBackend(runtime(script))
    session = await backend.initialize_session(policy=policy())
    rid = await backend.execute_task(session, HarnessTask("task"))
    with pytest.raises(ValueError) as caught:
        _ = [event async for event in backend.stream_events(session, rid)]
    assert caught.value is problem
    result = await backend.inspect_run(session, rid)
    assert result.errors[0].details["status_code"] == 403
    assert result.errors[1].to_dict()["message"] == "specific runtime error"
    await backend.close_session(session)


@pytest.mark.asyncio
async def test_session_identity_is_not_just_a_caller_supplied_string():
    async def script(**kwargs):
        yield AgentEvent(AgentEventType.DONE, "s")
    backend = NativeHarnessBackend(runtime(script))
    session = await backend.initialize_session(policy=policy())
    with pytest.raises(HarnessError, match="does not belong"):
        await backend.execute_task(replace(session), HarnessTask("task"))
    with pytest.raises(HarnessError, match="shared"):
        await backend.initialize_session(policy=policy())
    rid = await backend.execute_task(session, HarnessTask("task"))
    with pytest.raises(HarnessError, match="active task"):
        await backend.execute_task(session, HarnessTask("second task"))
    await backend.close_session(session)
    with pytest.raises(HarnessError):
        await backend.inspect_run(session, rid)


@pytest.mark.parametrize("options", [{"require_sandbox": True}, {"token_budget": 100},
                                     {"permissions": {"shell.safe": "deny"}}])
def test_native_rejects_unenforceable_policy(options):
    backend = NativeHarnessBackend(SimpleNamespace())
    with pytest.raises(HarnessError):
        backend.validate_configuration(policy(**options))


def test_sandbox_tool_mode_alone_never_uses_host_runtime():
    with pytest.raises(HarnessError, match="confinement"):
        NativeHarnessBackend(SimpleNamespace()).validate_configuration(HarnessExecutionPolicy(tool_mode="sandbox"))


@pytest.mark.parametrize("options", [{"timeout_seconds": float("nan")}, {"timeout_seconds": True},
    {"max_events": True}, {"max_sessions": 0}, {"token_budget": -1}, {"allow_network": "false"}])
def test_policy_bounds_are_strict(options):
    with pytest.raises(ValueError):
        policy(**options)


@pytest.mark.parametrize("options", [{"input_tokens": -1}, {"output_tokens": True},
                                    {"cost_usd": float("nan")}])
def test_usage_unknowns_are_not_invalid_counts(options):
    with pytest.raises(ValueError):
        HarnessUsage(**options)


@pytest.mark.asyncio
async def test_external_selection_cannot_fall_through_existing_cli_or_coding_runtime():
    agent = Agent.__new__(Agent)
    agent.config = {"harness": {"default_backend": "pi", "backends": {"pi": {"enabled": True}}}}
    with pytest.raises(HarnessError, match="External harnesses"):
        await agent.run("task")
    with pytest.raises(HarnessError, match="External harnesses"):
        _ = [event async for event in agent.run_stream("task")]


@pytest.mark.asyncio
async def test_native_events_deduplicate_usage_using_existing_metrics_reducer():
    async def script(**kwargs):
        for kind, usage in [(AgentEventType.MODEL_REQUEST_STARTED, {}),
                (AgentEventType.MODEL_USAGE, {"prompt_tokens": 4, "completion_tokens": 2}),
                (AgentEventType.MODEL_USAGE, {"prompt_tokens": 4, "completion_tokens": 2}),
                (AgentEventType.MODEL_REQUEST_FINISHED, {"prompt_tokens": 4, "completion_tokens": 2})]:
            yield AgentEvent(kind, "s", metadata={"request_id": "r", "usage": usage, "status": "completed"})
        yield AgentEvent(AgentEventType.DONE, "s")
    backend = NativeHarnessBackend(runtime(script))
    session = await backend.initialize_session(policy=policy())
    rid = await backend.execute_task(session, HarnessTask("task"))
    _ = [event async for event in backend.stream_events(session, rid)]
    result = await backend.inspect_run(session, rid)
    assert result.usage.input_tokens == 4 and result.usage.output_tokens == 2
    assert result.usage.cost_usd is None
    await backend.close_session(session)


def test_passive_harness_import_does_not_read_dotenv_or_keychain():
    script = '''
import dotenv, keyring
def forbidden(*args, **kwargs):
    raise AssertionError("passive harness import accessed credentials")
dotenv.load_dotenv = forbidden
keyring.get_keyring = forbidden
from firik_agent.harness import cli
assert cli.main(["settings"]) == 0
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_disabled_external_inventory_never_executes_a_configured_program(monkeypatch, tmp_path):
    from firik_agent.harness.external import PiBackend, CodexBackend
    from firik_agent.harness.registry import backend_inventory
    called = []
    monkeypatch.setattr(PiBackend, "get_metadata", lambda self: called.append("pi"))
    monkeypatch.setattr(CodexBackend, "get_metadata", lambda self: called.append("codex"))
    executable = tmp_path / "untrusted-local-script"
    executable.write_text("raise AssertionError('must not be executed')")
    inventory = backend_inventory({"harness": {"backends": {"pi": {"enabled": False, "executable": str(executable)}}}})
    assert called == []
    assert next(item for item in inventory if item["name"] == "pi")["version"] is None


@pytest.mark.asyncio
async def test_native_model_identity_uses_provider_not_session_label():
    from firik_agent.core.session import ChatSession
    async def script(**kwargs):
        yield AgentEvent(AgentEventType.DONE, "s", content="done")
    backend = NativeHarnessBackend(runtime(script), ChatSession(session_id="s", model="display-label"))
    session = await backend.initialize_session(policy=policy())
    with pytest.raises(HarnessError, match="model differs"):
        await backend.execute_task(session, HarnessTask("task", model="display-label"))
    rid = await backend.execute_task(session, HarnessTask("task"))
    _ = [event async for event in backend.stream_events(session, rid)]
    assert (await backend.inspect_run(session, rid)).model == "model-v1"
    await backend.close_session(session)
