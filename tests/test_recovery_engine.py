"""Policy and real Agent paths with deterministic providers; no network or sleeps >50ms."""
import asyncio
import threading

import httpx
import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.chat_history import ChatHistoryStore
from firik_agent.core.events import AgentEventType as E
from firik_agent.core.failures import FailureKind as F, RequestPhase as P, RuntimeFailure
from firik_agent.core.recovery import (AdaptiveRecoveryEngine, RecoveryAction as A, RecoveryBudget,
    RecoveryContext, RecoveryLimits, SideEffectRisk as S, tool_semantics)
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction, ToolDefinition, ToolResult
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter


def call(name="effect", arguments="{}", identifier="call1"):
    return ToolCall(id=identifier, function=ToolCallFunction(name=name, arguments=arguments))


class Scripted:
    name, current_model, model_metadata = "fixture", "offline", {}

    def __init__(self, steps):
        self.steps, self.calls, self.chat_calls = list(steps), [], 0

    def next(self, kwargs):
        self.calls.append(kwargs)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    async def chat(self, **kwargs):
        self.chat_calls += 1
        return self.next(kwargs)

    async def chat_stream(self, **kwargs):
        step = self.next(kwargs)
        if isinstance(step, list):
            for part in step:
                if isinstance(part, BaseException):
                    raise part
                yield part
        elif isinstance(step, Message):
            yield StreamChunk(delta_content=step.content, tool_calls=step.tool_calls, usage=step.usage)
        else:
            yield step


def runtime(steps, **limits):
    agent = Agent(config={"agent": {"context_compaction": {"enabled": False},
                                   "recovery": {"base_delay": 0, **limits}},
                          "providers": {}, "plugins": {}, "skills": {}})
    agent._is_initialized = True
    provider = Scripted(steps)
    agent.providers.register("fixture", provider, set_active=True)
    return agent, provider, ChatSession()


async def stream(agent, session, **kwargs):
    return [event async for event in agent.run_stream("task", session=session, **kwargs)]


def context(kind, phase=P.SUBMITTED_NO_OUTPUT, risk=S.PURE_READ, **kwargs):
    budget = RecoveryBudget(RecoveryLimits(base_delay=0))
    return RecoveryContext("run", "attempt", kind, phase, risk, {}, budget, **kwargs)


@pytest.mark.parametrize("kind,action", [(F.AUTHENTICATION_FAILED,A.INTERVENE), (F.PERMISSION_DENIED,A.STOP),
    (F.POLICY_REFUSAL,A.STOP), (F.CONTEXT_OVERFLOW,A.INTERVENE), (F.UNSUPPORTED_CAPABILITY,A.REFRESH_CAPABILITIES),
    (F.INVALID_TOOL_ARGUMENTS,A.FAIL), (F.BUDGET_EXCEEDED,A.FAIL), (F.CANCELLED,A.STOP),
    (F.WORKSPACE_CONFLICT,A.RECONCILE)])
def test_explicit_policy_decisions(kind, action):
    assert AdaptiveRecoveryEngine().decide(context(kind)).action == action


def test_jitter_and_retry_after_and_limits():
    engine = AdaptiveRecoveryEngine(RecoveryLimits(base_delay=2, request_retries=2), jitter=lambda: 0)
    first = engine.decide(context(F.RATE_LIMITED, retry_after=3))
    assert first.delay == 3 and first.safe_to_repeat
    assert engine.decide(context(F.PROVIDER_OVERLOADED, attempts_used=1)).delay == 2
    assert engine.decide(context(F.PROVIDER_OVERLOADED, attempts_used=2)).action == A.FAIL
    assert engine.decide(context(F.RATE_LIMITED, retry_after=99999)).action == A.INTERVENE


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("kind", [F.RATE_LIMITED, F.PROVIDER_OVERLOADED])
@pytest.mark.asyncio
async def test_429_retry_after_and_503_recovery_both_paths(streaming, kind):
    agent, provider, session = runtime([RuntimeFailure(kind, retry_after=.001), Message(role="assistant", content="done")])
    events = []
    if streaming:
        events = await stream(agent, session)
        assert events[-1].type == E.DONE
    else:
        assert await agent.run("task", session=session, on_event=events.append) == "done"
    assert len(provider.calls) == 2
    recovery, = [event for event in events if event.type == E.RECOVERY]
    assert recovery.metadata["safe_to_repeat"] and recovery.metadata["delay"] >= .001
    finishes = [event for event in events if event.type == E.MODEL_REQUEST_FINISHED]
    assert finishes[1].metadata["parent_request_id"] == finishes[0].metadata["request_id"]


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("kind", [F.AUTHENTICATION_FAILED,F.PERMISSION_DENIED,F.POLICY_REFUSAL,F.UNSUPPORTED_CAPABILITY])
@pytest.mark.asyncio
async def test_denials_and_schema_failures_never_switch_or_replay(streaming, kind):
    agent, provider, session = runtime([RuntimeFailure(kind)])
    if streaming:
        events = await stream(agent, session)
        assert events[-1].type == E.ERROR
    else:
        with pytest.raises(RuntimeFailure) as caught:
            await agent.run("task", session=session)
        assert caught.value.kind == kind
    assert len(provider.calls) == 1 and (provider.chat_calls == 0 if streaming else provider.chat_calls == 1)


