"""Voice control behavior using real desktop widgets and injected audio owners."""
from dataclasses import replace
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from firik_agent.ui.app import _ensure_tk_environment
from firik_agent.ui.components.voice_panel import VoicePanel
from firik_agent.voice.config import VoiceSettings
from test_ui_chat import pump


class Controller:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.state = "idle"
        self.session = None
        self.events = []
        self.conversation_active = False
        self.paused = False

    def bind_session(self, session):
        self.session = session

    def _event(self, name, state=None):
        self.events.append(name)
        if state:
            self.state = state
            self.callbacks["on_state"](state)
        return True

    def start_recording(self):
        return self._event("capture", "recording")

    def stop_recording(self):
        self._event("finalize", "transcribing")

    def start_conversation(self):
        self.conversation_active = True
        return self._event("conversation", "listening")

    def pause_conversation(self):
        self.paused = True
        self._event("pause", "interrupted")

    def resume_conversation(self):
        self.paused = False
        return self._event("resume", "listening")

    def exit_conversation(self):
        self.conversation_active = False
        self._event("exit", "idle")

    def stop_listening(self):
        self._event("stop_listening", "interrupted")

    def stop_speaking(self):
        self._event("stop_speaking")

    def transcript_resolved(self, submitted=False):
        self.events.append(("resolved", submitted))

    def mark_agent_state(self, state):
        self._event("agent", state)

    def response_completed(self, text):
        self.events.append(("completed", text))

    def cancel(self):
        self._event("cancel", "idle")

    def close(self):
        self._event("close")


@pytest.fixture
def panel():
    _ensure_tk_environment()
    root = ctk.CTk()
    root.geometry("550x600")
    service = SimpleNamespace(voice_settings=replace(VoiceSettings(), enabled=True))
    controllers, submitted, dictated, cancelled = [], [], [], []
    def create(**callbacks):
        controller = Controller(**callbacks)
        controllers.append(controller)
        return controller
    service.create_voice_controller = create
    service.update_voice_settings = lambda settings: setattr(service, "voice_settings", settings)
    session = ["session-one"]
    busy = [False]
    widget = VoicePanel(root, service, on_dictation=dictated.append,
                        on_submit=lambda text: submitted.append(text) or True,
                        get_session_id=lambda: session[0], is_busy=lambda: busy[0],
                        actions={"stop_response": lambda: cancelled.append(session[0])})
    widget.pack(fill="both", expand=True)
    pump(root, .05)
    yield SimpleNamespace(root=root, panel=widget, service=service, controllers=controllers,
                          submitted=submitted, dictated=dictated, cancelled=cancelled,
                          session=session, busy=busy)
    root.destroy()


def recognize(fixture, text):
    fixture.panel.toggle_recording()
    controller = fixture.controllers[-1]
    fixture.panel.toggle_recording()
    controller.state = "awaiting_confirmation"
    controller.callbacks["on_transcript"](text)
    return controller


def test_press_hold_records_once_and_release_finalizes(panel):
    widget = panel.panel
    canvas = widget.mic_button._canvas
    canvas.event_generate("<Enter>")
    canvas.event_generate("<ButtonPress-1>", x=10, y=10)
    pump(panel.root, .02)
    controller = panel.controllers[-1]
    widget._press_mic()  # Pointer repeat cannot duplicate capture.
    assert controller.events == ["capture"]
    assert "00:00" in widget.status.cget("text")
    canvas.event_generate("<ButtonRelease-1>", x=10, y=10)
    pump(panel.root, .02)
    widget._release_mic()
    assert controller.events == ["capture", "finalize"]
    assert controller.session == "session-one"


def test_hiding_last_visible_voice_surface_cancels_recording(panel):
    panel.panel.toggle_recording()
    controller = panel.controllers[-1]
    assert controller.events == ["capture"]
    panel.root.withdraw()
    pump(panel.root, .08)
    assert controller.events == ["capture", "stop_listening"]


def test_transcript_edit_copy_retry_discard_and_single_submission(panel):
    widget = panel.panel
    controller = recognize(panel, "Firik, explain this project")
    assert widget._review_visible and not panel.submitted and not panel.dictated
    widget.copy_transcript()
    assert widget.clipboard_get() == "Firik, explain this project"
    widget.retry_transcript()
    assert controller.events.count("capture") == 2 and not widget._pending
    controller.callbacks["on_transcript"]("Firik, explain this project again")
    widget.command_text.delete(0, "end")
    widget.command_text.insert(0, "Firik, explain that again in Turkish")
    widget.apply_transcript()
    widget.apply_transcript()
    assert panel.submitted == ["explain that again in Turkish"]
    assert ("resolved", True) in controller.events
    controller.state = "idle"
    recognize(panel, "discard me")
    widget.dismiss_command()
    assert not widget._review_visible
    assert ("resolved", False) in controller.events
    assert len(panel.submitted) == 1


def test_auto_send_only_ordinary_requests_and_never_cancels_busy_agent(panel):
    widget = panel.panel
    panel.service.voice_settings = replace(panel.service.voice_settings, auto_send=True, review_before_send=False)
    controller = recognize(panel, "Firik, explain the current project architecture")
    assert widget.command_text.get()  # Visible before deferred auto-send.
    pump(panel.root, .05)
    assert panel.submitted == ["explain the current project architecture"]
    controller.state = "idle"
    recognize(panel, "Firik, delete all source files")
    pump(panel.root, .05)
    assert len(panel.submitted) == 1 and widget._pending
    widget.dismiss_command()
    controller.state = "idle"
    panel.busy[0] = True
    recognize(panel, "summarize the test failures")
    pump(panel.root, .05)
    assert len(panel.submitted) == 1 and widget._pending
    assert not panel.cancelled
    widget.stop_listening()
    widget.stop_speaking()
    assert not panel.cancelled
    assert controller.events[-2:] == ["stop_listening", "stop_speaking"]


