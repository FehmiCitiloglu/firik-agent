"""Real Tk voice integration: reviewed dictation, existing sessions, safe controls."""
from dataclasses import replace
import time

import customtkinter as ctk
import pytest

from firik_agent.core.events import AgentEventType
from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.views.chat_workspace import ChatWorkspace
from firik_agent.ui.components.voice_panel import VoicePanel
from firik_agent.voice.config import VoiceSettings
from test_ui_chat import ChatServiceStub, emit, pump, send
from test_ui_coding_workspace import workspace, settle


class Controller:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.state = "idle"
        self.spoken, self.cancelled = [], 0
        self.closed = False
    def start_recording(self):
        self.state = "recording"
        self.callbacks["on_state"](self.state)
    def stop_recording(self):
        self.state = "transcribing"
        self.callbacks["on_state"](self.state)
    def speak(self, text):
        self.spoken.append(text)
    def cancel(self):
        self.cancelled += 1
        self.state = "idle"
    def close(self):
        self.closed = True


class Service(ChatServiceStub):
    def __init__(self, path):
        super().__init__(path)
        self.voice_settings = replace(VoiceSettings(), enabled=True, read_replies=True)
        self.controllers = []
    def create_voice_controller(self, **callbacks):
        controller = Controller(**callbacks)
        self.controllers.append(controller)
        return controller
    def update_voice_settings(self, settings):
        self.voice_settings = settings


@pytest.fixture
def voice_chat(tmp_path):
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("1120x800")
    service = Service(tmp_path / "chats.db")
    workspace = ChatWorkspace(root, service)
    workspace.pack(fill="both", expand=True)
    pump(root)
    yield root, workspace, service
    root.destroy()


def dictate(panel, service, text):
    panel.toggle_recording()
    controller = service.controllers[-1]
    panel.toggle_recording()
    controller.state = "idle"
    # The real controller emits its legacy idle alias after finalized PTT ASR.
    controller.callbacks["on_state"]("idle")
    controller.callbacks["on_transcript"](text)
    return controller


def foreground_voice_window(root):
    """Automatic speech requires actual foreground focus, including on Aqua."""
    root.deiconify()
    root.lift()
    root.focus_force()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        pump(root, .01)
        focused = root.focus_displayof()
        if focused is not None and focused.winfo_toplevel() is root:
            return
    focused = root.focus_displayof()
    assert focused is not None and focused.winfo_toplevel() is root


def test_dictation_review_uses_existing_model_session_and_completion(voice_chat):
    root, workspace, service = voice_chat
    view = workspace.current
    view.input_textbox.insert("1.0", "Önceki taslak:")
    controller = dictate(view.voice_panel, service, "Türkçe and English")
    assert not service.streams
    assert view.input_textbox.get("1.0", "end-1c") == "Önceki taslak:"
    assert view.voice_panel.command_text.get() == "Türkçe and English"
    view.voice_panel.apply_transcript()
    assert not service.streams  # Never overwrite an existing typed draft.
    view.input_textbox.delete("1.0", "end")
    sid, model = view.session.session_id, view.model_combo.get()
    view.voice_panel.apply_transcript()
    stream = service.streams[-1]
    assert stream["prompt"] == "Türkçe and English"
    assert stream["session_id"] == sid and view.session.model == model
    foreground_voice_window(root)
    emit(root, stream, AgentEventType.DONE, content="Tamamlandı.")
    stream["on_done"]("Tamamlandı.")
    pump(root)
    assert controller.spoken == ["Tamamlandı."]
    assert controller.cancelled == 0


def test_switching_chat_rejects_delayed_transcript_and_speech(voice_chat):
    root, workspace, service = voice_chat
    old = workspace.current
    old.voice_panel.toggle_recording()
    controller = service.controllers[-1]
    old.input_textbox.insert("1.0", "Taslak")
    workspace.new_chat()
    new = workspace.current
    assert new is not old and controller.cancelled
    controller.callbacks["on_transcript"]("Yanlış oturuma gitmesin")
    old.voice_panel.response_completed("Gecikmiş yanıt")
    assert old.input_textbox.get("1.0", "end-1c") == "Taslak"
    assert not new.input_textbox.get("1.0", "end-1c")
    assert not controller.spoken


def test_reviewed_commands_cancel_only_originating_session_and_reject_approval(voice_chat):
    root, workspace, service = voice_chat
    view = workspace.current
    stream = send(view, service)
    panel = view.voice_panel
    panel.mode.set("Sesli komut")
    dictate(panel, service, "yanıtı durdur")
    assert not service.cancelled_sessions
    panel.apply_command()
    assert service.cancelled_sessions == [stream["session_id"]]
    assert not view.is_streaming
    for text in ("evet", "approve", "planı onayla", "run rm -rf /", "yeni sohbet ve çalıştır"):
        panel.dismiss_command()
        dictate(panel, service, text)
        panel.apply_command()
        assert ("Sesli onay kabul edilmez" if text in {"evet", "approve", "planı onayla"}
                else "Komut tanınmadı") in panel.status.cget("text")
    assert len(service.streams) == 1


