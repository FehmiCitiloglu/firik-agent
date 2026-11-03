"""Real desktop conversational sessions, with only native/network I/O replaced."""
from dataclasses import replace
import io
import threading
import time
import wave

import httpx

from firik_agent.api.client import FirikAPI
from firik_agent.voice.config import VoiceSettings
from test_ui_coding_workspace import workspace, settle


def until(app, predicate, timeout=4):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        settle(app, .02)
    assert predicate()


def test_real_conversation_followups_use_one_chat_and_pause_resume_cleanly(workspace, monkeypatch):
    """VAD utterances -> real runtime/HTTP -> actual chat submit -> follow-up."""
    app, projects, project, streams = workspace
    main_thread = threading.get_ident()
    requests, spoken, workers, delivered = [], [], [], []
    app.service.config["voice"] = replace(
        VoiceSettings(), enabled=True, read_replies=False,
        auto_send=True, review_before_send=False,
    ).to_dict()
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda *args, **kwargs: "fixture-key")

    class LocalVAD:
        def __init__(self):
            self.windows = []
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def record_utterance(self, stop, cancel, max_seconds, device=None,
                             on_speech_start=None, **options):
            workers.append(threading.get_ident())
            window = {"end": threading.Event(), "closed": threading.Event(), "cancel": cancel}
            with self.lock:
                self.windows.append(window)
                index = len(self.windows)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                deadline = time.monotonic() + 5
                while not (window["end"].is_set() or stop.is_set() or cancel.is_set()):
                    if time.monotonic() >= deadline:
                        raise AssertionError("Test did not finish its simulated local VAD window")
                    cancel.wait(.01)
                if stop.is_set() or cancel.is_set():
                    return b""
                on_speech_start()
                output = io.BytesIO()
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(16000)
                    wav.writeframes(b"\xe8\x03\x18\xfc" * 2400)
                return output.getvalue()
            finally:
                with self.lock:
                    self.active -= 1
                window["closed"].set()

    recorder = LocalVAD()
    monkeypatch.setattr("firik_agent.voice.audio.SoundDeviceRecorder", lambda: recorder)

    class Speaker:
        def speak(self, text, cancel, language, rate):
            spoken.append(text)
    monkeypatch.setattr("firik_agent.voice.synthesis.EngineSpeaker", lambda **kwargs: Speaker())
    transcripts = ["Firik, explain the current project architecture.",
                   "Firik, explain that again in Turkish."]

    def respond(request):
        workers.append(threading.get_ident())
        requests.append(request)
        return httpx.Response(200, json={"text": transcripts[len(requests) - 1]})

    monkeypatch.setattr("firik_agent.ui.service.FirikAPI", lambda *args, **kwargs:
                        FirikAPI(*args, **kwargs, transport=httpx.MockTransport(respond)))
    app.show_view("chat")
    view = app.views["chat"].current
    panel = view.voice_panel
    session_id = view.session.session_id
    original = panel._on_transcript

    def received(text):
        delivered.append((threading.get_ident(), text))
        original(text)
    panel._on_transcript = received
    panel.session_button.invoke()
    until(app, lambda: len(recorder.windows) == 1)
    assert panel.controller.conversation_active
    assert not requests and not streams  # Ambient waiting is entirely local.
    assert not [message for message in view.messages if message["role"] == "user"]

    recorder.windows[0]["end"].set()
    until(app, lambda: len(streams) == 1)
    assert streams[0]["session_id"] == session_id
    assert streams[0]["prompt"] == "explain the current project architecture."
    assert panel.controller.state == "thinking"
    assert len(recorder.windows) == 1  # No mic reopening while the agent works.
    assert recorder.windows[0]["closed"].is_set()
    streams[0]["on_done"]("The project uses the existing services.")
    until(app, lambda: len(recorder.windows) == 2)
    assert not spoken  # Follow-up listening resumes with speech output disabled.

    recorder.windows[1]["end"].set()
    until(app, lambda: len(streams) == 2)
    assert streams[1]["session_id"] == session_id == view.session.session_id
    assert streams[1]["prompt"] == "explain that again in Turkish."
    assert len(app.views["chat"].pages) == 1
    panel.pause_button.invoke()
    assert panel.controller.paused and panel.controller.conversation_active
    panel.pause_button.invoke()
    assert not panel.controller.paused
    assert len(recorder.windows) == 2  # Resuming voice does not cancel/restart the agent.
    streams[1]["on_done"]("Projenin mimarisi mevcut hizmetleri kullanır.")
    until(app, lambda: len(recorder.windows) == 3)

    panel.stop_button.invoke()
    until(app, lambda: recorder.windows[2]["closed"].is_set())
    assert panel.controller.paused and recorder.active == 0
    panel.pause_button.invoke()
    until(app, lambda: len(recorder.windows) == 4)
    panel.exit_button.invoke()
    until(app, lambda: recorder.windows[3]["closed"].is_set())
    assert not panel.controller.conversation_active
    assert not panel.controller.resume_conversation()
    assert recorder.active == 0 and recorder.max_active == 1
    assert len(requests) == len(streams) == 2
    assert all(request.url.path.endswith("/audio/transcriptions") for request in requests)
    assert all(request.headers["X-API-Key"] == "fixture-key" for request in requests)
    assert all(thread_id != main_thread for thread_id in workers)
    assert [thread_id for thread_id, _ in delivered] == [main_thread, main_thread]
    assert len([message for message in view.messages if message["role"] == "user"]) == 2
