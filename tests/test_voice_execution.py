"""Voice exact-operation approval narrows the real session/coding executors."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.tools import ToolRegistry
from firik_agent.core.types import ToolCall, ToolCallFunction, ToolDefinition
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.voice.execution import VoiceToolFilter, voice_metadata
from firik_agent.voice.requests import VoiceRequest
from firik_agent.voice.requests import VoiceImpact
from firik_agent.voice.requests import VoiceScope
from firik_agent.voice.intent_router import VoiceRequestRouter


def request(*, target="conversation", session_id="existing-chat", project_id=None):
    scope = VoiceScope(session_id=session_id, project_id=project_id, target=target)
    router = VoiceRequestRouter(lambda request: True)
    issued = router.create_request("Run the permitted tests", scope)
    return router.confirm(issued)


def call(name="write_file", arguments=None):
    return ToolCall(id="call-one", function=ToolCallFunction(name=name,
        arguments=json.dumps(arguments if arguments is not None else {"path": "actual.py", "content": "exact"})))


def setup(*, source="meta:agent", name="write_file", enabled=None, approve=None, events=None, cancel=None):
    calls = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(name=name, description="existing handler", source=source),
                      lambda **arguments: calls.append(arguments) or "actual result")
    session = ChatSession(session_id="existing-chat", enabled_tool_names=enabled)
    delegate = SessionToolFilter(registry)
    wrapper = VoiceToolFilter(delegate, request(), approve=approve, cancel_event=cancel,
                              on_event=events.append if events is not None else None)
    return wrapper, session, calls, registry


@pytest.mark.asyncio
async def test_no_approval_callback_cannot_dispatch_effectful_voice_tool():
    wrapper, session, calls, _ = setup()
    result = await wrapper.execute(call(), session)
    assert result.is_error and not calls
    assert result.metadata["origin"] == "voice"
    assert result.metadata["request_phase"] == "tool_not_dispatched"


@pytest.mark.asyncio
@pytest.mark.parametrize("source,name", [("meta:agent", "write_file"), ("plugin:helper", "read_file"),
    ("mcp:server", "read_file"), ("meta:agent", "activate_skill"), ("unknown", "unknown")])
async def test_unknown_effects_require_exact_ui_approval_even_with_benign_name(source, name):
    approved, events = [], []
    async def approve(payload):
        approved.append(payload)
        return True
    wrapper, session, calls, registry = setup(source=source, name=name, approve=approve, events=events)
    if source.startswith("mcp:"):
        session.active_mcp_servers.add("server")
    tool_call = call(name, {"path": "actual.py", "content": "reviewed"})
    result = await wrapper.execute(tool_call, session)
    assert not result.is_error and calls == [{"path": "actual.py", "content": "reviewed"}]
    assert approved[0]["tool_name"] == name
    assert approved[0]["arguments"] == {"path": "actual.py", "content": "reviewed"}
    assert approved[0]["session_id"] == session.session_id
    assert [event.type for event in events] == [AgentEventType.APPROVAL_REQUIRED, AgentEventType.APPROVAL_DECIDED]
    assert events[-1].metadata["allowed"] is True
    assert events[0].metadata["utterance_id"] == wrapper.request.utterance_id


@pytest.mark.asyncio
async def test_enabled_tools_and_mcp_scoping_are_enforced_before_asking():
    approved = []
    async def approve(payload):
        approved.append(payload)
        return True
    wrapper, session, calls, _ = setup(approve=approve, enabled=set())
    assert not wrapper.get_schemas(session)
    assert (await wrapper.execute(call(), session)).is_error
    assert not approved and not calls
    wrapper, session, calls, _ = setup(source="mcp:disabled", approve=approve)
    assert (await wrapper.execute(call(), session)).is_error
    assert not approved and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["read_file", "list_skills", "list_plugins", "list_mcp_servers", "get_environment_info"])
async def test_fixed_application_reads_keep_existing_permissions_without_extra_prompt(name):
    wrapper, session, calls, _ = setup(name=name)
    assert not (await wrapper.execute(call(name), session)).is_error
    assert calls


@pytest.mark.asyncio
async def test_high_impact_request_cannot_skip_tool_review_using_readonly_handler():
    proposed = []
    async def approve(payload):
        proposed.append(payload)
        return False
    wrapper, session, calls, _ = setup(name="read_file", approve=approve)
    router = VoiceRequestRouter(lambda request: True)
    issued = router.create_request("Delete actual.py", VoiceScope(session.session_id))
    wrapper.request = router.confirm(issued)
    assert (await wrapper.execute(call("read_file"), session)).is_error
    assert proposed and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [False, "yes", "true", 1, None])
async def test_only_literal_true_from_trusted_async_ui_callback_is_a_grant(decision):
    async def approve(payload):
        return decision
    wrapper, session, calls, _ = setup(approve=approve)
    assert (await wrapper.execute(call(), session)).is_error
    assert not calls


@pytest.mark.asyncio
async def test_synchronous_transcript_handler_boolean_is_not_ui_approval():
    wrapper, session, calls, _ = setup(approve=lambda payload: True)
    assert (await wrapper.execute(call(), session)).is_error
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["[1,2]", "broken-json", '{"value":NaN}', "x" * 65537])
async def test_malformed_or_oversized_operation_never_reaches_approval_or_executor(raw):
    async def approve(payload):
        pytest.fail("invalid operation must not be proposed")
    wrapper, session, calls, _ = setup(approve=approve)
    tool_call = call()
    tool_call.function.arguments = raw
    assert (await wrapper.execute(tool_call, session)).is_error
    assert not calls


@pytest.mark.asyncio
async def test_cancellation_closes_pending_approval_and_preserves_no_execution():
    entered, closed = asyncio.Event(), asyncio.Event()
    async def approve(payload):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    wrapper, session, calls, _ = setup(approve=approve)
    pending = asyncio.create_task(wrapper.execute(call(), session))
    await asyncio.wait_for(entered.wait(), 1)
    wrapper.request.cancellation_token.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 1)
    assert closed.is_set() and not calls


@pytest.mark.asyncio
async def test_permission_changes_while_prompt_is_visible_cannot_be_approved_around():
    async def approve(payload):
        session.enabled_tool_names = set()
        return True
    wrapper, session, calls, _ = setup(approve=approve)
    assert (await wrapper.execute(call(), session)).is_error
    assert not calls


@pytest.mark.asyncio
async def test_changed_tool_handler_requires_a_fresh_operation_not_stale_grant():
    async def approve(payload):
        registry.register(ToolDefinition(name="write_file", description="replacement", source="meta:agent"),
                          lambda **kwargs: calls.append("replacement"))
        return True
    wrapper, session, calls, registry = setup(approve=approve)
    assert (await wrapper.execute(call(), session)).is_error
    assert not calls


@pytest.mark.asyncio
async def test_changed_handler_with_same_definition_also_invalidates_grant():
    async def approve(payload):
        registry.register(registry.get_tool("write_file"), lambda **kwargs: calls.append("replacement"))
        return True
    wrapper, session, calls, registry = setup(approve=approve)
    assert (await wrapper.execute(call(), session)).is_error
    assert not calls


@pytest.mark.asyncio
async def test_approved_operation_cannot_be_changed_during_wait():
    tool_call = call()
    async def approve(payload):
        tool_call.function.arguments = '{"path":"another.py","content":"unreviewed"}'
        return True
    wrapper, session, calls, _ = setup(approve=approve)
    assert not (await wrapper.execute(tool_call, session)).is_error
    assert calls == [{"path": "actual.py", "content": "exact"}]


@pytest.mark.asyncio
async def test_mutated_approval_payload_is_rejected():
    async def approve(payload):
        payload["arguments"]["path"] = "another.py"
        return True
    wrapper, session, calls, _ = setup(approve=approve)
    assert (await wrapper.execute(call(), session)).is_error
    assert not calls


@pytest.mark.asyncio
async def test_activity_event_redacts_secrets_but_approval_shows_exact_operation():
    events = []
    async def approve(payload):
        assert payload["arguments"]["api_key"] == "secret-for-ui"
        return False
    wrapper, session, _, _ = setup(approve=approve, events=events)
    await wrapper.execute(call(arguments={"api_key": "secret-for-ui"}), session)
    assert events[0].metadata["arguments"]["api_key"] == "<REDACTED>"


def test_voice_scope_is_validated_without_inventing_ids():
    value = request(target="coding", project_id="existing-project")
    assert voice_metadata(value, session_id="existing-chat", project_id="existing-project", target="coding")["origin"] == "voice"
    with pytest.raises(ValueError):
        voice_metadata(value, session_id="unknown")
    with pytest.raises(ValueError):
        voice_metadata(value, project_id="unknown")
    with pytest.raises(ValueError):
        voice_metadata(value, target="conversation")


def test_direct_or_replaced_requests_cannot_forge_transcript_review():
    from dataclasses import replace
    reviewed = request()
    direct = VoiceRequest(session_id="existing-chat", original_transcript="delete a.py", request_text="delete a.py")
    with pytest.raises(ValueError):
        voice_metadata(direct)
    with pytest.raises(ValueError):
        voice_metadata(replace(reviewed, request_text="delete unreviewed.py", impact=VoiceImpact.LOW))
    router = VoiceRequestRouter(lambda request: True)
    pending = router.create_request("Run permitted tests", VoiceScope("existing-chat"))
    with pytest.raises(ValueError):
        voice_metadata(pending)
    low = router.create_request("Explain the project", VoiceScope("existing-chat"))
    assert voice_metadata(low)["impact"] == "low"


@pytest.mark.asyncio
async def test_coding_voice_edits_use_existing_exact_approval_despite_auto_allow(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    session = ChatSession(session_id="existing-chat")
    voice = request(target="coding", project_id="existing-project")
    events = []
    control = CodingRunControl(session.session_id, "existing-project", mode="auto",
        policy={"filesystem.write": "allow"}, voice_request=voice, on_event=events.append)
    runtime = CodingToolFilter(root, control)
    tool_call = call("write_file", {"path": "new.py", "content": "reviewed", "expected_hash": "new"})
    operation = asyncio.create_task(runtime.execute(tool_call, session))
    while not control.pending_approvals:
        await asyncio.sleep(.005)
    assert not (root / "new.py").exists()
    request_id, proposed = next(iter(control.pending_approvals.items()))
    assert proposed["arguments"] == {"path": "new.py", "content": "reviewed", "expected_hash": "new"}
    assert control.decide_approval(request_id, True)
    assert not (await operation).is_error
    assert (root / "new.py").read_text() == "reviewed"
    assert next(event for event in events if event.type == AgentEventType.APPROVAL_REQUIRED).metadata["origin"] == "voice"


@pytest.mark.asyncio
async def test_voice_cannot_override_coding_deny_and_cancel_token_stops_paused_wait():
    voice = request(target="coding", project_id="existing-project")
    control = CodingRunControl("existing-chat", "existing-project", mode="auto",
        policy={"filesystem.write": "deny"}, voice_request=voice)
    assert control.permission_decision("filesystem.write") == "deny"
    control.pause()
    pending = asyncio.create_task(control.checkpoint())
    await asyncio.sleep(.03)
    voice.cancellation_token.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(pending, 1)


def test_voice_narrowing_reaches_real_child_control():
    from firik_agent.projects.subagents import _ChildControl
    voice = request(target="coding", project_id="existing-project")
    parent = CodingRunControl("existing-chat", "existing-project", mode="auto", voice_request=voice)
    manager = SimpleNamespace(control=parent, root="/tmp", limits=SimpleNamespace(max_iterations=3))
    job = {"session": ChatSession(session_id="existing-child"), "agent_id": "child-one", "canonical_role": "worker"}
    assignment = SimpleNamespace(mode="auto", permissions=dict(parent.permissions))
    child = _ChildControl(manager, job, assignment)
    assert child.permission_decision("filesystem.write") == "ask"
    assert child.permission_decision("filesystem.delete") == "ask"
    parent.permissions["allow_edits"] = False
    assert child.permission_decision("filesystem.write") == "deny"