def test_errors_and_disabled_voice_keep_text_operation(voice_chat):
    root, workspace, service = voice_chat
    view = workspace.current
    controller = dictate(view.voice_panel, service, "hello")
    stream = send(view, service, "text")
    emit(root, stream, AgentEventType.ERROR, content="offline")
    assert not controller.spoken
    service.voice_settings = VoiceSettings()
    view.voice_panel.response_completed("Yanıt")
    assert not controller.spoken
    send(view, service, "Metin çalışıyor")
    assert service.streams[-1]["prompt"] == "Metin çalışıyor"


def test_settings_dialog_saves_integer_duration_and_device_zero(voice_chat, monkeypatch):
    root, workspace, service = voice_chat
    service.voice_settings = replace(service.voice_settings, input_device=0)
    panel = workspace.current.voice_panel
    errors = []
    monkeypatch.setattr("firik_agent.ui.components.voice_panel.messagebox.showerror", lambda *a, **kw: errors.append(a))
    panel.open_settings()
    dialog = panel._dialog
    save = next(w for w in dialog.winfo_children() if isinstance(w, ctk.CTkButton) and w.cget("text") == "Kaydet")
    save.invoke()
    assert not errors
    assert service.voice_settings.input_device == 0
    assert service.voice_settings.max_record_seconds == 60


def test_coding_voice_uses_project_run_path_and_preserves_approvals(workspace, monkeypatch):
    app, view, project, streams = workspace
    settings = replace(VoiceSettings(), enabled=True, read_replies=True)
    app.service.config["voice"] = settings.to_dict()
    controllers = []
    def create(**callbacks):
        controller = Controller(**callbacks)
        controllers.append(controller)
        return controller
    monkeypatch.setattr(app.service, "create_voice_controller", create)
    panel = view.coding_voice_panel
    panel.toggle_recording()
    controllers[-1].state = "idle"
    controllers[-1].callbacks["on_state"]("idle")
    controllers[-1].callbacks["on_transcript"]("main.py dosyasını incele")
    assert not streams
    panel.apply_transcript()
    assert streams[-1]["session_id"] == view._coding_state()["session"].session_id
    assert streams[-1]["cwd"] == project.local_path
    assert "main.py" in streams[-1]["prompt"]
    foreground_voice_window(app)
    streams[-1]["on_done"]("İncelendi.")
    settle(app)
    assert len(controllers[-1].spoken) == 1
    assert "İncelendi." in controllers[-1].spoken[0] and project.name in controllers[-1].spoken[0]
    panel.mode.set("Sesli komut")
    panel.toggle_recording()
    controllers[-1].callbacks["on_transcript"]("approve")
    panel.apply_command()
    assert "Sesli onay kabul edilmez" in panel.status.cget("text")
    app.show_view("chat")
    assert controllers[-1].cancelled >= 1


def test_voice_controls_wrap_inside_compact_coding_pane(tmp_path):
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("330x260")
    panel = VoicePanel(root, Service(tmp_path / "chats.db"), on_dictation=lambda text: None, actions={})
    panel.pack(fill="x")
    try:
        pump(root, .1)
        assert panel.settings_button.winfo_y() > panel.mic_button.winfo_y()
        for widget in (panel.mic_button, panel.mode, panel.stop_button, panel.settings_button):
            assert widget.winfo_x() >= 0
            assert widget.winfo_x() + widget.winfo_width() <= panel.winfo_width()
    finally:
        root.destroy()


def test_real_desktop_pipeline_queues_audio_http_and_speech_on_existing_session(workspace, monkeypatch):
    """Exercise real service/controller/runtime/HTTP client, injecting only I/O."""
    import threading
    import time
    import httpx
    from test_voice_service import microphone_wav
    from firik_agent.api.client import FirikAPI
    app, projects, project, streams = workspace
    main_thread = threading.get_ident()
    worker_threads, spoken, requests = [], [], []
    app.service.config["voice"] = replace(VoiceSettings(), enabled=True, read_replies=True).to_dict()
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda *args, **kwargs: "fixture-key")
    class Recorder:
        def record(self, stop, cancel, max_seconds, device=None):
            worker_threads.append(threading.get_ident())
            stop.wait(2)
            return microphone_wav()
    class Speaker:
        def speak(self, text, cancel, language, rate):
            worker_threads.append(threading.get_ident())
            spoken.append(text)
    def respond(request):
        worker_threads.append(threading.get_ident())
        requests.append(request)
        return httpx.Response(200, json={"text": "Merhaba İstanbul hello"})
    monkeypatch.setattr("firik_agent.voice.audio.SoundDeviceRecorder", Recorder)
    monkeypatch.setattr("firik_agent.voice.synthesis.EngineSpeaker", lambda **kwargs: Speaker())
    monkeypatch.setattr("firik_agent.ui.service.FirikAPI", lambda *a, **kw: FirikAPI(*a, **kw, transport=httpx.MockTransport(respond)))
    app.show_view("chat")
    view = app.views["chat"].current
    delivered = []
    original = view.voice_panel._on_transcript
    def dictation(text):
        delivered.append(threading.get_ident())
        original(text)
    view.voice_panel._on_transcript = dictation
    view.voice_panel.mic_button.invoke()
    view.voice_panel.mic_button.invoke()
    deadline = time.monotonic() + 3
    while not delivered and time.monotonic() < deadline:
        settle(app, .02)
    assert delivered == [main_thread]
    assert requests and requests[0].headers["X-API-Key"] == "fixture-key"
    assert not streams
    assert view.voice_panel.command_text.get() == "Merhaba İstanbul hello"
    assert view.input_textbox.get("1.0", "end-1c") == ""
    view.voice_panel.apply_transcript()
    stream = streams[-1]
    assert stream["session_id"] == view.session.session_id
    foreground_voice_window(app)
    stream["on_done"]("Normal ajan yanıtı")
    deadline = time.monotonic() + 3
    while not spoken and time.monotonic() < deadline:
        settle(app, .02)
    assert spoken == ["Normal ajan yanıtı"]
    assert worker_threads and all(thread_id != main_thread for thread_id in worker_threads)