@pytest.mark.asyncio
async def test_unsent_stream_failure_recovers_but_submitted_and_partial_do_not():
    error = httpx.ConnectError("secret must not leak")
    agent, provider, session = runtime([error, StreamChunk(delta_content="done")])
    assert (await stream(agent, session))[-1].type == E.DONE
    assert len(provider.calls) == 2
    for steps in ([ConnectionError("opaque secret")],
                  [[StreamChunk(delta_content="partial"), ConnectionError("opaque secret")]]):
        agent, provider, session = runtime(steps)
        events = await stream(agent, session)
        assert len(provider.calls) == 1 and events[-1].type == E.ERROR
        assert "opaque secret" not in repr(events)
        if isinstance(steps[0], list):
            assert session.messages[-1].content == "partial"
            assert session.messages[-1].execution["request_phase"] == "partial_response"


@pytest.mark.asyncio
async def test_cancellation_during_backoff_prevents_second_request():
    agent, provider, session = runtime([RuntimeFailure(F.RATE_LIMITED, retry_after=.05)])
    cancelled = threading.Event()
    events = []
    async for event in agent.run_stream("task", session=session, cancel_event=cancelled):
        events.append(event)
        if event.type == E.RECOVERY:
            cancelled.set()
    assert len(provider.calls) == 1 and events[-1].metadata["completion_classification"] == "cancelled"


@pytest.mark.asyncio
async def test_permission_revoked_between_attempts_is_authoritative(tmp_path):
    agent, provider, session = runtime([RuntimeFailure(F.PROVIDER_OVERLOADED), StreamChunk(delta_content="must not run")])
    control = CodingRunControl(session.session_id, "p", mode="ask")
    tools = CodingToolFilter(tmp_path, control)
    events = []
    async for event in agent.run_stream("task", session=session, run_control=control, session_tool_filter=tools):
        events.append(event)
        if event.type == E.RECOVERY:
            control.permissions["allow_reads"] = False
    assert len(provider.calls) == 1 and events[-1].metadata["failure_kind"] == "permission_denied"


@pytest.mark.parametrize("limits", [{"max_requests":1}, {"max_tokens":0}, {"max_cost":0}])
@pytest.mark.asyncio
async def test_request_token_and_monetary_limits_are_bounded(limits):
    agent, provider, session = runtime([RuntimeFailure(F.PROVIDER_OVERLOADED), StreamChunk(delta_content="forbidden")], **limits)
    events = await stream(agent, session)
    assert len(provider.calls) <= 1 and events[-1].type == E.ERROR


@pytest.mark.asyncio
async def test_malformed_arguments_and_repeated_identical_failure_are_bounded():
    steps = [Message(role="assistant", tool_calls=[call(arguments="{bad", identifier=str(i))]) for i in range(5)]
    agent, provider, session = runtime(steps)
    effects=[]
    agent.tools.register(ToolDefinition(name="effect", description="effect"), lambda: effects.append(1))
    events=await stream(agent, session)
    assert not effects and len(provider.calls) == 4
    assert events[-1].type == E.ERROR
    assert sum(event.type == E.TOOL_CALL_ERROR for event in events) == 3
    assert all(message.to_dict().get("execution") is None for message in session.messages)


