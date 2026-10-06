"""Real service, agent loop, SQLite and local tools with scripted model frames."""
import asyncio
from concurrent.futures import CancelledError
import hashlib
import json
import sys
import threading

import pytest

from firik_agent.core.events import AgentEventType
from firik_agent.core.types import Message, ModelInfo, StreamChunk, ToolCall, ToolCallFunction, ToolDefinition
from firik_agent.mcp.models import MCPServerConfig, ToolPolicy, ToolPolicyMode
from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project, Task
from firik_agent.providers.registry import ProviderRegistry
from firik_agent.ui.service import FirikService


def tool(name, **arguments):
    return ToolCall(id=f"call_{name}", function=ToolCallFunction(name=name, arguments=json.dumps(arguments)))


class ScriptedProvider:
    def __init__(self, state, name):
        self.state, self.name, self.iteration = state, name, 0
        self.current_model = "fake"

    def set_model(self, model):
        self.current_model = model

    async def chat_stream(self, messages, **kwargs):
        self.iteration += 1
        self.state["requests"].append({"provider": self.name, "model": self.current_model,
                                       "messages": [m.model_dump() for m in messages], "tools": kwargs.get("tools")})
        async for frame in self.state["script"](self.iteration, messages):
            yield frame

    async def chat(self, **kwargs):
        raise RuntimeError("Scripted stream failed")


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    state = {"requests": []}
    async def simple(iteration, messages):
        yield StreamChunk(delta_content="Türkçe yanıt", usage={"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6})
    state["script"] = simple
    def providers(config):
        registry = ProviderRegistry()
        for name in ("firik", "local"):
            registry.register(name, ScriptedProvider(state, name))
        registry.set_active("firik")
        return registry
    monkeypatch.setattr(ProviderRegistry, "from_config", providers)
    service = FirikService()
    roots = {}
    for name in ("p1", "p2"):
        root = tmp_path / name
        root.mkdir()
        (root / "main.py").write_text("value = 'eski'\n", encoding="utf-8", newline="\n")
        service.projects.db.create_project(Project(id=name, name=name, local_path=str(root)))
        service.projects.db.save_task(Task(id=f"t-{name}", project_id=name, title=f"Görev {name}"))
        roots[name] = root
    try:
        yield service, state, roots
    finally:
        service.shutdown()
        service.projects.scheduler.stop()


def start(bridge, project="p1", session=None, mode="auto", **callbacks):
    service, state, roots = bridge
    session = session or service.get_or_create_session()
    options = {"project_id": project, "task_id": f"t-{project}", "mode": mode,
               "permissions": {"allow_edits": True, "allow_tests": True},
               "provider": "local", "visibility_report": [{"kind": "task", "included": True}]}
    options.update(callbacks.pop("options", {}))
    future = service.chat_agent_stream_async(session.session_id, callbacks.pop("prompt", "İsteği uygula"),
                    cwd=str(roots[project]), coding_options=options, **callbacks)
    return session, future


def test_subagents_real_loop_read_only_history_and_usage(bridge):
    service, state, roots = bridge
    async def script(iteration, messages):
        child = "Araştırma uzmanısın" in messages[0].content
        if child:
            if iteration == 1:
                yield StreamChunk(tool_calls=[tool("read_file", path="main.py")])
            else:
                yield StreamChunk(delta_content="main.py:1 eski değer içeriyor.", usage={"total_tokens": 7})
        elif iteration == 1:
            yield StreamChunk(tool_calls=[tool("spawn_subagent", role="research", task="main.py dosyasını incele.")])
        elif iteration == 2:
            spawned = next(json.loads(m.content) for m in messages if m.name == "spawn_subagent")
            yield StreamChunk(tool_calls=[tool("wait_subagent", agent_id=spawned["agent_id"])])
        else:
            result = next(json.loads(m.content) for m in messages if m.name == "wait_subagent")
            assert result["passed"] and "eski değer" in result["result"]
            yield StreamChunk(delta_content="Araştırma sonucunu topladım.", usage={"total_tokens": 5})
    state["script"] = script
    events, errors = [], []
    session, future = start(bridge, options={"subagents_enabled": True}, on_event=events.append, on_error=errors.append)
    future.result(timeout=8)
    assert not errors
    assert (roots["p1"] / "main.py").read_text(encoding="utf-8") == "value = 'eski'\n"
    children = [request for request in state["requests"] if "Araştırma uzmanısın" in request["messages"][0]["content"]]
    assert len(children) == 2
    for request in children:
        names = {entry["function"]["name"] for entry in request["tools"]}
        assert "read_file" in names and not names & {"write_file", "run_command", "spawn_subagent", "record_verification"}
        assert all(message.get("content") != "İsteği uygula" for message in request["messages"])
    assert not any(message.name == "read_file" for message in session.messages)
    record = service.coding_store.latest_run("p1")
    assert record.tokens_used == 12
    decision = next(item for item in record.decisions if item["kind"] == "subagent")
    assert decision["status"] == "completed" and decision["parent_run_id"] == record.id
    assert any(message.get("name") == "read_file" for message in decision["messages"])
    stored = service.coding_store.list_events(record.id)
    assert any(event.event_type == "subagent_event" for event in stored)
    assert sum(event.type == AgentEventType.DONE for event in events) == 1
    assert "alt ajanları kullanılabilir" not in session.system_prompt


def test_parent_cancellation_stops_active_subagent_and_records_it(bridge):
    service, state, roots = bridge
    entered = threading.Event()
    async def script(iteration, messages):
        if "Araştırma uzmanısın" in messages[0].content:
            entered.set()
            await asyncio.Event().wait()
            yield
        elif iteration == 1:
            yield StreamChunk(tool_calls=[tool("spawn_subagent", role="research", task="main.py incele")])
        else:
            spawned = next(json.loads(m.content) for m in messages if m.name == "spawn_subagent")
            yield StreamChunk(tool_calls=[tool("wait_subagent", agent_id=spawned["agent_id"])])
    state["script"] = script
    session, future = start(bridge, options={"subagents_enabled": True})
    assert entered.wait(timeout=4)
    assert service.coding_control(session.session_id, "cancel")
    try:
        future.result(timeout=4)
    except CancelledError:
        pass
    # Future cancellation may precede the coroutine's durable cleanup.
    import time
    deadline = time.monotonic() + 4
    while service._streams and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not service._streams
    record = service.coding_store.latest_run("p1")
    assert record.status == "cancelled"
    children = [item for item in record.decisions if item["kind"] == "subagent"]
    assert len(children) == 1 and children[0]["status"] == "cancelled"


def test_subagents_disabled_does_not_expose_delegation(bridge):
    service, state, roots = bridge
    _, future = start(bridge)
    future.result(timeout=8)
    assert "spawn_subagent" not in {item["function"]["name"] for item in state["requests"][0]["tools"]}


def test_subagent_failure_is_returned_without_terminating_parent(bridge):
    service, state, roots = bridge
    async def script(iteration, messages):
        if "Araştırma uzmanısın" in messages[0].content:
            raise RuntimeError("alt sağlayıcı hatası")
            yield
        if iteration == 1:
            yield StreamChunk(tool_calls=[tool("spawn_subagent", role="review", task="main.py incele")])
        elif iteration == 2:
            spawned = next(json.loads(m.content) for m in messages if m.name == "spawn_subagent")
            yield StreamChunk(tool_calls=[tool("wait_subagent", agent_id=spawned["agent_id"])])
        else:
            result = next(json.loads(m.content) for m in messages if m.name == "wait_subagent")
            assert result["status"] == "failed" and result["passed"] is False
            yield StreamChunk(delta_content="İnceleme başarısız oldu.")
    state["script"] = script
    errors = []
    _, future = start(bridge, options={"subagents_enabled": True}, on_error=errors.append)
    future.result(timeout=8)
    assert not errors
    record = service.coding_store.latest_run("p1")
    assert record.decisions[-1]["status"] == "failed"


def test_real_agent_approval_edit_diff_test_evidence_provider_and_sqlite_history(bridge, python_command):
    service, state, roots = bridge
    path = roots["p1"] / "main.py"
    command = python_command("-c", "from pathlib import Path; assert 'yeni' in Path('main.py').read_text(encoding='utf-8')")
    plan = [{"id": "s1", "title": "Değeri güncelle", "acceptance_criteria": ["Yeni değer okunur"], "verification": command}]
    async def script(iteration, messages):
        if iteration == 1:
            yield StreamChunk(tool_calls=[tool("update_plan", steps=plan), tool("read_file", path="main.py")])
        elif iteration == 2:
            read = next(json.loads(m.content) for m in reversed(messages) if m.role == "tool" and m.name == "read_file")
            yield StreamChunk(tool_calls=[tool("edit_file", path="main.py", expected_hash=read["hash"], old_text="'eski'", new_text="'yeni'", step_id="s1"),
                                          tool("run_tests", command=command)])
        elif iteration == 3:
            evidence = next(json.loads(m.content) for m in reversed(messages) if m.role == "tool" and m.name == "run_tests")
            yield StreamChunk(tool_calls=[tool("update_plan", steps=[{**plan[0], "status": "completed", "evidence_ids": [evidence["evidence_id"]]}])])
        else:
            yield StreamChunk(delta_content="Dosya ve gerçek test tamamlandı.", usage={"total_tokens": 15})
    state["script"] = script
    approvals, events, errors = [], [], []
    def receive(event):
        events.append(event)
        if event.type == AgentEventType.APPROVAL_REQUIRED:
            approvals.append(event.metadata["tool_name"])
            if event.metadata["tool_name"] == "edit_file":
                assert "eski" in path.read_text(encoding="utf-8")
            assert service.coding_control(event.session_id, "approve", event.metadata["request_id"], True)
    session, future = start(bridge, mode="approved", on_event=receive, on_error=errors.append)
    future.result(timeout=8)
    assert not errors
    assert approvals == ["edit_file", "run_tests"]
    assert "yeni" in path.read_text(encoding="utf-8")
    record = service.coding_store.latest_run("p1")
    assert record.status == "completed" and record.provider == "local"
    assert record.tests_executed[0]["exit_code"] == 0 and record.tests_executed[0]["executed"] is True
    assert "-value = 'eski'" in record.file_journal[0]["diff"]
    assert record.file_journal[0]["after_hash"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert json.loads(record.plan)[0]["status"] == "completed"
    assert record.completed_steps[0]["id"] == "s1"
    assert len(record.decisions) == 2 and record.tokens_used == 15
    assert state["requests"][0]["provider"] == "local"
    assert all(e.project_id == "p1" and e.task_id == "t-p1" for e in events)
    assert all(e.metadata["run_id"] == record.run_id for e in events)
    assert not service._streams and session.session_id not in service._coding_controls
    restored, restored_record = service.restore_coding("p1")
    assert restored.session_id == session.session_id and restored.messages[-1].content == "Dosya ve gerçek test tamamlandı."
    assert restored_record.id == record.id
    undone, undo_errors = [], []
    service.undo_coding_async(session.session_id, "main.py", undone.append, undo_errors.append).result(timeout=4)
    assert not undo_errors and undone[0]["undone"]
    assert "eski" in path.read_text(encoding="utf-8")
    saved_undo = service.coding_store.get_run(record.id)
    assert saved_undo.file_journal[0]["undone"] is True
    assert saved_undo.decisions[-1]["kind"] == "undo"
    assert saved_undo.checkpoints[-1]["kind"] == "undo"


def test_first_user_partial_and_plan_are_durable_while_model_is_blocked(bridge):
    service, state, roots = bridge
    partial = threading.Event()
    async def script(iteration, messages):
        if iteration == 1:
            yield StreamChunk(tool_calls=[tool("update_plan", steps=[{"id": "s1", "title": "İncele", "acceptance_criteria": ["Okunur"], "verification": "read_file"}])])
        else:
            yield StreamChunk(delta_content="Yarım yanıt")
            while True:
                await asyncio.sleep(0.01)
    state["script"] = script
    def receive(event):
        if event.type == AgentEventType.TEXT_DELTA:
            partial.set()
    session, future = start(bridge, mode="plan", on_event=receive)
    assert partial.wait(4)
    reopened = CodingSessionStore(ProjectDatabase(service.projects.db.db_path))
    record = reopened.latest_run("p1")
    assert record.status in {"planning", "running"}
    assert record.messages[0]["role"] == "user"
    assert record.messages[-1]["content"] == "Yarım yanıt"
    assert record.messages[-1]["partial"] is True
    assert json.loads(record.plan)[0]["id"] == "s1"
    assert record.checkpoints and record.context_report
    service.coding_control(session.session_id, "cancel")
    with pytest.raises(CancelledError):
        future.result(timeout=3)


def test_cancel_blocked_inference_releases_stream_and_ignores_late_done(bridge):
    service, state, roots = bridge
    running, finished = threading.Event(), threading.Event()
    done, errors, terminal = [], [], []
    async def script(iteration, messages):
        running.set()
        try:
            while True:
                await asyncio.sleep(0.01)
        finally:
            state["model_cancelled"] = True
        yield StreamChunk(delta_content="Should never arrive")
    state["script"] = script
    session, future = start(bridge, on_done=lambda text: (done.append(text), finished.set()), on_error=errors.append,
                            on_event=lambda event: terminal.append(event) if event.type == AgentEventType.DONE else None)
    assert running.wait(4)
    assert service.coding_control(session.session_id, "cancel")
    with pytest.raises(CancelledError):
        future.result(timeout=3)
    assert finished.wait(4)
    assert state["model_cancelled"] and not errors and len(done) == 1 and not terminal
    assert service.coding_store.latest_run("p1").status == "cancelled"
    assert not service._streams and not service._coding_controls


def test_two_project_models_events_messages_and_cancellation_are_isolated(bridge):
    service, state, roots = bridge
    gates = {"model-a": threading.Event(), "model-b": threading.Event()}
    started = {name: threading.Event() for name in gates}
    async def script(iteration, messages):
        text = next(m.content for m in reversed(messages) if m.role == "user")
        model = "model-a" if text == "A" else "model-b"
        started[model].set()
        yield StreamChunk(delta_content=text)
        while not gates[model].is_set():
            await asyncio.sleep(0.01)
        yield StreamChunk(delta_content=" bitti")
    state["script"] = script
    a, b = service.get_or_create_session(), service.get_or_create_session()
    a.model, b.model = "model-a", "model-b"
    events = []
    _, first = start(bridge, session=a, project="p1", prompt="A", on_event=events.append)
    _, second = start(bridge, session=b, project="p2", prompt="B", on_event=events.append)
    assert started["model-a"].wait(4) and started["model-b"].wait(4)
    with pytest.raises(ValueError, match="zaten"):
        start(bridge, session=b, project="p2", prompt="duplicate")
    gates["model-b"].set()
    second.result(timeout=4)
    assert not first.done()
    assert service.coding_store.latest_run("p2").messages[-1]["content"] == "B bitti"
    service.coding_control(a.session_id, "cancel")
    with pytest.raises(CancelledError):
        first.result(timeout=3)
    assert all(event.project_id == ("p1" if event.session_id == a.session_id else "p2") for event in events)
    assert service.coding_store.latest_run("p1").session_id == a.session_id
    assert service.coding_store.latest_run("p2").session_id == b.session_id


def test_setup_and_final_persistence_errors_release_stream_and_report_truth(bridge, monkeypatch):
    service, state, roots = bridge
    errors, finished = [], threading.Event()
    original = service.coding_store.create_run
    monkeypatch.setattr(service.coding_store, "create_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db unavailable")))
    session, future = start(bridge, on_error=lambda text: (errors.append(text), finished.set()))
    future.result(timeout=4)
    assert finished.wait(3) and "db unavailable" in errors[0]
    assert not service._streams and not service._coding_controls
    monkeypatch.setattr(service.coding_store, "create_run", original)
    update = service.coding_store.update_run
    def fail_final(run_id, **fields):
        if "result" in fields:
            raise RuntimeError("disk full")
        return update(run_id, **fields)
    monkeypatch.setattr(service.coding_store, "update_run", fail_final)
    done, errors = [], []
    _, future = start(bridge, session=session, on_done=done.append, on_error=errors.append)
    future.result(timeout=4)
    assert not done and "saklanamadı" in errors[0]
    assert not service._streams and not service._coding_controls


def test_done_event_starts_next_turn_only_after_old_stream_cleanup(bridge):
    service, state, roots = bridge
    session = service.get_or_create_session()
    following = []
    def receive(event):
        if event.type == AgentEventType.DONE:
            following.append(start(bridge, session=session, prompt="İkinci istek")[1])
    _, first = start(bridge, session=session, on_event=receive)
    first.result(timeout=4)
    assert len(following) == 1
    following[0].result(timeout=4)
    assert len(service.coding_store.list_runs("p1", session_id=session.session_id)) == 2
    assert not service._streams


def test_prompt_prior_history_json_tool_arguments_and_export_are_redacted_before_model(bridge):
    service, state, roots = bridge
    session = service.get_or_create_session()
    session.system_prompt = "password=system-secret"
    session.messages = [Message(role="assistant", tool_calls=[tool("example", api_key="json-secret")]),
                        Message(role="tool", tool_call_id="call_example", content="API_KEY=history-secret")]
    _, future = start(bridge, session=session, prompt="FIRIK_API_KEY=prompt-secret")
    future.result(timeout=4)
    request = json.dumps(state["requests"], ensure_ascii=False)
    export = service.coding_store.export_json(service.coding_store.latest_run("p1").id)
    for secret in ["system-secret", "history-secret", "prompt-secret", "json-secret"]:
        assert secret not in request and secret not in export


def test_resume_revalidates_actual_hash_and_keeps_concurrent_user_edit(bridge):
    service, state, roots = bridge
    session = service.get_or_create_session()
    source = roots["p1"] / "main.py"
    previous = service.coding_store.create_run("p1", task_id="t-p1", session_id=session.session_id)
    service.coding_store.record_file_change(previous.id, {"path": "main.py", "after_hash": hashlib.sha256(source.read_bytes()).hexdigest()})
    service.coding_store.finish_run(previous.id, "interrupted")
    source.write_text("# Kullanıcının yeni düzenlemesi\n", encoding="utf-8", newline="\n")
    _, future = start(bridge, session=session)
    future.result(timeout=4)
    resumed = service.coding_store.latest_run("p1")
    checkpoint = next(c for c in resumed.checkpoints if c.get("kind") == "resume_revalidation")
    assert resumed.resumed_from == previous.id and checkpoint["files"][0]["matches"] is False
    assert "değişen dosyaları yeniden oku" in state["requests"][0]["messages"][0]["content"]
    assert source.read_text(encoding="utf-8") == "# Kullanıcının yeni düzenlemesi\n"


def test_project_legacy_denials_override_selected_tool_permissions(bridge):
    service, state, roots = bridge
    settings = service.projects.db.get_settings("p1")
    settings.permissions.update(network_access="never", write_files="deny")
    service.projects.db.save_settings(settings)
    _, future = start(bridge)
    future.result(timeout=4)
    record = service.coding_store.latest_run("p1")
    assert record.permissions["allow_edits"] is False
    assert record.permissions["allow_terminal"] is False and record.permissions["allow_tests"] is False
    tools = {value["function"]["name"] for value in state["requests"][0]["tools"]}
    assert "write_file" not in tools and "run_tests" not in tools


def test_skill_metadata_is_prepared_off_ui_and_selected_body_is_loaded(bridge):
    service, state, roots = bridge
    folder = roots["p1"] / "skills" / "turkce"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: turkce\ndescription: Türkçe metin denetimi\n---\nSeçili beceri talimatı", encoding="utf-8", newline="\n")
    result, finished = [], threading.Event()
    session = service.get_or_create_session()
    future = service.prepare_coding_async("p1", "İncele", lambda data: (result.append(data), finished.set()),
                lambda error: (result.append(error), finished.set()), session_id=session.session_id, selected_skills=["turkce"])
    future.result(timeout=4)
    assert finished.wait(3)
    assert result[0]["selected_skills"] == ["turkce"]
    assert "Seçili beceri talimatı" in result[0]["system_prompt"]
    assert any(skill["name"] == "turkce" for skill in result[0]["skill_catalogue"])


@pytest.mark.parametrize("metadata, expected_window, expected_tools", [
    ({"context_window": 128000, "supports_tools": True}, None, None),
    ({"verified": True, "context_window": 32000, "supports_tools": False}, 32000, False),
    ({"source": "provider", "context_window": 48000, "supports_tools": True}, 48000, True),
    ({"source": "cached_guess", "context_window": 999999, "supports_tools": True}, None, None),
    ({"source": "provider", "supports_tools": False}, None, False),
])
def test_prepare_capabilities_use_only_explicit_selected_provider_model_config(bridge, metadata, expected_window, expected_tools):
    service, state, roots = bridge
    session = service.get_or_create_session()
    session.model = "selected-model"
    settings = service.projects.db.get_settings("p1")
    settings.coding_preferences["provider"] = "local"
    service.projects.db.save_settings(settings)
    service.config["providers"] = {
        "firik": {"models": [{"id": "selected-model", "source": "provider", "context_window": 1000000, "supports_tools": True}]},
        "local": {"models": [{"id": "different-model", "source": "provider", "context_window": 2000000},
                              {"id": "selected-model", **metadata}]},
    }
    values = []
    future = service.prepare_coding_async("p1", "İncele", values.append, values.append,
        session_id=session.session_id, available_skills=[])
    future.result(timeout=4)
    assert isinstance(values[0], dict)
    assert values[0]["capabilities"]["context_window"] == expected_window
    assert values[0]["capabilities"]["supports_tools"] is expected_tools
    assert values[0]["context_budget"]["capacity_known"] is (expected_window is not None)


@pytest.mark.parametrize("configured", [None,
    [{"id": "other", "source": "provider", "context_window": 128000}],
    [ModelInfo(id="unlisted-model", name="Default-valued metadata")],
])
def test_prepare_missing_selected_model_keeps_capabilities_unknown(bridge, configured):
    service, state, roots = bridge
    session = service.get_or_create_session()
    session.model = "unlisted-model"
    service.config["providers"]["firik"]["models"] = configured
    values = []
    service.prepare_coding_async("p1", "İncele", values.append, values.append,
        session_id=session.session_id, available_skills=[]).result(timeout=4)
    assert values[0]["capabilities"]["context_window"] is None
    assert values[0]["capabilities"]["supports_tools"] is None


@pytest.mark.parametrize("mode, enabled, selected, server_policy, expected", [
    ("ask", True, ["mcp_selected_read"], "allow", []),
    ("plan", True, ["mcp_selected_read"], "allow", []),
    ("auto", False, ["mcp_selected_read"], "allow", []),
    ("approved", True, [], "allow", []),
    ("auto", True, ["builtin_example"], "allow", []),
    ("auto", True, ["mcp_other_read"], "allow", []),
    ("auto", True, ["mcp_selected_read"], "deny", []),
    ("approved", True, ["mcp_selected_read"], "allow", ["selected"]),
])
def test_mcp_startup_requires_application_mode_permission_exact_tool_and_server_policy(
        bridge, monkeypatch, mode, enabled, selected, server_policy, expected):
    from types import SimpleNamespace
    service, state, roots = bridge
    registry = service.mcp_manager.tool_registry
    configs = {name: SimpleNamespace(config=MCPServerConfig(name=name, command="fake", enabled=True,
                         tool_policy=ToolPolicy(mode=ToolPolicyMode(server_policy) if name == "selected" else ToolPolicyMode.ALLOW)))
               for name in ("selected", "other", "unselected")}
    for name in configs:
        registry.register(ToolDefinition(name=f"mcp_{name}_read", description="Safe test schema", parameters={}, source=f"mcp:{name}"), lambda: "read")
    registry.register(ToolDefinition(name="builtin_example", description="Builtin", parameters={}, source="builtin"), lambda: "builtin")
    started, released = [], []
    async def acquire(session_id, name):
        started.append(name)
        return True
    async def release(session_id, name):
        released.append(name)
    monkeypatch.setattr(service.mcp_manager, "get_connection", configs.get)
    monkeypatch.setattr(service.mcp_manager, "acquire_for_session", acquire)
    monkeypatch.setattr(service.mcp_manager, "release_for_session", release)
    session, future = start(bridge, mode=mode, options={"permissions": {"allow_mcp": enabled},
        "mcp_servers": ["selected", "unselected"], "mcp_tools": selected})
    future.result(timeout=4)
    assert started == released == expected
    assert session.active_mcp_servers == set(expected)
    schemas = {value["function"]["name"] for value in state["requests"][0]["tools"]}
    assert ("mcp_selected_read" in schemas) is bool(expected)
    assert "mcp_other_read" not in schemas and "mcp_unselected_read" not in schemas
