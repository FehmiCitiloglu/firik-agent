"""MCP failures preserve evidence and the conversation without replaying effects."""
import asyncio
import json
import threading

import httpx
import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType as E
from firik_agent.core.failures import FailureKind as F, RequestPhase as P, RuntimeFailure
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction, ToolDefinition
from firik_agent.mcp.connection import MCPConnection, MCPConnectionManager
from firik_agent.mcp.manager import MCPManager
from firik_agent.mcp.models import MCPServerConfig
from firik_agent.mcp.protocol import MCPCallToolResult, MCPContentItem, MCPToolSchema
from firik_agent.mcp.results import tool_result_from_mcp
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter


def call(name="mcp_remote_effect", arguments=None, identifier="call1"):
    return ToolCall(id=identifier, function=ToolCallFunction(
        name=name, arguments=json.dumps(arguments or {})))


def response(*calls, content=None):
    return Message(role="assistant", content=content, tool_calls=list(calls) or None)


class ScriptedProvider:
    name, current_model, model_metadata = "fixture", "offline", {}

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def next(self, kwargs):
        # Preserve what the provider actually received, before later turns
        # append messages or mutate host execution metadata.
        snapshot = {**kwargs, "messages": [m.model_copy(deep=True) for m in kwargs["messages"]]}
        self.calls.append(snapshot)
        step = self.steps.pop(0)
        if callable(step):
            step = step(snapshot)
        if isinstance(step, BaseException):
            raise step
        return step

    async def chat(self, **kwargs):
        return self.next(kwargs)

    async def chat_stream(self, **kwargs):
        value = self.next(kwargs)
        yield StreamChunk(delta_content=value.content, tool_calls=value.tool_calls)


def runtime(steps, **limits):
    agent = Agent(config={
        "agent": {"context_compaction": {"enabled": False},
                  "recovery": {"base_delay": 0, "overload_base_delay": 0, **limits}},
        "providers": {}, "plugins": {}, "skills": {}, "self_improvement": {"mode": "off"},
    })
    agent._is_initialized = True
    provider = ScriptedProvider(steps)
    agent.providers.register("fixture", provider, set_active=True)
    return agent, provider, ChatSession(learning_enabled=False)


async def run(agent, session, streaming, **kwargs):
    events = []
    if streaming:
        events = [event async for event in agent.run_stream("task", session=session, **kwargs)]
        result = next((event.content for event in reversed(events) if event.type == E.DONE), None)
    else:
        result = await agent.run("task", session=session, on_event=events.append, **kwargs)
    return result, events


def remote_error():
    return tool_result_from_mcp(MCPCallToolResult(isError=True, content=[
        MCPContentItem(type="text", text="PRIVATE-REMOTE-BODY")]), "effect")


