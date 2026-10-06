"""Persistent transcripts and complete, resumable agent context."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from firik_agent.core.chat_history import ChatHistoryStore
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message, ToolCall, ToolCallFunction


def tool_call(call_id="call-1"):
    return ToolCall(id=call_id, function=ToolCallFunction(name="run_command", arguments='{"command": "pwd"}'))


def test_restart_restores_messages_tools_and_settings(tmp_path):
    path = tmp_path / "chats.db"
    store = ChatHistoryStore(path)
    session = ChatSession(model="chosen-model", system_prompt="Türkçe yanıtla", temperature=0.4,
                          max_tokens=4096, active_mcp_servers={"blender"})
    session.messages = [Message(role="user", content="Sahne oluştur"),
                        Message(role="assistant", tool_calls=[tool_call()], reasoning="Model düşüncesi"),
                        Message(role="tool", tool_call_id="call-1", name="run_command", content="Çalışma dizini"),
                        Message(role="assistant", content="Sahne hazır.")]
    transcript = [{"role": "user", "content": "Sahne oluştur"},
                  {"role": "assistant", "content": "Sahne hazır.", "activity": {"tools": [{"output": "Çalışma dizini"}]}}]
    store.save_transcript(session, transcript)
    store.save_context(session)
    reopened = ChatHistoryStore(path)
    record = reopened.load(session.session_id)
    restored = reopened.restore_session(record)
    assert record["transcript"] == transcript
    assert restored.messages == session.messages
    assert restored.model == "chosen-model"
    assert restored.system_prompt == "Türkçe yanıtla"
    assert restored.temperature == 0.4
    assert restored.max_tokens == 4096
    assert restored.active_mcp_servers == {"blender"}
    assert restored.created_at == session.created_at
    assert reopened.list_conversations()[0]["title"] == "Sahne oluştur"


def test_ui_updates_keep_runtime_context_and_new_settings(tmp_path):
    store = ChatHistoryStore(tmp_path / "chats.db")
    session = ChatSession()
    transcript = [{"role": "user", "content": "Merhaba"}]
    store.save_transcript(session, transcript)
    session.messages = [Message(role="user", content="Merhaba"), Message(role="assistant", content="Yanıt")]
    store.save_context(session)
    stale_ui_session = ChatSession(session_id=session.session_id, model="new-model", temperature=0.2)
    store.save_transcript(stale_ui_session, transcript + [{"role": "assistant", "content": "Yanıt"}])
    restored = store.restore_session(store.load(session.session_id))
    assert restored.messages == session.messages
    assert restored.model == "new-model"
    assert restored.temperature == 0.2


def test_concurrent_transcript_and_context_writes_do_not_lose_data(tmp_path):
    path = tmp_path / "chats.db"
    store = ChatHistoryStore(path)
    session = ChatSession()
    transcript = [{"role": "user", "content": "Soru"}, {"role": "assistant", "content": "Yanıt"}]
    store.save_transcript(session, transcript)
    session.messages = [Message(role="user", content="Soru"), Message(role="assistant", content="Yanıt")]
    barrier = threading.Barrier(2)

    def save_ui():
        local = ChatHistoryStore(path)
        barrier.wait(timeout=5)
        for _ in range(20):
            local.save_transcript(session, transcript)

    def save_runtime():
        local = ChatHistoryStore(path)
        barrier.wait(timeout=5)
        for _ in range(20):
            local.save_context(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save_ui), pool.submit(save_runtime)]
        for future in futures:
            future.result(timeout=10)
    record = store.load(session.session_id)
    assert record["transcript"] == transcript
    assert store.restore_session(record).messages == session.messages


def test_deleted_chat_is_not_recreated_by_late_worker(tmp_path):
    store = ChatHistoryStore(tmp_path / "chats.db")
    session = ChatSession()
    store.save_transcript(session, [{"role": "user", "content": "Silinecek"}])
    store.delete(session.session_id)
    session.messages.append(Message(role="assistant", content="Geç yanıt"))
    store.save_context(session)
    store.save_settings(session)
    assert store.load(session.session_id) is None
    assert store.list_conversations() == []


def test_interrupted_tools_are_closed_without_reexecution(tmp_path):
    store = ChatHistoryStore(tmp_path / "chats.db")
    session = ChatSession()
    session.messages = [Message(role="user", content="İki işlem"),
                        Message(role="assistant", tool_calls=[tool_call("a"), tool_call("b")]),
                        Message(role="tool", tool_call_id="a", name="run_command", content="İlk sonuç")]
    store.save_transcript(session, [{"role": "user", "content": "İki işlem"},
                                   {"role": "assistant", "content": "Kısmi yanıt", "pending": True}])
    restored = store.restore_session(store.load(session.session_id))
    assert restored.messages[2].content == "İlk sonuç"
    assert restored.messages[3].role == "tool"
    assert restored.messages[3].tool_call_id == "b"
    assert "kesildi" in restored.messages[3].content
    assert restored.messages[4].content == "Kısmi yanıt"


def test_prompt_saved_before_worker_starts_survives_restart(tmp_path):
    store = ChatHistoryStore(tmp_path / "chats.db")
    session = ChatSession()
    session.messages = [Message(role="user", content="Önceki soru"), Message(role="assistant", content="Önceki yanıt")]
    transcript = [message.to_dict() for message in session.messages]
    store.save_transcript(session, transcript)
    transcript.extend([{"role": "user", "content": "Yeni soru"}, {"role": "assistant", "content": "", "pending": True}])
    store.save_transcript(session, transcript)
    restored = store.restore_session(store.load(session.session_id))
    assert [message.content for message in restored.messages] == ["Önceki soru", "Önceki yanıt", "Yeni soru"]


def test_empty_chat_is_not_saved_and_newest_is_listed_first(tmp_path):
    store = ChatHistoryStore(tmp_path / "chats.db")
    first, second = ChatSession(), ChatSession()
    store.save_transcript(first, [])
    assert store.list_conversations() == []
    store.save_transcript(first, [{"role": "user", "content": " İlk\n soru "}])
    store.save_transcript(second, [{"role": "user", "content": [{"type": "text", "text": "Görsel sorusu"}]}])
    assert [row["session_id"] for row in store.list_conversations()] == [second.session_id, first.session_id]
    assert store.list_conversations()[1]["title"] == "İlk soru"


def test_service_reopens_disk_session_after_restart(tmp_path, monkeypatch):
    from firik_agent.ui.service import FirikService
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    first = FirikService()
    try:
        session = first.get_or_create_session()
        session.messages = [Message(role="user", content="Kalıcı soru"), Message(role="assistant", content="Kalıcı yanıt")]
        first.save_chat_transcript(session, [message.to_dict() for message in session.messages])
        first.chat_history.save_context(session)
    finally:
        first.projects.scheduler.stop()
        first.shutdown()
    second = FirikService()
    try:
        restored = second.get_or_create_session(session.session_id)
        assert restored.messages == session.messages
        assert second.list_chat_history()[0]["session_id"] == session.session_id
        assert second.load_chat_transcript(session.session_id)[1]["content"] == "Kalıcı yanıt"
    finally:
        second.projects.scheduler.stop()
        second.shutdown()


def test_completed_runtime_reply_recovers_before_gui_callbacks_run(tmp_path):
    store = ChatHistoryStore(tmp_path / "chats.db")
    session = ChatSession()
    store.save_transcript(session, [{"role": "user", "content": "Soru"},
                                   {"role": "assistant", "content": "Kısmi", "pending": True}])
    session.messages = [Message(role="user", content="Soru"),
                        Message(role="assistant", content="İşlemi hazırlıyorum.", tool_calls=[tool_call()], reasoning="Düşünce"),
                        Message(role="tool", tool_call_id="call-1", name="run_command", content="Tam çıktı"),
                        Message(role="assistant", content="Tam yanıt.")]
    store.save_context(session)
    record = ChatHistoryStore(store.path).load(session.session_id)
    assert record["transcript"][1]["content"] == "İşlemi hazırlıyorum.\n\nTam yanıt."
    assert not record["transcript"][1]["pending"]
    assert record["transcript"][1]["activity"]["tools"][0]["output"] == "Tam çıktı"
    assert record["transcript"][1]["activity"]["reasoning"] == "Düşünce"
    assert not record["transcript"][1]["activity"]["running"]
