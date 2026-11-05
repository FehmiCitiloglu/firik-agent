"""Desktop voice acceptance through real routing, agent execution and audio HTTP.

Only capture/output devices and provider transports are replaced. The normal Tk
composer, ChatSession, Agent, history and voice adapters execute together.
"""
from dataclasses import replace
import json
import threading

import httpx
import pytest

from firik_agent.api.client import FirikAPI
from firik_agent.core.agent import Agent
from firik_agent.core.types import StreamChunk
from firik_agent.providers.registry import ProviderRegistry
from firik_agent.ui.service import FirikService
from firik_agent.voice.config import VoiceSettings
from test_ui_coding_workspace import workspace, settle
from test_ui_voice import foreground_voice_window
from test_ui_voice_experience import SyntheticCapture, until
from test_voice_engine_output import Backend, wav_bytes


def connect_pipeline(workspace, monkeypatch, *, auto_send=False, audio_failure=False,
                     transcript="Firik, mevcut projenin mimarisini açıkla.",
                     answer="Mevcut proje sohbet ve kodlama oturumlarını birlikte yönetir."):
    app, _, _, _ = workspace
    capture, playback = SyntheticCapture(), Backend()
    audio_requests, model_requests = [], []

    class ChatProvider:
        name = "firik"
        base_url = "https://chat.invalid/v1"
        model_metadata = {}
        current_model = "selected-chat-model"

        def set_model(self, model):
            self.current_model = model

        async def chat_stream(self, messages, **options):
            model_requests.append((self.current_model, messages, threading.get_ident()))
            yield StreamChunk(delta_content=answer)

    def providers(config):
        registry = ProviderRegistry()
        registry.register("firik", ChatProvider())
        return registry

    def audio_response(request):
        audio_requests.append((request, threading.get_ident()))
        if audio_failure:
            raise httpx.ConnectError("private transport detail", request=request)
        if request.url.path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": transcript, "language": "tr"})
        assert request.url.path == "/v1/audio/speech"
        return httpx.Response(200, content=wav_bytes(), headers={"Content-Type": "audio/wav"})

    monkeypatch.setattr(ProviderRegistry, "from_config", providers)
    monkeypatch.setattr("firik_agent.voice.audio.SoundDeviceRecorder", lambda: capture)
    monkeypatch.setattr("firik_agent.voice.playback.load_backend", lambda: playback)
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda *args, **kwargs: "fixture-key")
    monkeypatch.setattr("firik_agent.ui.service.FirikAPI",
        lambda *args, **kwargs: FirikAPI(*args, **kwargs, transport=httpx.MockTransport(audio_response)))
    monkeypatch.setattr(app.service, "chat_agent_stream_async",
        FirikService.chat_agent_stream_async.__get__(app.service, FirikService))
    app.service.config["voice"] = replace(VoiceSettings(), enabled=True, language="tr",
        read_replies=not audio_failure, auto_send=auto_send, review_before_send=not auto_send).to_dict()
    app.show_view("chat")
    view = app.views["chat"].current
    view.session.active_mcp_servers.clear()
    view.session.model = "selected-chat-model"
    view.model_combo.configure(values=["selected-chat-model"])
    view.model_combo.set("selected-chat-model")
    foreground_voice_window(app)
    return app, view, capture, playback, audio_requests, model_requests, transcript, answer


