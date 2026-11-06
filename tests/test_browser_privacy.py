"""Browser input stays executable while events, history and SQLite omit values."""
import json

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.core.types import Message, StreamChunk, ToolCall
from firik_agent.browser.config import BrowserConfig
from firik_agent.browser.models import BrowserError, BrowserGrant, BrowserIdentity
from firik_agent.browser.tool_adapter import BrowserToolAdapter
from firik_agent.projects.coding_sessions import CodingSessionStore, REDACTED, redact_browser_arguments, redact_sensitive
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.execution_journal import ExecutionJournal
from firik_agent.projects.models import Project


PRIVATE = "indistinct-input-573912"


@pytest.mark.parametrize("target", [{"label": "Password"}, {"name": "OTP code", "role": "textbox"},
                                    {"test_id": "MFA"}, {"ref": "e17"}])
def test_contextual_storage_copies_hide_browser_fill_even_for_opaque_refs(target):
    arguments = {"target": target, "revision": 1, "value": PRIVATE}
    call = {"id": "fill", "function": {"name": "browser_fill", "arguments": json.dumps(arguments)}}
    for raw in (call, json.dumps(call), {"tool_name": "browser_fill", "arguments": arguments}):
        clean = redact_sensitive(raw)
        assert PRIVATE not in str(clean)
        assert REDACTED in str(clean)
    assert arguments["value"] == PRIVATE  # A log projection cannot alter dispatch.


def test_sensitive_target_and_form_value_redacted_without_a_tool_name():
    for payload in ({"target": {"label": "Password"}, "value": PRIVATE},
                    {"form": [{"name": "otp", "value": PRIVATE}]},
                    {"form": [{"name": "login_otp_code", "value": PRIVATE}]},
                    {"target": {"name": "Security code"}, "key": PRIVATE}):
        assert PRIVATE not in str(redact_sensitive(payload))
    arguments = '{ "target": {"label": "Name"}, "value": "Alice" }'
    assert redact_browser_arguments("", arguments) == arguments
    assert redact_browser_arguments("write_file", arguments) == arguments


def test_real_sqlite_browser_history_events_and_intents_omit_input_values(tmp_path):
    db = ProjectDatabase(tmp_path / "state.db")
    db.create_project(Project(id="p", name="Project", local_path=str(tmp_path)))
    store = CodingSessionStore(db)
    run = store.create_run("p", session_id="chat")
    arguments = {"target": {"ref": "e17"}, "value": PRIVATE, "revision": 1}
    message = {"role": "assistant", "tool_calls": [{"id": "fill", "function": {
        "name": "browser_fill", "arguments": json.dumps(arguments)}}]}
    store.record_message(run.id, message)
    store.append_event(run.id, {"type": "tool_call_started", "tool_name": "browser_fill", "arguments": arguments})
    journal = ExecutionJournal(db)
    journal.begin(run.id, "tool:browser_fill", input_data=redact_browser_arguments("browser_fill", json.dumps(arguments)),
                  side_effect="external_side_effect")
    assert PRIVATE not in str(store.get_run(run.id).messages)
    assert PRIVATE not in str(store.list_events(run.id))
    assert PRIVATE not in str(journal.list_steps(run.id))
    # Check the actual SQLite storage, including its execution/event rows.
    with db._get_connection() as connection:
        assert PRIVATE not in "\n".join(connection.iterdump())


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.asyncio
async def test_native_loop_preserves_dispatch_input_but_hides_history_and_tool_events(streaming, reject):
    seen, displayed = [], []
    class Manager:
        config = BrowserConfig(enabled=True)
        async def prepare(self, identity, grant, operation, arguments):
            seen.append(arguments["value"])
            if reject:
                raise BrowserError("authentication_handoff_required", "Manual authentication required.")
            return {"fingerprint": "trusted"}
        async def dispatch(self, identity, grant, operation, arguments, **kwargs):
            assert arguments["value"] == PRIVATE
            return {"outcome": "action_dispatched"}
        async def close_owner(self, identity):
            pass
    class Provider:
        name, current_model, model_metadata = "fixture", "offline", {}
        def __init__(self):
            self.count = 0
        async def chat(self, **kwargs):
            self.count += 1
            if self.count == 1:
                return Message(role="assistant", tool_calls=[ToolCall(id="fill", function={
                    "name": "browser_fill", "arguments": json.dumps({
                        "target": {"ref": "e17"}, "revision": 1, "value": PRIVATE})})])
            assert PRIVATE not in str([message.to_dict() for message in kwargs["messages"]])
            return Message(role="assistant", content="Reviewed")
        async def chat_stream(self, **kwargs):
            response = await self.chat(**kwargs)
            yield StreamChunk(delta_content=response.content, tool_calls=response.tool_calls)
    agent = Agent(config={"agent": {"context_compaction": {"enabled": False}}, "providers": {},
                         "plugins": {}, "skills": {}, "self_improvement": {"mode": "off"}})
    agent._is_initialized = True
    agent.providers.register("fixture", Provider(), set_active=True)
    session = ChatSession(model="offline", learning_enabled=False)
    session.browser_enabled = True
    adapter = BrowserToolAdapter(Manager(), BrowserIdentity(session.session_id), lambda: BrowserGrant(enabled=True),
                                 authorize=lambda *args: True)
    gate = SessionToolFilter(agent.tools, browser_adapter=adapter)
    try:
        if streaming:
            events = [event async for event in agent.run_stream("Fill the field", session=session, session_tool_filter=gate)]
            displayed = [event.arguments for event in events if event.type == AgentEventType.TOOL_CALL_STARTED]
        else:
            await agent.run("Fill the field", session=session, session_tool_filter=gate,
                            on_tool_start=lambda name, args: displayed.append(args))
        assert seen == [PRIVATE]
        assert displayed and PRIVATE not in str(displayed)
        assert PRIVATE not in str([message.to_dict() for message in session.messages])
    finally:
        await agent.close()
