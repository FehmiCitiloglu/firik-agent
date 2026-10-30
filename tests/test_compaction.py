"""Behavioral evidence for incremental compression, safe tool boundaries and recovery."""
import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.chat_history import ChatHistoryStore
from firik_agent.core.compaction import CompactionState, ContextCompactor, digest, estimate_tokens, safe_boundaries
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction


class SummaryProvider:
    current_model = "test"
    model_metadata = {}

    def __init__(self, failure=None):
        self.requests = []
        self.streams = []
        self.failure = failure

    async def chat(self, **kwargs):
        self.requests.append(kwargs)
        if self.failure:
            raise self.failure
        return Message(role="assistant", content="Hedef: düzeltme. main.py değişti. pytest başarısız; yeniden doğrula. Onay bekliyor.")

    async def chat_stream(self, **kwargs):
        self.streams.append(kwargs)
        yield StreamChunk(delta_content="Devam edildi.", is_done=True)


def archive(n=30):
    return [Message(role=role, content=f"tur {i} " + "x" * 500) for i in range(n)
            for role in ("user", "assistant")]


def compressor():
    return ContextCompactor({"fallback_input_tokens": 3000, "summary_max_tokens": 256})


@pytest.mark.asyncio
async def test_incremental_summary_keeps_archive_and_recent_messages():
    history = archive()
    original = list(history)
    state, provider, compactor = CompactionState(), SummaryProvider(), compressor()
    details = await compactor.compact(state, history, [], [], 3000, provider)
    assert history == original
    assert details["after_tokens_estimate"] < details["before_tokens_estimate"]
    assert details["summary_requests"] > 1  # Oversized restored archive is batched.
    assert state.view(history)[-6:] == history[-6:]
    assert not compactor.needs_compaction(state, history, [], [], 3000)
    old_cursor = state.cursor
    history.extend(archive(20))
    await compactor.compact(state, history, [], [], 3000, provider)
    assert state.cursor > old_cursor
    payloads = [json.loads(r["messages"][1].content) for r in provider.requests]
    assert any(p["previous_summary"] for p in payloads)
    assert all(r["tools"] is None for r in provider.requests)


@pytest.mark.asyncio
async def test_current_long_tool_turn_is_compacted_without_orphans():
    history = [Message(role="user", content="Kullanıcı hedefi")]
    for i in range(25):
        history.extend([Message(role="assistant", tool_calls=[ToolCall(id=str(i), function=ToolCallFunction(name="read_file", arguments="{}"))]),
                        Message(role="tool", tool_call_id=str(i), content="x" * 800)])
    state = CompactionState()
    await compressor().compact(state, history, [], [], 3000, SummaryProvider())
    assert state.cursor in safe_boundaries(history)
    view = state.view(history)
    assert view[1] == history[0]  # Original active user intent stays verbatim.
    calls = {c.id for m in view for c in m.tool_calls or []}
    assert all(m.tool_call_id in calls for m in view if m.role == "tool")
    assert len(history) == 51


@pytest.mark.asyncio
async def test_failure_and_cancel_do_not_commit_partial_summary():
    history, state = archive(), CompactionState()
    with pytest.raises(RuntimeError):
        await compressor().compact(state, history, [], [], 3000, SummaryProvider(RuntimeError("offline")))
    assert state == CompactionState()
    checks = 0
    async def checkpoint():
        nonlocal checks
        checks += 1
        if checks > 2:
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await compressor().compact(state, history, [], [], 3000, SummaryProvider(), checkpoint)
    assert state == CompactionState()


def test_persistence_clear_and_modified_archive_invalidate_cache(tmp_path):
    session = ChatSession()
    session.messages = archive(5)
    session.compaction = CompactionState("kalıcı özet", 4, digest(session.messages[:4]))
    store = ChatHistoryStore(tmp_path / "chats.db")
    store.save_transcript(session, [{"role": "user", "content": "test"}])
    store.save_context(session)
    restored = store.restore_session(store.load(session.session_id))
    assert asdict(restored.compaction) == asdict(session.compaction)
    restored.messages[0].content = "değişen hedef"
    assert restored.compaction.view(restored.messages) == restored.messages
    assert restored.compaction.cursor == 0
    session.clear()
    assert not session.compaction.summary