async def register_mock_servers(agent, monkeypatch, connection_owner, dispatched):
    schema = MCPToolSchema(name="read", description="remote source", inputSchema={
        "type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]})

    class Client:
        def __init__(self, name="remote", **kwargs):
            self.name = name

        async def connect(self):
            pass

        async def list_tools(self):
            return [schema]

        async def call_tool(self, name, arguments):
            dispatched.append((self.name, arguments["index"]))
            failed = self.name == "remote" and arguments["index"] == 10
            text = "PRIVATE-REMOTE-BODY" if failed else f"Article-{self.name}-{arguments['index']}"
            return MCPCallToolResult(isError=failed, content=[MCPContentItem(type="text", text=text)])

    if connection_owner:
        owner = MCPConnectionManager(agent.tools)
        for name in ("remote", "backup"):
            connection = MCPConnection(MCPServerConfig(name=name, command="fake"))
            connection.cached_tools = [schema]
            connection.call_tool = Client(name).call_tool
            owner._register_tools_in_registry(connection)
    else:
        monkeypatch.setattr("firik_agent.mcp.manager.MCPClient", Client)
        for name in ("remote", "backup"):
            await agent.mcp.add_server(name, command="fake")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("connection_owner", [False, True])
@pytest.mark.asyncio
async def test_reported_error_preserves_ten_sources_and_completes_batch(
        streaming, connection_owner, monkeypatch):
    def summarize(request):
        tool_messages = [message for message in request["messages"] if message.role == "tool"]
        assert len(tool_messages) == 12
        for index in range(10):
            assert f"Article-remote-{index}" in tool_messages[index].content
        assert tool_messages[10].execution["reconciliation_status"] == "required"
        assert "Article-backup-11" in tool_messages[11].content
        assert "PRIVATE-REMOTE-BODY" not in str([message.to_dict() for message in request["messages"]])
        return response(content="Ten sources and the backup support this summary; one source failed.")

    agent, provider, session = runtime([
        response(*(call("mcp_remote_read", {"index": index}, f"read-{index}") for index in range(10))),
        response(call("mcp_remote_read", {"index": 10}, "failed"),
                 call("mcp_backup_read", {"index": 11}, "backup")),
        summarize,
    ])
    dispatched = []
    await register_mock_servers(agent, monkeypatch, connection_owner, dispatched)
    result, events = await run(agent, session, streaming)
    assert result == "Ten sources and the backup support this summary; one source failed."
    assert len(provider.calls) == 3 and len(dispatched) == 12
    assert events[-1].type == E.DONE
    assert not any(event.type == E.ERROR for event in events)
    assert sum(event.type == E.TOOL_CALL_ERROR for event in events) == 1
    assert sum(event.type == E.TOOL_CALL_RESULT for event in events) == 11


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_desktop_filter_uses_separate_mcp_registry_for_continuation(streaming):
    agent, provider, session = runtime([
        response(call(), call("mcp_remote_read", identifier="independent")),
        response(content="The independent result is available despite the failed call."),
    ])
    mcp_registry = ToolRegistry()
    owner = MCPConnectionManager(mcp_registry)
    connection = MCPConnection(MCPServerConfig(name="remote", command="fake"))
    connection.cached_tools = [MCPToolSchema(name=name, description="remote tool", inputSchema={"type": "object"})
                               for name in ("effect", "read")]
    dispatched = []

    async def call_remote(name, arguments):
        dispatched.append(name)
        return MCPCallToolResult(isError=name == "effect", content=[
            MCPContentItem(type="text", text="PRIVATE-REMOTE-BODY" if name == "effect" else "independent result")])

    connection.call_tool = call_remote
    owner.connections["remote"] = connection
    owner._register_tools_in_registry(connection)
    session.enable_mcp("remote")
    tool_filter = SessionToolFilter(agent.tools, connection_manager=owner, mcp_registry=mcp_registry)
    result, events = await run(agent, session, streaming, session_tool_filter=tool_filter)
    assert result.startswith("The independent result")
    assert dispatched == ["effect", "read"] and len(provider.calls) == 2
    assert not agent.tools.get_tool("mcp_remote_effect")
    assert events[-1].type == E.DONE and not any(event.type == E.ERROR for event in events)
    failed = next(event for event in events if event.type == E.TOOL_CALL_ERROR)
    assert failed.metadata["execution_boundary"] == "remote_mcp"
    assert failed.metadata["continuation_allowed"] is True


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("error_type", [TimeoutError, httpx.ConnectError])
@pytest.mark.asyncio
async def test_transport_failure_and_rejected_replay_allow_independent_work(streaming, error_type):
    agent, provider, session = runtime([
        response(call()),
        response(call(identifier="repeat"), call("mcp_backup_read", identifier="independent")),
        response(content="Backup information remains available; the first operation was not confirmed."),
    ])
    effects = []

    def effect():
        effects.append("dispatched")
        raise error_type("PRIVATE-REMOTE-BODY")

    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), effect)
    agent.tools.register(ToolDefinition(name="mcp_backup_read", description="backup", source="mcp:backup"),
                         lambda: "independent evidence")
    result, events = await run(agent, session, streaming)
    assert result.startswith("Backup information")
    assert effects == ["dispatched"] and len(provider.calls) == 3
    assert events[-1].type == E.DONE and not any(event.type == E.ERROR for event in events)
    errors = [event for event in events if event.type == E.TOOL_CALL_ERROR]
    assert len(errors) == 2
    assert errors[0].metadata["reconciliation_status"] == "required"
    assert errors[1].metadata["request_phase"] == P.TOOL_NOT_DISPATCHED.value
    assert errors[1].metadata["replay_blocked"] is True
    assert "PRIVATE-REMOTE-BODY" not in str([message.to_dict() for message in session.messages])


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_repeated_uncertain_operation_switches_to_bounded_synthesis(streaming):
    def summarize(request):
        assert not request["tools"]
        return response(content="The operation failed and was not repeated; here is the partial result.")

    agent, provider, session = runtime([
        response(call(identifier="initial")),
        response(call(identifier="repeat-1")),
        response(call(identifier="repeat-2")),
        summarize,
    ])
    dispatched = []

    def effect():
        dispatched.append(1)
        return remote_error()

    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), effect)
    result, events = await run(agent, session, streaming)
    assert result.startswith("The operation failed")
    assert dispatched == [1] and len(provider.calls) == 4
    assert events[-1].type == E.DONE
    assert sum(event.type == E.TOOL_CALL_ERROR for event in events) == 3
    assert not any(event.type == E.ERROR for event in events)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_error_at_iteration_limit_gets_one_synthesis_request(streaming):
    def summarize(request):
        assert not request["tools"]
        assert any(message.role == "tool" for message in request["messages"])
        return response(content="The retrieved information is incomplete because one source failed.")

    agent, provider, session = runtime([response(call()), summarize])
    agent.max_iterations = 1
    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), remote_error)
    result, events = await run(agent, session, streaming)
    assert result.startswith("The retrieved information")
    assert len(provider.calls) == 2 and events[-1].type == E.DONE


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_synthesis_refuses_unadvertised_tool_calls(streaming):
    agent, provider, session = runtime([
        response(call()), response(call("mcp_backup_mutation", identifier="must-not-run")),
    ])
    agent.max_iterations = 1
    effects = []
    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), remote_error)
    agent.tools.register(ToolDefinition(name="mcp_backup_mutation", description="mutation", source="mcp:backup"),
                         lambda: effects.append("unsafe synthesis execution"))
    result, events = await run(agent, session, streaming)
    assert not provider.calls[-1]["tools"]
    assert not effects and len(provider.calls) == 2
    assert result and events[-1].type == E.DONE
    assert not session.messages[-1].tool_calls


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_synthesis_cannot_exceed_model_request_budget(streaming):
    agent, provider, session = runtime([response(call())], max_requests=1)
    agent.max_iterations = 1
    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), remote_error)
    if streaming:
        _, events = await run(agent, session, True)
        assert events[-1].type == E.ERROR
        assert events[-1].metadata["failure_kind"] == F.BUDGET_EXCEEDED.value
    else:
        with pytest.raises(RuntimeFailure) as error:
            await run(agent, session, False)
        assert error.value.kind == F.BUDGET_EXCEEDED
    assert len(provider.calls) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_host_mutation_unknown_outcome_still_stops(streaming):
    agent, provider, session = runtime([response(call("host_effect"))])

    def effect():
        raise TimeoutError("unconfirmed host mutation")

    agent.tools.register(ToolDefinition(name="host_effect", description="host mutation"), effect)
    if streaming:
        _, events = await run(agent, session, True)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(RuntimeFailure) as error:
            await run(agent, session, False)
        assert error.value.kind == F.TOOL_OUTCOME_UNKNOWN
    assert len(provider.calls) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", [F.BUDGET_EXCEEDED, F.CANCELLED, F.PERMISSION_DENIED,
                                  F.POLICY_REFUSAL, F.AUTHENTICATION_FAILED])
