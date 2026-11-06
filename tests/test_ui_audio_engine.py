"""Native Tk integration for engine streaming and discovered device controls."""
from dataclasses import replace
import threading
import time

import customtkinter as ctk

from firik_agent.core.events import AgentEventType
from firik_agent.voice.audio_devices import AudioDevice
from firik_agent.voice.config import VoiceSettings
from test_ui_chat import emit, pump
from test_ui_coding_workspace import workspace, settle
from test_ui_voice import voice_chat, foreground_voice_window


def until(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        settle(app, .02)
    assert predicate()


def test_native_chat_reads_stable_sentences_before_stream_completion(workspace, monkeypatch):
    app, _, _, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True,
        read_replies=True, stream_replies=True).to_dict()
    spoken, workers = [], []
    class Speaker:
        def configure(self, **kwargs):
            pass
        def speak(self, text, cancel, language, rate):
            spoken.append(text)
            workers.append(threading.get_ident())
    monkeypatch.setattr('firik_agent.voice.synthesis.EngineSpeaker', lambda **kwargs: Speaker())
    app.show_view('chat')
    view = app.views['chat'].current
    view.input_textbox.insert('1.0', 'Explain this briefly')
    view._on_send_pressed()
    stream = streams[-1]
    foreground_voice_window(app)
    emit(app, stream, AgentEventType.TEXT_DELTA, content='Hello. ')
    until(app, lambda: spoken == ['Hello.'])
    assert view.is_streaming
    emit(app, stream, AgentEventType.TEXT_DELTA, content='Second sentence.')
    stream['on_done']('Hello. Second sentence.')
    until(app, lambda: len(spoken) == 2)
    assert spoken == ['Hello.', 'Second sentence.']
    assert all(worker != threading.get_ident() for worker in workers)


def test_settings_device_picker_persists_stable_ids_and_controls_are_visible(voice_chat):
    root, workspace, service = voice_chat
    inputs = AudioDevice('pa:input-stable', 3, 'USB Mic', 'Core Audio', 1, 48000)
    outputs = AudioDevice('pa:output-stable', 4, 'Headphones', 'Core Audio', 2, 48000)
    service.audio_devices_async = lambda done, error: done({'input': [inputs], 'output': [outputs]})
    panel = workspace.current.voice_panel
    panel.open_settings()
    pump(root, .1)  # Device enumeration is requested lazily when settings map.
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    menus = [widget for widget in descendants(panel._dialog) if isinstance(widget, ctk.CTkOptionMenu)]
    for device in (inputs, outputs):
        menu = next(menu for menu in menus if any(device.name in label for label in menu.cget('values')))
        label = next(label for label in menu.cget('values') if device.name in label)
        menu.set(label)
        menu.cget('command')(label)
    save = next(widget for widget in panel._dialog.winfo_children() if isinstance(widget, ctk.CTkButton))
    save.invoke()
    assert service.voice_settings.input_device == inputs.id
    assert service.voice_settings.output_device == outputs.id
    assert panel.input_level.winfo_ismapped()
    surface = panel.show_voice_surface()
    pump(root, .1)
    assert not surface.playback_controls.winfo_ismapped()
    panel._update_response_preview('Tamamlanan sesli yanıt.')
    pump(root, .1)
    assert surface.pause_speech_button.winfo_ismapped() and surface.replay_button.winfo_ismapped()