def test_voice_auto_send_never_cancels_active_chat(voice_chat):
    root, workspace, service = voice_chat
    service.voice_settings = replace(service.voice_settings, auto_send=True, review_before_send=False)
    view = workspace.current
    stream = send(view, service, "existing work")
    dictate(view.voice_panel, service, "Firik, explain the current project architecture.")
    pump(root)
    assert len(service.streams) == 1 and not service.cancelled_sessions
    assert view.voice_panel.command_text.get().startswith("Firik")
    emit(root, stream, AgentEventType.DONE, content="Done")
    view.voice_panel.apply_transcript()
    assert len(service.streams) == 2
    assert service.streams[-1]["prompt"] == "explain the current project architecture."
    assert service.streams[-1]["session_id"] == stream["session_id"]


def test_voice_auto_send_sends_ordinary_requests_once_and_reviews_sensitive(voice_chat):
    root, workspace, service = voice_chat
    service.voice_settings = replace(service.voice_settings, auto_send=True, review_before_send=False)
    panel = workspace.current.voice_panel
    dictate(panel, service, "Firik, summarize the test failures.")
    pump(root)
    panel.apply_transcript()
    assert len(service.streams) == 1
    assert service.streams[0]["prompt"] == "summarize the test failures."
    emit(root, service.streams[0], AgentEventType.DONE, content="No failures")
    dictate(panel, service, "delete the project")
    pump(root)
    assert len(service.streams) == 1 and panel.command_text.get() == "delete the project"


def test_voice_submission_never_cancels_active_coding(workspace, monkeypatch):
    app, view, project, streams = workspace
    assert view._submit_coding_voice_transcript("explain the architecture")
    calls = []
    monkeypatch.setattr(app.service, "coding_control", lambda *args: calls.append(args))
    assert not view._submit_coding_voice_transcript("summarize the tests")
    assert not calls and len(streams) == 1
    assert view._coding_state()["busy"]


def test_voice_navigation_reuses_existing_coding_session(workspace):
    app, view, project, streams = workspace
    session_id = view._coding_state()["session"].session_id
    app.show_view("chat")
    assert app.open_existing_coding_conversation()
    assert app.active_tab == "projects" and view.active_workspace_tab == "coding"
    assert view._coding_state()["session"].session_id == session_id
    assert not streams


def test_application_close_does_not_wait_for_service_shutdown(workspace, monkeypatch):
    import threading
    import time
    app, view, project, streams = workspace
    entered, release = threading.Event(), threading.Event()
    shutdown = app.service.shutdown
    def slow_shutdown():
        entered.set()
        assert release.wait(3)
        shutdown()
    monkeypatch.setattr(app.service, "shutdown", slow_shutdown)
    try:
        started = time.monotonic()
        app.destroy()
        assert time.monotonic() - started < .5
        assert entered.wait(1) and app._shutdown_thread.is_alive()
    finally:
        release.set()
        app._shutdown_thread.join(3)
    assert not app._shutdown_thread.is_alive()


def test_running_agent_events_do_not_discard_new_spoken_interruption(workspace, monkeypatch):
    import threading
    app, projects, project, streams = workspace
    app.service.config["voice"] = replace(VoiceSettings(), enabled=True).to_dict()
    entered, ended = threading.Event(), threading.Event()
    captured = []
    class Recorder:
        def record(self, stop, cancel, max_seconds, device=None):
            captured.append(cancel)
            entered.set()
            cancel.wait(3)
            ended.set()
            return b""
    monkeypatch.setattr("firik_agent.voice.audio.SoundDeviceRecorder", Recorder)
    app.show_view("chat")
    view = app.views["chat"].current
    view.input_textbox.insert("1.0", "existing work")
    view.send_btn.invoke()
    panel = view.voice_panel
    panel.toggle_recording()
    assert entered.wait(1)
    emit(app, streams[0], AgentEventType.TOOL_CALL_STARTED,
         tool_name="read_file", server_name="local", arguments={"path": "main.py"})
    assert not captured[0].is_set() and panel.controller.state == "recording"
    assert view.is_streaming and len(streams) == 1
    panel.stop_listening()
    assert ended.wait(1) and view.is_streaming
