"""Profiles retain retrieval targets without imposing false model capacity caps."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType as E
from firik_agent.core.failures import FailureKind, RuntimeFailure
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message, StreamChunk
from firik_agent.projects.coding_runtime import CodingRunControl, prepare_coding_messages
from firik_agent.projects.harness_profiles import PROFILES, input_limit, resolve_harness_profile


PROMPT = "Türkçe uygulama gereksinimi ve doğrulama adımı.\n" * 1200
CAPACITY = 1_048_576


class Provider:
    name, current_model, base_url = "fixture", "exact-model", "https://fixture.invalid/v1"

    def __init__(self):
        self.calls = []
        self.model_metadata = {self.current_model: {"source": "provider", "context_window": CAPACITY}}

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return Message(role="assistant", content="done")

    async def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        yield StreamChunk(delta_content="done", is_done=True)


def runtime(profile, compaction=True):
    agent = Agent(config={"agent": {"context_compaction": {"enabled": compaction}},
                          "providers": {}, "plugins": {}, "skills": {}})
    agent._is_initialized = True
    agent._build_full_system_prompt = lambda: "Follow the user request."
    agent.tools.get_schemas = lambda: []
    provider = Provider()
    agent.providers.register("fixture", provider, set_active=True)
    session = ChatSession()
    session.harness_profile = resolve_harness_profile(session_override=profile)
    return agent, provider, session


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("profile", list(PROFILES))
@pytest.mark.parametrize("compaction", [False, True])
async def test_default_profile_uses_known_model_capacity_for_long_active_request(streaming, profile, compaction):
    agent, provider, session = runtime(profile, compaction)
    events = []
    if streaming:
        events = [event async for event in agent.run_stream(PROMPT, session=session)]
        assert events[-1].type == E.DONE
    else:
        assert await agent.run(PROMPT, session=session, on_event=events.append) == "done"
    assert len(provider.calls) == 1
    assert provider.calls[0]["messages"][-1].content == PROMPT
    assert session.messages[0].content == PROMPT and session.compaction.cursor == 0
    assert not any(event.type in {E.ERROR, E.CONTEXT_COMPACTION_STARTED, E.CONTEXT_COMPACTED} for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("compaction", [False, True])
async def test_explicit_context_cap_still_stops_oversized_request_before_submission(streaming, compaction):
    agent, provider, session = runtime({"name": "coding", "context_tokens": 4000}, compaction)
    if streaming:
        events = [event async for event in agent.run_stream(PROMPT, session=session)]
        assert events[-1].type == E.ERROR
        assert not any(event.type == E.MODEL_REQUEST_STARTED for event in events)
    elif compaction:
        with pytest.raises(ValueError, match="bağlam bütçesine"):
            await agent.run(PROMPT, session=session)
    else:
        with pytest.raises(RuntimeFailure) as caught:
            await agent.run(PROMPT, session=session)
        assert caught.value.kind == FailureKind.CONTEXT_OVERFLOW
    assert not provider.calls
    assert session.messages[0].content == PROMPT and session.compaction.cursor == 0


@pytest.mark.asyncio
async def test_desktop_coding_project_context_uses_known_capacity_without_failed_compaction():
    agent, provider, session = runtime("coding")
    control = CodingRunControl(session.session_id, "project", mode="ask")
    control.capabilities = provider.model_metadata[provider.current_model]
    control.harness_profile = session.harness_profile
    control.project_context_data = PROMPT
    gate = SimpleNamespace(get_schemas=lambda session: [], root="/tmp")
    events = [event async for event in agent.run_stream("İncele", session=session,
              session_tool_filter=gate, run_control=control)]
    assert events[-1].type == E.DONE
    assert not any(event.type in {E.ERROR, E.CONTEXT_COMPACTION_STARTED, E.CONTEXT_COMPACTED} for event in events)
    assert len(provider.calls) == 1
    assert any(PROMPT in (message.content or "") for message in provider.calls[0]["messages"])
    assert provider.calls[0]["messages"][-1].content == "İncele"


def test_context_limit_provenance_survives_resolving_and_old_session_storage():
    default = resolve_harness_profile(session_override="coding")
    assert default["context_tokens_explicit"] is False
    assert resolve_harness_profile(session_override=default) == default
    legacy = {key: value for key, value in default.items() if key != "context_tokens_explicit"}
    assert resolve_harness_profile(session_override=legacy) == default
    explicit = resolve_harness_profile(session_override={"name": "coding", "context_tokens": 8000})
    assert explicit["context_tokens_explicit"] is True
    assert resolve_harness_profile(session_override=explicit) == explicit
    legacy["context_tokens"] = 5000
    assert resolve_harness_profile(session_override=legacy)["context_tokens_explicit"] is True
    configured = resolve_harness_profile(user_config={"harness_overrides": {"context_tokens": 6000}})
    assert configured["context_tokens_explicit"] is True


def test_default_unknown_capacity_and_explicit_host_caps_remain_bounded():
    default = resolve_harness_profile(session_override="coding")
    assert input_limit(100_000, default, CAPACITY) == 100_000
    assert input_limit(100_000, default, None) == 8000
    assert input_limit(5000, default, 8192) == 5000
    explicit = resolve_harness_profile(session_override={"name": "coding", "context_tokens": 4000})
    assert input_limit(100_000, explicit, CAPACITY) == 4000
    assert input_limit(3000, explicit, 8192) == 3000


def test_deterministic_coding_preparation_uses_provider_capacity_and_respects_explicit_caps():
    control = CodingRunControl("s", "p")
    control.capabilities = {"source": "provider", "context_window": CAPACITY}
    control.harness_profile = resolve_harness_profile(session_override="coding")
    messages = [Message(role="system", content="Instructions"), Message(role="user", content=PROMPT)]
    original = deepcopy(messages)
    assert prepare_coding_messages(messages, control, []) == messages
    control.harness_profile = resolve_harness_profile(session_override={"name": "coding", "context_tokens": 4000})
    with pytest.raises(ValueError, match="bağlam bütçesini"):
        prepare_coding_messages(messages, control, [])
    assert messages == original