@pytest.mark.asyncio
async def test_timeout_after_external_dispatch_blocks_replay_and_persists_identity(tmp_path):
    agent, provider, session = runtime([Message(role="assistant",tool_calls=[call()]), StreamChunk(delta_content="forbidden")], max_elapsed=2)
    effects=[]
    async def effect():
        effects.append(1)
        await asyncio.Event().wait()
    agent.tools.register(ToolDefinition(name="effect",description="remote",source="mcp:remote"),effect)
    store=ChatHistoryStore(tmp_path/"chats.db")
    store.save_transcript(session,[{"role":"user","content":"task"}])
    events=[]
    async for event in agent.run_stream("task",session=session):
        events.append(event)
        if event.type in {E.TOOL_CALL_STARTED,E.TOOL_CALL_ERROR}:
            store.save_context(session)
    assert effects == [1] and len(provider.calls)==1 and events[-1].type==E.ERROR
    attempt=session.messages[1].execution["tool_attempts"][0]
    assert attempt["status"]=="outcome_unknown" and attempt["attempt_id"]
    restored=store.restore_session(store.load(session.session_id))
    assert restored.messages[1].execution["tool_attempts"][0]["attempt_id"]==attempt["attempt_id"]
    agent2, provider2, _=runtime([Message(role="assistant",tool_calls=[call(identifier="different-id")])])
    agent2.tools=agent.tools
    await stream(agent2,restored)
    assert effects==[1]


@pytest.mark.asyncio
async def test_confirmed_mutation_is_not_replayed_on_next_model_retry():
    agent, provider, session = runtime([Message(role="assistant",tool_calls=[call()]),
        RuntimeFailure(F.PROVIDER_OVERLOADED),StreamChunk(delta_content="done")])
    effects=[]
    agent.tools.register(ToolDefinition(name="effect",description="remote",source="mcp:remote"),lambda:effects.append(1))
    events=await stream(agent,session)
    assert effects==[1] and len(provider.calls)==3 and events[-1].type==E.DONE
    assert session.messages[1].execution["tool_attempts"][0]["request_phase"]=="tool_completed"


@pytest.mark.asyncio
async def test_timeout_before_dispatch_has_distinct_phase():
    agent, provider, session=runtime([Message(role="assistant",tool_calls=[call()]),StreamChunk(delta_content="explained")])
    class BeforeDispatch:
        tool_registry=ToolRegistry()
        def get_schemas(self,session): return []
        async def execute(self,tc,session):
            return ToolResult(tool_call_id=tc.id,name=tc.function.name,is_error=True,content="not dispatched",
                              metadata={"failure_kind":"tool_timeout","request_phase":"tool_not_dispatched"})
    events=await stream(agent,session,session_tool_filter=BeforeDispatch())
    failed=next(event for event in events if event.type==E.TOOL_CALL_ERROR)
    assert failed.metadata["request_phase"]=="tool_not_dispatched"


@pytest.mark.asyncio
async def test_final_without_tools_cannot_satisfy_incomplete_acceptance(tmp_path):
    agent, provider, session=runtime([StreamChunk(delta_content="Everything is done")])
    control=CodingRunControl(session.session_id,"p",mode="auto")
    control.plan=[{"id":"s","title":"Fix", "status":"pending", "acceptance_criteria":["fixed"],"verification":"pytest"}]
    events=await stream(agent,session,run_control=control,session_tool_filter=CodingToolFilter(tmp_path,control))
    assert events[-1].type==E.ERROR and not any(e.type==E.DONE for e in events)
    assert events[-1].metadata["completion_classification"]=="blocked"


@pytest.mark.asyncio
async def test_cleanup_error_finishes_request_without_leaking():
    agent,provider,session=runtime([])
    class Iterator:
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
        async def aclose(self): raise ValueError("CLEANUP-SECRET")
    provider.chat_stream=lambda **kwargs:Iterator()
    events=await stream(agent,session)
    assert any(e.type==E.MODEL_REQUEST_FINISHED for e in events)
    assert events[-1].type==E.ERROR and "CLEANUP-SECRET" not in repr(events)


def test_unknown_mcp_override_cannot_inherit_builtin_semantics():
    assert tool_semantics("read_file",ToolDefinition(name="read_file",description="x",source="mcp:remote"))==S.UNKNOWN


@pytest.mark.asyncio
async def test_overflow_without_compaction_never_silently_clips_tool_result():
    agent,provider,session=runtime([RuntimeFailure(F.CONTEXT_OVERFLOW),StreamChunk(delta_content="done")])
    session.messages=[Message(role="user",content="old"),Message(role="assistant",tool_calls=[call()]),
                      Message(role="tool",tool_call_id="call1",name="effect",content="x"*12000)]
    events=await stream(agent,session)
    assert events[-1].type==E.ERROR and len(provider.calls)==1
    assert len(session.messages[2].content)==12000
    assert len(provider.calls[0]["messages"][3].content)==12000
    assert events[-1].metadata["failure_kind"]=="context_overflow"


