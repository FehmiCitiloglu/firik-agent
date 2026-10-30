"""Real Agent streaming integration for the guarded delegation cockpit."""
import asyncio
from dataclasses import FrozenInstanceError
import json
import shlex
import sys
import subprocess
import threading

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.subagents import SubagentLimits, SubagentToolFilter
from firik_agent.providers.base import BaseProvider


def tool(name, arguments, identifier="tool"):
    return ToolCall(id=identifier, function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


class ScriptedProvider(BaseProvider):
    def __init__(self, script, name="scripted", model="model-a"):
        super().__init__(name=name, base_url="http://unused.invalid", default_model=model)
        self.script = script
        self.requests = []
        self.model_metadata[model] = {"source": "provider", "supports_tools": True,
                                     "input_cost_per_million": 1.0, "output_cost_per_million": 2.0}

    async def chat_stream(self, messages, tools=None, **kwargs):
        request = {"messages": [message.model_copy(deep=True) for message in messages],
                   "tools": tools or [], "model": self.current_model, **kwargs}
        self.requests.append(request)
        async for chunk in self.script(len(self.requests), request):
            yield chunk

    async def chat(self, **kwargs):
        raise AssertionError("A child must not issue an unreserved fallback request")

    async def list_models(self):
        return []


def real_agent(provider):
    agent = Agent(config={"agent": {"max_iterations": 8}, "plugins": {"autoload_builtins": False}})
    agent._is_initialized = True
    agent.providers.register(provider.name, provider, set_active=True)
    return agent


def setup(tmp_path, factory, mode="auto", permissions=None, **kwargs):
    session = ChatSession(session_id="parent", model="model-a")
    control = CodingRunControl("parent", "project", "task", mode=mode, permissions=permissions)
    parent = CodingToolFilter(tmp_path, control)
    events = []
    return SubagentToolFilter(parent, session, factory, events.append, **kwargs), events


def committed_repository(root):
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "core.autocrlf", "false"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)