def test_conversation_controls_do_not_cancel_agent_and_reuse_controller(panel):
    widget = panel.panel
    widget.start_conversation()
    controller = panel.controllers[-1]
    widget.toggle_pause()
    widget.toggle_pause()
    widget.exit_conversation()
    assert controller.events == ["conversation", "pause", "resume", "exit"]
    assert len(panel.controllers) == 1 and not panel.cancelled
    assert not widget._conversation and not widget._holding


def test_command_only_rejection_is_visible_in_voice_window_and_edit_can_recover(panel):
    widget = panel.panel
    widget.mode.set('Sesli komut')
    recognize(panel, 'Bu proje hakkında bilgi ver.')
    surface = widget.show_voice_surface()
    surface.transcript.delete('1.0', 'end')
    surface.transcript.insert('1.0', 'Veri yapıları ve algoritmalar hakkında bilgi verir misin?')
    surface.send_button.invoke()
    pump(panel.root, .05)
    assert widget._pending and not panel.submitted and not panel.cancelled
    assert 'Komut tanınmadı' in surface.error_label.cget('text')
    assert surface.error_label.cget('text') == widget.status.cget('text')
    viewport = surface.body._parent_canvas
    assert surface.error_label.winfo_rooty() >= viewport.winfo_rooty()
    assert (surface.error_label.winfo_rooty() + surface.error_label.winfo_height()
            <= viewport.winfo_rooty() + viewport.winfo_height())
    surface.transcript.delete('1.0', 'end')
    surface.transcript.insert('1.0', 'yanıtı durdur')
    surface.send_button.invoke()
    assert panel.cancelled == ['session-one'] and not panel.submitted
    assert not widget._pending and not surface.error_label.cget('text')


def test_conversation_retry_keeps_ordinary_messages_after_command_selection(panel):
    widget = panel.panel
    widget.mode.set('Sesli komut')
    widget.start_conversation()
    controller = panel.controllers[-1]
    controller.state = 'awaiting_confirmation'
    controller.callbacks['on_transcript']('İlk kayıt.')
    surface = widget._voice_surface
    widget.mode.set('Sesli komut')
    surface.retry_button.invoke()
    assert controller.events.count('capture') == 1 and widget._conversation
    controller.state = 'awaiting_confirmation'
    controller.callbacks['on_transcript']('Veri yapıları ve algoritmalar hakkında bilgi verir misin?')
    surface.send_button.invoke()
    surface.send_button.invoke()
    assert panel.submitted == ['Veri yapıları ve algoritmalar hakkında bilgi verir misin?']
    assert widget.mode.get() == 'Bas-konuş' and not widget._pending
    assert not panel.cancelled and ('resolved', True) in controller.events


def test_empty_reviewed_transcript_shows_feedback_in_voice_window(panel):
    widget = panel.panel
    recognize(panel, 'Explain this project.')
    surface = widget.show_voice_surface()
    surface.transcript.delete('1.0', 'end')
    surface.send_button.invoke()
    assert widget._pending and not panel.submitted
    assert 'Transkript boş' in surface.error_label.cget('text')


def test_session_switch_rejects_late_transcript_and_hidden_shortcut(panel):
    widget = panel.panel
    widget.toggle_recording()
    controller = panel.controllers[-1]
    panel.session[0] = "session-two"
    controller.callbacks["on_transcript"]("old utterance")
    assert not widget._pending and not panel.submitted
    widget.deactivate()
    widget._shortcut_press(SimpleNamespace(keysym="v"))
    assert controller.events == ["capture", "cancel"]
    widget.activate()
    widget._shortcut_press(SimpleNamespace(keysym="v"))
    widget._shortcut_press(SimpleNamespace(keysym="v"))
    widget._shortcut_release(SimpleNamespace(keysym="Control_L"))
    assert controller.session == "session-two"
    assert controller.events[-2:] == ["capture", "finalize"]


def test_native_shortcut_and_accessible_toggle_finalize_once(panel):
    widget = panel.panel
    panel.root.focus_force()
    pump(panel.root, .02)
    panel.root.event_generate("<KeyPress>", keysym="V", state=0x0005)
    pump(panel.root, .02)
    assert panel.controllers, "The default Control+Shift+V shortcut must match an actual shifted key"
    controller = panel.controllers[-1]
    panel.root.event_generate("<KeyRelease>", keysym="V", state=0x0005)
    pump(panel.root, .02)
    assert controller.events == ["capture", "finalize"]
    controller.state = "idle"
    widget.toggle_button._canvas.focus_force()
    widget.toggle_button._canvas.event_generate("<KeyPress-Return>")
    pump(panel.root, .02)
    widget.toggle_button._canvas.event_generate("<KeyPress-space>")
    pump(panel.root, .02)
    assert controller.events[-2:] == ["capture", "finalize"]


def test_provider_generation_invalidates_send_and_command_review(panel):
    widget = panel.panel
    controller = recognize(panel, "explain this project")
    controller.generation = 1
    widget.apply_transcript()
    assert not panel.submitted and not widget._pending
    controller.state = "idle"
    recognize(panel, "stop response")
    panel.session[0] = "session-two"
    widget.apply_command()
    assert not panel.cancelled
    panel.session[0] = "session-one"
    controller.generation = 2
    widget.apply_command()
    assert not panel.cancelled and not widget._pending