@pytest.mark.asyncio
async def test_nonstream_final_also_blocks_incomplete_coding_task(tmp_path):
    agent,provider,session=runtime([Message(role="assistant",content="all done")])
    control=CodingRunControl(session.session_id,"p",mode="auto")
    control.plan=[{"id":"s","title":"Fix", "status":"pending", "acceptance_criteria":["fixed"],"verification":"pytest"}]
    with pytest.raises(RuntimeError,match="Completion blocked"):
        await agent.run("task",session=session,session_tool_filter=CodingToolFilter(tmp_path,control))
    assert control.state=="blocked" and len(provider.calls)==1


@pytest.mark.asyncio
async def test_nonstream_permission_change_between_attempts_stops(tmp_path):
    agent,provider,session=runtime([RuntimeFailure(F.PROVIDER_OVERLOADED),Message(role="assistant",content="no")])
    control=CodingRunControl(session.session_id,"p",mode="ask")
    def observe(event):
        if event.type==E.RECOVERY: control.permissions["allow_reads"]=False
    with pytest.raises(RuntimeFailure) as caught:
        await agent.run("task",session=session,session_tool_filter=CodingToolFilter(tmp_path,control),on_event=observe)
    assert caught.value.kind==F.PERMISSION_DENIED and len(provider.calls)==1


@pytest.mark.parametrize("streaming",[True,False])
@pytest.mark.asyncio
async def test_compaction_cannot_bypass_token_reservation(streaming):
    agent,provider,session=runtime([],max_tokens=1)
    agent.context_compactor.enabled=True
    agent.context_compactor.needs_compaction=lambda *args:True
    async def compact(state,history,systems,schemas,budget,provider,checkpoint=None,on_request_event=None):
        on_request_event("model_request_started",{"request_id":"summary","request_kind":"compaction"})
        await provider.chat(messages=[Message(role="user",content="x"*10000)],tools=None,max_tokens=200)
    agent.context_compactor.compact=compact
    if streaming:
        events=await stream(agent,session)
        assert events[-1].type==E.ERROR
    else:
        with pytest.raises(RuntimeFailure) as caught: await agent.run("task",session=session)
        assert caught.value.kind==F.BUDGET_EXCEEDED
    assert provider.calls==[]


@pytest.mark.asyncio
async def test_host_auto_quality_hook_runs_before_completion_gate(tmp_path):
    agent,provider,session=runtime([StreamChunk(delta_content="done")])
    control=CodingRunControl(session.session_id,"p",mode="auto")
    tool_filter=CodingToolFilter(tmp_path,control)
    observed=[]
    async def before(): observed.append("host verification")
    control.before_completion=before
    original=tool_filter.completion_gate_async
    async def gate():
        assert observed==["host verification"]
        return await original()
    tool_filter.completion_gate_async=gate
    events=await stream(agent,session,run_control=control,session_tool_filter=tool_filter)
    assert events[-1].type==E.DONE and observed==["host verification"]


@pytest.mark.asyncio
async def test_unsupported_schema_refreshes_metadata_once_without_resubmitting_prompt():
    agent,provider,session=runtime([RuntimeFailure(F.UNSUPPORTED_CAPABILITY)])
    refreshes=[]
    async def refresh():
        refreshes.append(1)
        return {"status":"refreshed","model":"offline","source":"provider","capabilities":{"supports_tools":True}}
    provider.refresh_capabilities=refresh
    events=await stream(agent,session)
    assert len(provider.calls)==1 and refreshes==[1] and events[-1].type==E.ERROR
    assert any(event.metadata.get("capability_refresh",{}).get("status")=="refreshed" for event in events)


@pytest.mark.asyncio
async def test_signature_argument_error_is_proven_not_dispatched():
    registry=ToolRegistry();effects=[]
    registry.register(ToolDefinition(name="effect",description="mutate"), lambda required:effects.append(required))
    result=await registry.execute(call(arguments='{"unexpected":1}'))
    assert result.is_error and result.metadata=={"failure_kind":"invalid_tool_arguments","request_phase":"tool_not_dispatched"}
    assert effects==[]