@pytest.mark.asyncio
async def test_real_streaming_worker_uses_parent_hash_gate_and_lease(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n")
    (tmp_path / "other.py").write_text("other = 1\n")
    committed_repository(tmp_path)
    async def script(turn, request):
        names = {item["function"]["name"] for item in request["tools"]}
        assert "spawn_subagent" not in names and "send_message" in names and "run_command" not in names
        if turn == 1:
            yield StreamChunk(tool_calls=[tool("read_file", {"path": "source.py"}, "read")])
        elif turn == 2:
            result = next(message for message in request["messages"] if message.tool_call_id == "read")
            snapshot = json.loads(result.content)
            yield StreamChunk(tool_calls=[
                tool("edit_file", {"path": "source.py", "expected_hash": snapshot["hash"],
                                    "old_text": "value = 1", "new_text": "value = 2"}, "edit"),
                tool("write_file", {"path": "created.py", "expected_hash": "new", "content": "created = 1\n"}, "create"),
                tool("write_file", {"path": "other.py", "expected_hash": "", "content": "bad"}, "outside")])
        else:
            assert any(message.tool_call_id == "outside" and "kapsam" in message.content for message in request["messages"])
            yield StreamChunk(delta_content="Kaynak değişti; kapsam dışı yazma engellendi.",
                              usage={"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
    provider = ScriptedProvider(script)
    manager, events = setup(tmp_path, lambda: real_agent(provider))
    try:
        spawned = await manager.spawn("worker", "Kaynağı düzenle", write_paths=["source.py", "created.py"])
        result = await manager.wait(spawned["agent_id"])
        assert result["passed"], result
        from pathlib import Path
        isolated = Path(result["workspace"]["path"])
        assert (tmp_path / "source.py").read_text() == "value = 1\n"
        assert (isolated / "source.py").read_text() == "value = 2\n"
        assert (tmp_path / "other.py").read_text() == "other = 1\n"
        assert set(result["artifacts"]) == {"source.py", "created.py"}
        assert not (tmp_path / "created.py").exists()
        assert (isolated / "created.py").read_text() == "created = 1\n"
        assert not result["merge_proposal"]["reviewed"]
        assert result["usage"]["total_tokens"] == 30
        assert manager.jobs[spawned["agent_id"]]["session"].session_id != manager.session.session_id
        assert all(event.metadata["parent_agent_id"] == manager.control.run_id for event in events)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_steering_delivered_once_at_next_model_boundary_and_handoff_observable(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n")
    in_request, release = asyncio.Event(), asyncio.Event()
    async def script(turn, request):
        if turn == 1:
            in_request.set()
            await release.wait()
            yield StreamChunk(tool_calls=[tool("read_file", {"path": "source.py"}, "read")])
        elif turn == 2:
            assert sum("Yeni odak" in str(message.content) for message in request["messages"]) == 1
            yield StreamChunk(tool_calls=[tool("send_message", {"agent_id": "parent", "message": "Kanıt source.py:1"}, "handoff")])
        else:
            assert sum("Yeni odak" in str(message.content) for message in request["messages"]) == 1
            yield StreamChunk(delta_content="Bitti")
    provider = ScriptedProvider(script)
    manager, events = setup(tmp_path, lambda: real_agent(provider))
    try:
        spawned = await manager.spawn("researcher", "Dosyayı incele")
        await in_request.wait()
        receipt = manager.queue_steer(spawned["agent_id"], "Yeni odak: değeri açıkla")
        assert all("Yeni odak" not in str(message.content) for message in provider.requests[0]["messages"])
        release.set()
        assert (await manager.wait(spawned["agent_id"]))["passed"]
        manager.drain_parent_messages(manager.session)
        assert "Kanıt source.py:1" in manager.session.messages[-1].content
        entries = [event.metadata["message"] for event in events if "message" in event.metadata]
        assert any(entry["message_id"] == receipt["message_id"] and entry["status"] == "queued" for entry in entries)
        assert any(entry["message_id"] == receipt["message_id"] and entry["status"] == "delivered" for entry in entries)
        assert any(entry["sender"] == spawned["agent_id"] and entry["recipient"] == "parent" for entry in entries)
    finally:
        release.set()
        await manager.close()


@pytest.mark.asyncio
async def test_token_and_unknown_cost_budgets_stop_before_provider_request(tmp_path):
    async def script(turn, request):
        yield StreamChunk(delta_content="Should not run")
    for limits in ({"token_budget": 1}, {"cost_budget": 0}):
        provider = ScriptedProvider(script)
        if "cost_budget" in limits:
            provider.model_metadata = {}
        manager, events = setup(tmp_path, lambda: real_agent(provider), budgets=limits)
        try:
            spawned = await manager.spawn("research", "İncele")
            result = await manager.wait(spawned["agent_id"])
            assert result["status"] == "failed" and not provider.requests
            assert "bütçe" in result["result"] or "fiyat" in result["result"]
        finally:
            await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limits,expected", [
    ({"token_budget": 1}, "failed"),
    ({"cost_budget": 0}, "failed"),
    ({"token_budget": 131072}, "completed"),
])
async def test_long_child_history_never_sends_unreserved_summary_inference(tmp_path, limits, expected):
    async def script(turn, request):
        assert request["messages"][-1].content == "Son görevi incele"
        yield StreamChunk(delta_content="Bütçelenmiş inceleme tamamlandı")

    class SummaryCapableProvider(ScriptedProvider):
        def __init__(self):
            super().__init__(script)
            self.summary_requests = []

        async def chat(self, **kwargs):
            self.summary_requests.append(kwargs)
            return Message(role="assistant", content="Önceki konuşma verisinin özeti")

    provider = SummaryCapableProvider()
    agents = []
    def factory():
        agent = real_agent(provider)
        agent.context_compactor.fallback_budget = 3000
        agents.append(agent)
        return agent

    manager, _ = setup(tmp_path, factory, budgets=limits)
    try:
        spawned = await manager.spawn("researcher", "Son görevi incele")
        # Populate a restored archive before the scheduled worker enters its loop.
        session = manager.jobs[spawned["agent_id"]]["session"]
        session.messages = [Message(role=role, content=f"tur {index} " + "x" * 500)
                            for index in range(30) for role in ("user", "assistant")]
        result = await manager.wait(spawned["agent_id"])
        assert result["status"] == expected, result
        assert not provider.summary_requests
        assert len(provider.requests) == (1 if expected == "completed" else 0)
        assert manager.jobs[spawned["agent_id"]]["request_number"] == len(provider.requests)
        assert agents[0].context_compactor.enabled  # Parent/factory configuration is preserved.
        assert len(session.messages) == (62 if expected == "completed" else 61)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_verified_cost_reservation_and_repeated_usage_frames_are_bounded(tmp_path):
    async def script(turn, request):
        usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        yield StreamChunk(delta_content="ok", usage=usage)
        yield StreamChunk(usage=usage)
    provider = ScriptedProvider(script)
    manager, _ = setup(tmp_path, lambda: real_agent(provider), budgets={"cost_budget": 1.0})
    try:
        spawned = await manager.spawn("reviewer", "İncele")
        result = await manager.wait(spawned["agent_id"])
        assert result["passed"]
        assert result["usage"]["total_tokens"] == 150
        assert result["usage"]["cost_usd"] == pytest.approx(0.0002)
        assert manager.snapshot()["reserved_cost"] <= 1.0
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_provider_model_selection_requires_authorized_resolver(tmp_path):
    async def script(turn, request):
        yield StreamChunk(delta_content="Configured model")
    provider = ScriptedProvider(script, name="local", model="model-b")
    called = []
    def resolver(name, model):
        called.append((name, model))
        if name != "local" or model != "model-b":
            raise PermissionError("Yapılandırılmamış sağlayıcı/model")
        return real_agent(provider)
    manager, _ = setup(tmp_path, lambda: None, provider_resolver=resolver)
    try:
        spawned = await manager.spawn("researcher", "İncele", provider="local", model="model-b")
        assert (await manager.wait(spawned["agent_id"]))["passed"]
        assert called == [("local", "model-b")]
        assert provider.requests[0]["model"] == "model-b"
        denied = await manager.spawn("researcher", "İncele", provider="unconfigured")
        assert (await manager.wait(denied["agent_id"]))["status"] == "failed"
    finally:
        await manager.close()
    no_resolver, _ = setup(tmp_path, lambda: None)
    with pytest.raises(PermissionError, match="çözümleyici"):
        await no_resolver.spawn("researcher", "İncele", provider="unconfigured")
    await no_resolver.close()


@pytest.mark.asyncio
async def test_dependencies_receive_real_result_and_cancelled_prerequisite_blocks_runner(tmp_path):
    manager, _ = setup(tmp_path, lambda: None, limits={"max_parallel": 1})
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def runner(assignment, tools):
        job = manager.jobs[assignment.context["agent_id"]]
        calls.append(job["agent_id"])
        if len(calls) == 1:
            started.set()
            await release.wait()
            return "Önceki kanıt"
        assert "Önceki kanıt" not in job["session"].system_prompt
        assert any("Önceki kanıt" in (message.content or "") for message in job["session"].messages if message.role == "user")
        return "Bağımlı sonuç"
    manager.coordinator.register("planning", runner)
    try:
        first = await manager.spawn("research", "Ön koşul")
        await started.wait()
        second = await manager.spawn("research", "Bağımlı görev", depends_on=[first["agent_id"]])
        assert second["status"] == "waiting"
        release.set()
        assert (await manager.wait(second["agent_id"]))["passed"]
        blocked = await manager.spawn("research", "Durdurulacak")
        dependent = await manager.spawn("research", "Durdurulanın bağımlısı", depends_on=[blocked["agent_id"]])
        await manager.cancel(blocked["agent_id"])
        assert (await manager.wait(dependent["agent_id"]))["status"] == "failed"
        assert dependent["agent_id"] not in calls
    finally:
        release.set()
        await manager.close()


@pytest.mark.asyncio
async def test_live_spawn_denial_and_immutable_limits(tmp_path):
    manager, _ = setup(tmp_path, lambda: None)
    manager.control.set_policy({"spawn_agents": "deny"})
    assert "spawn_subagent" not in {item["function"]["name"] for item in manager.get_schemas(manager.session)}
    with pytest.raises(PermissionError):
        await manager.spawn("research", "İncele")
    with pytest.raises(FrozenInstanceError):
        manager.limits.max_children = 16
    with pytest.raises(ValueError):
        SubagentLimits(max_depth=2)
    await manager.close()


@pytest.mark.asyncio
async def test_cancel_child_during_inherited_edit_approval_cleans_parent_pending_request(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n")
    committed_repository(tmp_path)
    manager = None
    async def script(turn, request):
        snapshot = manager.parent.read_file("source.py")
        yield StreamChunk(tool_calls=[tool("edit_file", {"path": "source.py", "expected_hash": snapshot["hash"],
                                                          "old_text": "value = 1", "new_text": "value = 2", "step_id": "edit"})])
    provider = ScriptedProvider(script)
    manager, events = setup(tmp_path, lambda: real_agent(provider), mode="approved")
    manager.parent.update_plan([{"id": "edit", "title": "Kaynağı düzenle", "acceptance_criteria": ["Değer değişir"],
                                 "verification": "Değeri oku"}])
    try:
        spawned = await manager.spawn("worker", "Düzenle", write_paths=["source.py"])
        for _ in range(100):
            if manager.control.pending_approvals:
                break
            await asyncio.sleep(0.01)
        assert manager.control.pending_approvals, manager.jobs[spawned["agent_id"]]["result"]
        assert next(iter(manager.control.pending_approvals.values()))["agent_id"] == spawned["agent_id"]
        await manager.cancel(spawned["agent_id"])
        assert (await manager.wait(spawned["agent_id"]))["status"] == "cancelled"
        assert not manager.control.pending_approvals
        assert (tmp_path / "source.py").read_text() == "value = 1\n"
        assert any(event.metadata["status"] == "cancelled" for event in events)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_wait_on_child_cancelled_before_entry_does_not_cancel_parent(tmp_path):
    manager, _ = setup(tmp_path, lambda: None)
    spawned = await manager.spawn("research", "İncele")
    await manager.cancel(spawned["agent_id"])
    result = await manager.wait(spawned["agent_id"])
    assert result["status"] == "cancelled" and not result["passed"]
    assert not manager.control.cancel_event.is_set()
    await manager.close()


@pytest.mark.asyncio
async def test_host_tester_cannot_execute_scripts_outside_an_os_sandbox(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n")
    committed_repository(tmp_path)
    async def script(turn, request):
        names = {schema["function"]["name"] for schema in request["tools"]}
        assert not names & {"run_tests", "run_quality_gate", "run_command", "write_file", "edit_file"}
        yield StreamChunk(delta_content="Host test execution unavailable for isolated child")
    provider = ScriptedProvider(script)
    manager, _ = setup(tmp_path, lambda: real_agent(provider), permissions={"allow_terminal": False, "allow_tests": True})
    try:
        spawned = await manager.spawn("tester", "Testleri incele")
        assert (await manager.wait(spawned["agent_id"]))["passed"]
        assert not manager.control.verifications
        assert (tmp_path / "source.py").read_text() == "value = 1\n"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_workers_from_different_managers_have_distinct_workspaces(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n")
    committed_repository(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    async def script(turn, request):
        entered.set()
        await release.wait()
        yield StreamChunk(delta_content="ok")
    provider = ScriptedProvider(script)
    first, _ = setup(tmp_path, lambda: real_agent(provider))
    second, _ = setup(tmp_path, lambda: real_agent(provider))
    try:
        job = await first.spawn("worker", "Çalış", write_paths=["source.py"])
        await asyncio.wait_for(entered.wait(), 3)
        independent = await second.spawn("worker", "Yalıtılmış iş", write_paths=["source.py"])
        assert job["workspace"]["path"] != independent["workspace"]["path"]
        release.set()
        assert (await second.wait(independent["agent_id"]))["passed"]
        assert (await first.wait(job["agent_id"]))["passed"]
        assert (tmp_path / "source.py").read_text() == "value = 1\n"
    finally:
        release.set()
        await first.close()
        await second.close()


# Reuse the existing service/SQLite fixture; these calls use the real desktop action bridge.
from test_coding_bridge import bridge as bridge, start as start_bridge


def configure_manual_service(bridge):
    service, state, roots = bridge
    session, initial = start_bridge(bridge)
    initial.result(timeout=5)
    settings = service.projects.db.get_settings("p1")
    settings.coding_preferences.update(provider="local", mode="auto")
    service.projects.db.save_settings(settings)
    return service, state, roots, session


def test_idle_manual_agent_result_history_and_usage_are_durable(bridge):
    service, state, roots, session = configure_manual_service(bridge)
    results, errors = [], []
    future = service.spawn_coding_agent_async(session.session_id, "researcher", "main.py dosyasını incele",
        results.append, errors.append)
    result = future.result(timeout=5)
    assert not errors and result["passed"]
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert record.status == "completed" and record.tokens_used == 6
    children = [entry for entry in record.decisions if entry.get("kind") == "subagent"]
    assert len(children) == 1 and children[0]["status"] == "completed"
    assert children[0]["messages"][-1]["content"] == "Türkçe yanıt"
    assert not service._streams and session.session_id not in service._coding_managers


def test_idle_manual_child_cancel_marks_run_cancelled_and_clears_registries(bridge):
    service, state, roots, session = configure_manual_service(bridge)
    entered = threading.Event()
    async def script(turn, messages):
        entered.set()
        await asyncio.Event().wait()
        yield
    state["script"] = script
    errors = []
    future = service.spawn_coding_agent_async(session.session_id, "researcher", "Bekleyen inceleme",
        lambda value: None, errors.append)
    assert entered.wait(timeout=5)
    manager = service._coding_managers[session.session_id]
    identifier = next(iter(manager.jobs))
    service.cancel_coding_agent_async(session.session_id, identifier, lambda value: None, errors.append).result(timeout=5)
    result = future.result(timeout=5)
    assert result["status"] == "cancelled" and not errors
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert record.status == "cancelled"
    assert not service._streams and session.session_id not in service._coding_controls
    assert session.session_id not in service._coding_managers


def test_idle_manual_close_failure_still_cleans_registries_and_records_failure(bridge, monkeypatch):
    service, state, roots, session = configure_manual_service(bridge)
    original = SubagentToolFilter.close
    async def fail_after_cleanup(manager):
        await original(manager)
        raise RuntimeError("forced child journal cleanup error")
    monkeypatch.setattr(SubagentToolFilter, "close", fail_after_cleanup)
    future = service.spawn_coding_agent_async(session.session_id, "researcher", "İnceleme",
        lambda value: None, lambda error: None)
    try:
        result = future.result(timeout=5)
        assert result["status"] == "failed"
    except RuntimeError as error:
        assert "forced child journal" in str(error)
    record = service.coding_store.latest_run("p1", session_id=session.session_id)
    assert record.status == "failed" and "forced child journal" in record.error
    assert not service._streams and session.session_id not in service._coding_controls
    assert session.session_id not in service._coding_managers