@pytest.mark.parametrize("auto_send", [False, True])
def test_turkish_capture_reaches_existing_agent_and_voxcpm_playback(workspace, monkeypatch, auto_send):
    app, view, capture, playback, audio, model, transcript, answer = connect_pipeline(
        workspace, monkeypatch, auto_send=auto_send)
    session = view.session
    panel = view.voice_panel
    panel._begin_hold("pointer")
    until(app, lambda: bool(capture.windows))
    assert panel.controller.state == "recording"
    panel._finish_hold()
    if not auto_send:
        until(app, lambda: panel._pending)
        assert panel.command_text.get() == transcript and not model
        panel.apply_transcript()
    until(app, lambda: len(audio) >= 2 and bool(playback.streams))
    until(app, lambda: all(stream.closed for stream in playback.streams) and not view.is_streaming)
    assert view.session is session and session.model == "selected-chat-model"
    assert len(model) == 1 and model[0][0] == session.model
    assert isinstance(app.service.get_agent(session.session_id), Agent)
    assert [item[0].url.path for item in audio] == ["/v1/audio/transcriptions", "/v1/audio/speech"]
    assert b"qwen3-asr-1.7b" in audio[0][0].content
    assert json.loads(audio[1][0].content)["model"] == "voxcpm2"
    assert json.loads(audio[1][0].content)["input"] == answer
    assert all(worker != threading.get_ident() for _, worker in audio)
    assert model[0][2] != threading.get_ident()
    assert session.messages[0].content == "mevcut projenin mimarisini açıkla."
    assert session.messages[-1].content == answer
    assert view.messages[-1]["content"] == answer
    history = app.service.chat_history.load(session.session_id)
    assert history and history["transcript"][-1]["content"] == answer
    panel.stop_speaking()
    assert view.messages[-1]["content"] == answer
    assert capture.windows[0]["closed"].is_set()


def test_disconnected_asr_recovers_through_same_text_chat(workspace, monkeypatch):
    app, view, capture, _, audio, model, _, answer = connect_pipeline(
        workspace, monkeypatch, audio_failure=True)
    panel = view.voice_panel
    panel._begin_hold("pointer")
    until(app, lambda: bool(capture.windows))
    panel._finish_hold()
    until(app, lambda: panel.controller.state_id.value == "error")
    settle(app, .05)
    assert panel.error_actions.winfo_ismapped()
    assert "private transport detail" not in panel.status.cget("text")
    assert not model and not view.is_streaming and len(audio) == 1
    panel.use_text_input()
    view.input_textbox.insert("1.0", "Metinle devam et.")
    view._on_send_pressed()
    until(app, lambda: bool(model) and not view.is_streaming)
    assert view.messages[-1]["content"] == answer
    assert len(audio) == 1


def test_spoken_project_test_summary_uses_existing_coding_run(workspace, monkeypatch):
    transcript = "Firik, mevcut proje testlerini özetle."
    answer = "Test dosyaları proje kapsamında incelenebilir. Bu istekte test çalıştırılmadı."
    app, _, capture, playback, audio, model, _, _ = connect_pipeline(
        workspace, monkeypatch, transcript=transcript, answer=answer)
    _, projects, project, _ = workspace
    settings = projects.project_service.db.get_settings(project.id)
    settings.coding_model = "selected-coding-model"
    projects.project_service.db.save_settings(settings)
    app.show_view("projects")
    projects._switch_workspace_tab("coding")
    panel = projects.coding_voice_panel
    state = projects._coding_state()
    session = state["session"]
    session.active_mcp_servers.clear()
    foreground_voice_window(app)
    panel._begin_hold("pointer")
    until(app, lambda: bool(capture.windows))
    panel._finish_hold()
    until(app, lambda: panel._pending)
    panel.apply_transcript()
    until(app, lambda: bool(model) and not state["busy"])
    until(app, lambda: len(audio) >= 2 and bool(playback.streams))
    until(app, lambda: panel.controller.state in ("idle", "ready"))
    assert state["session"] is session and session.model == "selected-coding-model"
    assert model[0][0] == "selected-coding-model"
    record = app.service.coding_store.latest_run(project.id, session_id=session.session_id)
    assert record and record.status == "completed"
    assert record.messages[0]["execution"]["origin"] == "voice"
    assert record.messages[-1]["content"] == answer
    assert any(message["content"] == answer for message in state["messages"])
    speech = " ".join(json.loads(request.content)["input"] for request, _ in audio
                      if request.url.path == "/v1/audio/speech")
    assert len(speech) <= 600
    assert "test çalıştırılmadı" in speech.lower()