@pytest.mark.parametrize("connection_owner",[True,False])
@pytest.mark.asyncio
async def test_reported_mcp_error_is_not_misrecorded_as_success(connection_owner,monkeypatch):
    from firik_agent.mcp.protocol import MCPCallToolResult,MCPContentItem,MCPToolSchema
    from firik_agent.mcp.manager import MCPManager
    from firik_agent.mcp.connection import MCPConnection,MCPConnectionManager
    from firik_agent.mcp.models import MCPServerConfig
    schema=MCPToolSchema(name="effect",description="remote mutation",inputSchema={"type":"object"})
    class Client:
        def __init__(self,**kwargs): pass
        async def connect(self): pass
        async def list_tools(self): return [schema]
        async def call_tool(self,*args,**kwargs):
            return MCPCallToolResult(isError=True,content=[MCPContentItem(type="text",text="PRIVATE-REMOTE-BODY")])
    registry=ToolRegistry()
    if connection_owner:
        owner=MCPConnectionManager(registry)
        conn=MCPConnection(MCPServerConfig(name="remote",command="fake"));conn.cached_tools=[schema]
        conn.call_tool=Client().call_tool
        owner._register_tools_in_registry(conn)
    else:
        monkeypatch.setattr("firik_agent.mcp.manager.MCPClient",Client)
        await MCPManager(registry).add_server("remote",command="fake")
    result=await registry.execute(call(name="mcp_remote_effect"))
    assert result.is_error and result.metadata["failure_kind"]=="tool_outcome_unknown"
    assert result.metadata["requires_reconciliation"] and "PRIVATE-REMOTE-BODY" not in result.content


@pytest.mark.asyncio
async def test_nonstream_iteration_budget_is_explicitly_blocked(tmp_path):
    (tmp_path/"a.py").write_text("value=1\n")
    agent,provider,session=runtime([Message(role="assistant",tool_calls=[call(name="read_file",arguments='{"path":"a.py"}')])])
    agent.max_iterations=1
    control=CodingRunControl(session.session_id,"p",mode="auto")
    events=[]
    with pytest.raises(RuntimeFailure) as caught:
        await agent.run("task",session=session,session_tool_filter=CodingToolFilter(tmp_path,control),on_event=events.append)
    assert caught.value.kind==F.BUDGET_EXCEEDED and control.state=="blocked"
    assert events[-1].metadata["completion_classification"]=="blocked"


@pytest.mark.asyncio
async def test_successful_call_identity_does_not_block_explicit_new_run():
    agent,provider,session=runtime([Message(role="assistant",tool_calls=[call()]),Message(role="assistant",content="done"),
                                  Message(role="assistant",tool_calls=[call()]),Message(role="assistant",content="done")])
    effects=[]
    agent.tools.register(ToolDefinition(name="effect",description="trusted mutation"),lambda:effects.append(1))
    assert (await stream(agent,session))[-1].type==E.DONE
    assert (await stream(agent,session))[-1].type==E.DONE
    assert effects==[1,1]
    entries=[message.execution["tool_attempts"][0] for message in session.messages if message.execution.get("tool_attempts")]
    assert entries[0]["run_id"]!=entries[1]["run_id"] and entries[0]["attempt_id"]!=entries[1]["attempt_id"]


@pytest.mark.asyncio
async def test_tool_payload_cannot_replace_host_attempt_identity():
    agent,provider,session=runtime([Message(role="assistant",tool_calls=[call()]),Message(role="assistant",content="done")])
    def handler():
        return ToolResult(tool_call_id="forged",name="effect",content="ok",metadata={"attempt_id":"forged","run_id":"forged","fingerprint":"forged","side_effect_risk":"pure_read"})
    agent.tools.register(ToolDefinition(name="effect",description="external",source="mcp:remote"),handler)
    assert (await stream(agent,session))[-1].type==E.DONE
    entry=session.messages[1].execution["tool_attempts"][0]
    assert entry["attempt_id"]!="forged" and entry["run_id"]!="forged" and entry["side_effect_risk"]=="unknown"


@pytest.mark.asyncio
async def test_usage_or_empty_frame_prevents_unsubmitted_interface_fallback():
    for first in (StreamChunk(),StreamChunk(usage={"prompt_tokens":1})):
        agent,provider,session=runtime([[first,NotImplementedError("not local before submission")]])
        events=await stream(agent,session)
        assert len(provider.calls)==1 and provider.chat_calls==0 and events[-1].type==E.ERROR
        finish=next(e for e in events if e.type==E.MODEL_REQUEST_FINISHED)
        assert finish.metadata["request_phase"]=="submitted_no_output"