@pytest.mark.asyncio
async def test_agent_stream_emits_compaction_then_continues():
    agent = Agent(config={"agent": {"context_compaction": {"fallback_input_tokens": 3000, "summary_max_tokens": 256}},
                          "providers": {}, "plugins": {}, "skills": {}})
    agent._is_initialized = True
    provider = SummaryProvider()
    agent.providers.register("summary", provider, set_active=True)
    session = ChatSession()
    session.messages = archive()
    events = [e async for e in agent.run_stream("Son istek", session=session)]
    types = [e.type.value for e in events]
    assert types[0] == "context_compaction_started"
    compacted = types.index("context_compacted")
    assert any(e.type.value == "model_request_started" and e.metadata.get("request_kind") == "compaction"
               for e in events[:compacted])
    assert any(e.type.value == "model_request_started" and e.metadata.get("request_kind") == "model"
               for e in events[compacted + 1:])
    assert "done" in types and "error" not in types
    assert provider.streams[0]["messages"][-1].content == "Son istek"
    assert provider.streams[0]["max_tokens"] == session.max_tokens
    assert len(session.messages) == 62
    assert estimate_tokens(provider.streams[0]["messages"]) < 2500


@pytest.mark.asyncio
async def test_nonstream_loop_uses_same_compaction():
    agent = Agent(config={"agent": {"context_compaction": {"fallback_input_tokens": 3000, "summary_max_tokens": 256}},
                          "providers": {}, "plugins": {}, "skills": {}})
    agent._is_initialized = True
    provider = SummaryProvider()
    agent.providers.register("summary", provider, set_active=True)
    agent.messages = archive()
    await agent.run("Son istek")
    assert agent.compaction.cursor > 0
    assert provider.requests[-1]["messages"][-1].content == "Son istek"


@pytest.mark.asyncio
async def test_oversized_active_input_stops_without_destroying_history():
    history = [Message(role="user", content="x" * 20000)]
    state, compactor, provider = CompactionState(), compressor(), SummaryProvider()
    assert compactor.needs_compaction(state, history, [], [], 3000)
    with pytest.raises(ValueError, match="bağlam bütçesine"):
        await compactor.compact(state, history, [], [], 3000, provider)
    assert state.cursor == 0 and len(history[0].content) == 20000
    assert not provider.requests


def test_small_request_skips_compaction_candidate_planning(monkeypatch):
    compactor = compressor()
    def unexpected_plan(*args):
        raise AssertionError("A small request must not scan compaction candidates")
    monkeypatch.setattr(compactor, "_plan_compaction", unexpected_plan)
    assert not compactor.needs_compaction(CompactionState(), [Message(role="user", content="Hello")], [], [], 3000)


@pytest.mark.asyncio
@pytest.mark.parametrize("ratio", [.8, .95])
async def test_fitting_active_input_above_soft_trigger_needs_no_summary(ratio):
    compactor, state, provider = compressor(), CompactionState(), SummaryProvider()
    history = [Message(role="user", content="x" * int(3000 * ratio * 3))]
    original = history[0].content
    assert 2250 < estimate_tokens(history) <= 3000
    assert not compactor.needs_compaction(state, history, [], [], 3000)
    details = await compactor.compact(state, history, [], [], 3000, provider)
    assert details["compacted"] is False and details["summary_requests"] == 0
    assert state == CompactionState() and history[0].content == original
    assert not provider.requests


@pytest.mark.asyncio
async def test_fitting_active_input_is_not_rejected_when_history_cannot_reach_soft_trigger():
    compactor, state, provider = compressor(), CompactionState(), SummaryProvider()
    history = [Message(role="user", content="Earlier question"),
               Message(role="assistant", content="Earlier answer"),
               Message(role="user", content="x" * 7200)]
    assert estimate_tokens(history) < 3000
    assert not compactor.needs_compaction(state, history, [], [], 3000)
    details = await compactor.compact(state, history, [], [], 3000, provider)
    assert details["compacted"] is False
    assert not provider.requests and state == CompactionState()


@pytest.mark.asyncio
async def test_oversized_history_can_fit_hard_budget_above_soft_trigger():
    compactor, state, provider = compressor(), CompactionState(), SummaryProvider()
    history = [Message(role="user", content="x" * 4000),
               Message(role="assistant", content="y" * 4000),
               Message(role="user", content="z" * 7200)]
    original = list(history)
    assert estimate_tokens(history) > 3000
    assert compactor.needs_compaction(state, history, [], [], 3000)
    details = await compactor.compact(state, history, [], [], 3000, provider)
    assert details["compacted"] is True
    assert 2250 < details["after_tokens_estimate"] <= 3000
    assert state.view(history)[-1] == original[-1] and history == original
    assert not compactor.needs_compaction(state, history, [], [], 3000)