@pytest.mark.asyncio
async def test_protected_mcp_failures_remain_terminal(streaming, kind):
    agent, provider, session = runtime([response(call())])

    def effect():
        raise RuntimeFailure(kind, phase=P.TOOL_DISPATCHED)

    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), effect)
    if streaming:
        _, events = await run(agent, session, True)
        assert events[-1].type == E.ERROR
        assert events[-1].metadata["failure_kind"] == kind.value
    else:
        with pytest.raises(RuntimeFailure) as error:
            await run(agent, session, False)
        assert error.value.kind == kind
    assert len(provider.calls) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", [F.PERMISSION_DENIED, F.AUTHENTICATION_FAILED])
@pytest.mark.asyncio
async def test_terminal_mcp_batch_keeps_skipped_calls_paired_for_next_run(streaming, kind):
    def next_answer(request):
        advertised = [tool_call.id for message in request["messages"] for tool_call in message.tool_calls or []]
        results = [message.tool_call_id for message in request["messages"] if message.role == "tool"]
        assert advertised == results == ["denied", "must-not-run"]
        return response(content="The previous protected failure remains recorded; this new turn can continue.")

    agent, provider, session = runtime([
        response(call(identifier="denied"), call("mcp_backup_mutation", identifier="must-not-run")),
        next_answer,
    ])
    effects = []

    def denied():
        if kind == F.PERMISSION_DENIED:
            raise PermissionError("operation denied")
        raise RuntimeFailure(kind, phase=P.TOOL_DISPATCHED)

    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), denied)
    agent.tools.register(ToolDefinition(name="mcp_backup_mutation", description="mutation", source="mcp:backup"),
                         lambda: effects.append("must not dispatch"))
    if streaming:
        _, events = await run(agent, session, True)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(RuntimeFailure) as error:
            await run(agent, session, False)
        assert error.value.kind == kind
    assert not effects and len(provider.calls) == 1
    tool_messages = [message for message in session.messages if message.role == "tool"]
    assert [message.tool_call_id for message in tool_messages] == ["denied", "must-not-run"]
    assert tool_messages[1].execution["request_phase"] == P.TOOL_NOT_DISPATCHED.value
    assert tool_messages[1].execution["status"] == "not_dispatched"
    result, events = await run(agent, session, streaming)
    assert result.startswith("The previous protected failure remains recorded")
    assert events[-1].type == E.DONE and not effects
    assert len(provider.calls) == 2


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", [F.BUDGET_EXCEEDED, F.CANCELLED])
@pytest.mark.asyncio
async def test_terminal_budget_or_cancel_after_effect_still_blocks_next_run_replay(streaming, kind):
    agent, provider, session = runtime([response(call())])
    effects = []

    def effect():
        effects.append("remote mutation")
        raise RuntimeFailure(kind, phase=P.TOOL_DISPATCHED)

    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), effect)
    if streaming:
        _, events = await run(agent, session, True)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(RuntimeFailure):
            await run(agent, session, False)
    attempt = next(message.execution["tool_attempts"][0] for message in session.messages
                   if message.execution.get("tool_attempts"))
    assert attempt["status"] == "outcome_unknown"
    assert attempt["reconciliation_status"] == "required"
    provider.steps.extend([response(call(identifier="next-run")), response(content="The earlier outcome remains uncertain.")])
    result, events = await run(agent, session, streaming)
    assert effects == ["remote mutation"]
    assert result == "The earlier outcome remains uncertain."
    assert events[-1].type == E.DONE
    error = next(event for event in events if event.type == E.TOOL_CALL_ERROR)
    assert error.metadata["replay_blocked"] is True
    assert error.metadata["request_phase"] == P.TOOL_NOT_DISPATCHED.value


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_user_cancellation_prevents_continuation_after_mcp_error(streaming):
    agent, provider, session = runtime([response(call())])
    cancel = threading.Event()

    def effect():
        cancel.set()
        return remote_error()

    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), effect)
    if streaming:
        _, events = await run(agent, session, True, cancel_event=cancel)
        assert events[-1].type == E.DONE
    else:
        with pytest.raises(asyncio.CancelledError):
            await run(agent, session, False, cancel_event=cancel)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_coding_completion_keeps_unresolved_mcp_operation_blocked(streaming, tmp_path):
    agent, provider, session = runtime([
        response(call()), response(content="I can report partial evidence; the operation remains unresolved."),
    ])
    session.enable_mcp("remote")
    agent.tools.register(ToolDefinition(name="mcp_remote_effect", description="remote", source="mcp:remote"), remote_error)
    control = CodingRunControl(session.session_id, "project", mode="auto",
                               permissions={"allow_mcp": True}, policy={"mcp": "allow"})
    def approve(event):
        if event.type == E.APPROVAL_REQUIRED:
            control.decide_approval(event.metadata["request_id"], True)
    control.on_event = approve
    tool_filter = CodingToolFilter(tmp_path, control, mcp_registry=agent.tools,
                                   trusted_mcp_tools={"mcp_remote_effect"})
    kwargs = {"run_control": control, "session_tool_filter": tool_filter}
    if streaming:
        _, events = await run(agent, session, True, **kwargs)
        assert events[-1].type == E.ERROR
        assert not any(event.type == E.DONE for event in events)
    else:
        with pytest.raises(RuntimeError, match="Completion blocked"):
            await run(agent, session, False, **kwargs)
    assert control.unresolved_operations and control.state == "blocked"
    assert len(provider.calls) == 2
    assert session.messages[-1].content.startswith("I can report partial evidence")
