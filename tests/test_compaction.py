"""Behavioral evidence for incremental compression, safe tool boundaries and recovery."""
import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from evren_agent.core.agent import Agent
from evren_agent.core.chat_history import ChatHistoryStore
from evren_agent.core.compaction import CompactionState, ContextCompactor, digest, estimate_tokens, safe_boundaries
from evren_agent.core.session import ChatSession
from evren_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction


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
    assert types[:3] == ["context_compaction_started", "context_compacted", "model_request_started"]
    assert "done" in types and "error" not in types
    assert provider.streams[0]["messages"][-1].content == "Son istek"
    assert provider.streams[0]["max_tokens"] == session.max_tokens
    assert len(session.messages) == 62
    assert estimate_tokens(provider.streams[0]["messages"]) < 2250


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
    state = CompactionState()
    with pytest.raises(ValueError, match="bağlam bütçesine"):
        await compressor().compact(state, history, [], [], 3000, SummaryProvider())
    assert state.cursor == 0 and len(history[0].content) == 20000


def test_verified_capacity_output_reserve_and_unknown_budget():
    c = compressor()
    assert c.input_budget(32000, 2048) == 28928
    assert c.input_budget(None, 2048) == 3000
    assert not ContextCompactor({"enabled": False}).needs_compaction(CompactionState(), archive(), [], [], 512)


@pytest.mark.asyncio
async def test_coding_compaction_keeps_authoritative_state_and_permissions():
    from evren_agent.projects.coding_runtime import CodingRunControl
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
    assert '"allowed": false' in system and '"passed": false' in system and 'failed-1' in system
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