@pytest.mark.asyncio
async def test_summary_missing_soft_target_preserves_fitting_original_view():
    class LongerSummaryProvider(SummaryProvider):
        async def chat(self, **kwargs):
            self.requests.append(kwargs)
            return Message(role="assistant", content="s" * 1100)
    compactor, state, provider = compressor(), CompactionState(), LongerSummaryProvider()
    history = [Message(role="user", content="x" * 1100),
               Message(role="assistant", content="y" * 1100),
               Message(role="user", content="z" * 5500)]
    assert estimate_tokens(history) < 3000
    assert compactor.needs_compaction(state, history, [], [], 3000)
    details = await compactor.compact(state, history, [], [], 3000, provider)
    assert details["compacted"] is False and details["summary_requests"] == 1
    assert state == CompactionState() and state.view(history) == history
    assert details["after_tokens_estimate"] == details["before_tokens_estimate"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_agent_sends_fitting_long_prompt_without_compaction_events(streaming):
    agent = Agent(config={"agent": {}, "providers": {}, "plugins": {}, "skills": {}})
    agent._is_initialized = True
    provider = SummaryProvider()
    provider.model_metadata = {"test": {"source": "provider", "context_window": 9000}}
    agent.providers.register("summary", provider, set_active=True)
    prompt = "x" * 14000
    events = []
    if streaming:
        events = [event async for event in agent.run_stream(prompt)]
        request = provider.streams[0]
    else:
        await agent.run(prompt, on_event=events.append)
        request = provider.requests[0]
    assert request["messages"][-1].content == prompt
    assert estimate_tokens(request["messages"]) <= agent.context_compactor.input_budget(9000, 2048)
    assert not any(event.type.value in {"context_compaction_started", "context_compacted", "error"}
                   for event in events)
    assert agent.compaction == CompactionState()


def test_verified_capacity_output_reserve_and_unknown_budget():
    c = compressor()
    assert c.input_budget(32000, 2048) == 28928
    assert c.input_budget(None, 2048) == 3000
    assert not ContextCompactor({"enabled": False}).needs_compaction(CompactionState(), archive(), [], [], 512)


@pytest.mark.asyncio
async def test_coding_compaction_keeps_authoritative_state_and_permissions():
    from firik_agent.projects.coding_runtime import CodingRunControl
    agent = Agent(config={"agent": {"context_compaction": {"fallback_input_tokens": 3000, "summary_max_tokens": 256}},
                          "providers": {}, "plugins": {}, "skills": {}})
    agent._is_initialized = True
    provider = SummaryProvider()
    agent.providers.register("summary", provider, set_active=True)
    session = ChatSession()
    session.messages = archive()
    control = CodingRunControl(session.session_id, "project", None, mode="ask")
    control.plan = [{"title": "Bitmemiş iş", "status": "pending"}]
    control.verifications = [{"command": "pytest", "passed": False, "exit_code": 1, "evidence_id": "failed-1"}]
    control.decisions = [{"allowed": False, "tool_name": "edit_file"}]
    gate = SimpleNamespace(get_schemas=lambda session: [], root="/tmp")
    events = [e async for e in agent.run_stream("Son soru", session=session, session_tool_filter=gate, run_control=control)]
    assert "context_compacted" in [e.type.value for e in events]
    assert not any(e.type.value == "error" for e in events)
    system = '\n'.join(m.content or '' for m in provider.streams[0]["messages"] if m.role == "system")
    task_data = '\n'.join(m.content or '' for m in provider.streams[0]["messages"] if m.role == "user")
    assert '"allowed": false' in task_data and '"passed": false' in task_data and 'failed-1' in task_data
    assert 'failed-1' not in system and '"allowed": false' not in system
    assert "Çalışma modu: ask" in system
    assert control.mode == "ask" and control.plan[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_cancellation_stops_waiting_provider_promptly():
    started = asyncio.Event()
    stopped = asyncio.Event()
    class WaitingProvider(SummaryProvider):
        async def chat(self, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
    async def checkpoint():
        if started.is_set():
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(compressor().compact(CompactionState(), archive(), [], [], 3000,
                                                    WaitingProvider(), checkpoint), timeout=2)
    assert stopped.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [Message(role='assistant', content=''), Message(role='assistant', content='x' * 10000)])
async def test_invalid_or_oversized_summary_does_not_change_cache(response):
    class InvalidProvider(SummaryProvider):
        async def chat(self, **kwargs):
            return response
    state = CompactionState()
    with pytest.raises(ValueError):
        await compressor().compact(state, archive(), [], [], 3000, InvalidProvider())
    assert state == CompactionState()


def test_ui_settings_cannot_overwrite_runtime_summary(tmp_path):
    session = ChatSession()
    store = ChatHistoryStore(tmp_path / "chats.db")
    store.save_transcript(session, [{"role": "user", "content": "test"}])
    session.messages = archive(5)
    session.compaction = CompactionState("yeni özet", 4, digest(session.messages[:4]))
    store.save_context(session)
    stale = ChatSession(session_id=session.session_id, model="new-model")
    store.save_transcript(stale, [{"role": "user", "content": "test"}])
    store.save_settings(stale)
    restored = store.restore_session(store.load(session.session_id))
    assert restored.compaction == session.compaction
    assert restored.model == "new-model"
