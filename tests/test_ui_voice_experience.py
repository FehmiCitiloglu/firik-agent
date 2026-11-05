"""Native composer/session UX checks with actual controllers and synthetic audio.

Only microphone I/O, recognition and speakers are injected. No hardware or
network request is made. Each case can run in its own WindowServer process.
"""
from dataclasses import replace
import io
import os
from pathlib import Path
import threading
import time
import wave

import customtkinter as ctk
import pytest

from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.errors import VoicePermissionError
from firik_agent.voice.transcription import FinalTranscript
from test_ui_coding_workspace import workspace, settle


def until(app,predicate,timeout=4):
    end = time.monotonic()+timeout
    while not predicate() and time.monotonic()<end:
        settle(app,.02)
    assert predicate()


def focus_native(app, widget):
    """Wait out CTk's native titlebar redraw and deliver actual focus events."""
    settle(app,.3)
    def focused():
        widget.winfo_toplevel().lift()
        widget.focus_force()
        return app.focus_displayof() is widget
    try:
        until(app,focused)
    except AssertionError:
        raise AssertionError(str({"top_state": widget.winfo_toplevel().state(),
            "owner_state": app.state(), "mapped": widget.winfo_ismapped(),
            "viewable": widget.winfo_viewable(), "focus": str(app.focus_displayof()),
            "target": str(widget)})) from None
    settle(app,.02)


class SyntheticCapture:
    supports_barge_in = False
    def __init__(self):
        self.options = {}
        self.windows = []
        self.failure = None
    def configure(self,**options):
        self.options = options
    def _capture(self,stop,cancel,on_speech_start=None):
        if self.failure:
            error,self.failure = self.failure,None
            raise error
        window = {'finish':threading.Event(),'closed':threading.Event(),'cancel':cancel}
        self.windows.append(window)
        try:
            if self.options.get('on_level'):
                self.options['on_level'](.63)
            while not(stop.is_set() or cancel.is_set() or window['finish'].is_set()):
                cancel.wait(.01)
            if cancel.is_set():
                return b''
            if on_speech_start and not stop.is_set():
                on_speech_start()
            if stop.is_set() and on_speech_start:
                return b''
            output = io.BytesIO()
            with wave.open(output,'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b'\xe8\x03\x18\xfc'*2400)
            return output.getvalue()
        finally:
            window['closed'].set()
    def record(self,stop,cancel,max_seconds,device=None):
        return self._capture(stop,cancel)
    def record_utterance(self,stop,cancel,max_seconds,device=None,*,on_speech_start=None,**kwargs):
        return self._capture(stop,cancel,on_speech_start)


class SyntheticSpeaker:
    supports_barge_in = False
    def __init__(self):
        self.spoken = []
    def configure(self,**options):
        pass
    def speak(self,text,cancel,language,rate):
        if not cancel.is_set():
            self.spoken.append(text)


def setup_chat(fixture,monkeypatch,**settings):
    app,projects,project,streams = fixture
    app.service.config['voice'] = replace(VoiceSettings(),enabled=True,read_replies=False,
        **settings).to_dict()
    app.show_view('chat')
    view = app.views['chat'].current
    panel = view.voice_panel
    async def transcribe(audio,model,language):
        return FinalTranscript('Firik, explain this synthetic project.','tr')
    monkeypatch.setattr(app.service,'_transcribe_microphone',transcribe)
    controller = panel._controller()
    capture,speaker = SyntheticCapture(),SyntheticSpeaker()
    controller._recorder,controller._speaker = capture,speaker
    controller._speaker_factory = lambda settings:speaker
    app.lift()
    view.input_textbox.focus_force()
    settle(app,.08)
    return app,view,panel,controller,capture,speaker,streams


def snapshot(window,name):
    if not os.environ.get('FIRIK_CAPTURE_VOICE_UI'):
        return
    from voice_ui_capture import capture_window
    destination = Path(os.environ['FIRIK_CAPTURE_VOICE_UI'])
    destination.mkdir(parents=True,exist_ok=True)
    window.lift()
    window.update()
    capture_window(window,destination/(name+'.png'))


@pytest.mark.parametrize('target',['conversation','coding'])
def test_composer_microphone_shares_send_row_and_keyboard_activates_existing_controller(workspace,monkeypatch,target):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    if target == 'coding':
        app.show_view('projects')
        projects = app.views['projects']
        view,panel = projects,projects.coding_voice_panel
        controller = panel._controller()
        controller._recorder,controller._speaker = capture,speaker
        mic,send = view.coding_mic_button,view.coding_send_btn
    else:
        mic,send = view.voice_mic_button,view.send_btn
    app.geometry('940x620')
    settle(app,.12)
    assert panel.compact and panel.composer_mic_button is mic
    assert int(mic.grid_info()['row']) == int(send.grid_info()['row'])
    assert int(mic.grid_info()['column']) < int(send.grid_info()['column'])
    assert mic.winfo_ismapped() and mic.winfo_rootx()+mic.winfo_width() <= send.winfo_rootx()
    assert int(mic._canvas.cget('takefocus')) == 1
    mic._canvas.focus_force()
    mic._canvas.event_generate('<space>')
    until(app,lambda:len(capture.windows)==1 and panel._last_state=='recording')
    assert panel.controller is controller and 'Bitir' in mic.cget('text')
    assert mic.cget('fg_color') == ('#dc2626','#b91c1c')
    mic._canvas.event_generate('<Return>')
    until(app,lambda:capture.windows[0]['closed'].is_set())
    until(app,lambda:panel._pending)
    assert not streams and controller is panel.controller
    panel.dismiss_command()
    app.service.config['voice']=replace(app.service.voice_settings,interaction_mode='conversation',vad_enabled=True).to_dict()
    mic._canvas.event_generate('<Return>')
    until(app,lambda:controller.conversation_active)
    assert panel._voice_surface is not None
    panel.exit_conversation()


@pytest.mark.parametrize('selected_mode', ['Bas-konuş', 'Sesli komut'])
def test_dedicated_session_edits_final_transcript_into_same_chat_without_partial_text(workspace,monkeypatch,selected_mode):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    sid = view.session.session_id
    panel.mode.set(selected_mode)
    panel.start_conversation()
    surface = panel._voice_surface
    until(app,lambda:len(capture.windows)==1 and panel._last_state=='listening')
    assert surface is not None and panel.controller is controller
    assert surface.transcript.get('1.0','end-1c') == '' and not streams
    assert 'söyleyin' in surface.live_text_note.cget('text')
    assert surface.send_button.cget('state') == 'disabled'
    panel._on_level(1.7)
    assert surface.level.get() == 1
    panel._on_level(-.7)
    assert surface.level.get() == 0
    panel._on_level(.63)
    assert 'Mikrofon açık' in surface.recording_indicator.cget('text')
    capture.windows[0]['finish'].set()
    until(app,lambda:panel._pending)
    assert surface.transcript.cget('state') == 'normal'
    assert surface.send_button.cget('state') == 'normal'
    assert 'Firik' in surface.transcript.get('1.0','end-1c')
    surface.transcript.delete('1.0','end')
    surface.transcript.insert('1.0','Veri yapıları ve algoritmalar hakkında bilgi verir misin?')
    snapshot(surface,'session-review')
    surface.send_button.invoke()
    assert len(streams)==1 and streams[0]['session_id']==sid
    assert panel.mode.get() == 'Bas-konuş' and panel.apply_button.cget('text') == 'Gönder'
    request = streams[0]['voice_request']
    assert request.original_transcript == 'Firik, explain this synthetic project.'
    assert request.request_text == 'Veri yapıları ve algoritmalar hakkında bilgi verir misin?'
    assert view.session.session_id==sid and request.recognized_language=='tr'
    surface.stop_speech_button.invoke()
    surface.listen_button.invoke()
    assert view.is_streaming and not request.cancellation_token.is_set()
    surface.exit_button.invoke()
    assert not surface.winfo_exists() and not controller.conversation_active
    assert view.is_streaming and not request.cancellation_token.is_set() and not speaker.spoken
    streams[0]['on_done']('Synthetic response completed.')
    settle(app,.05)


def test_session_mute_resume_and_window_close_release_capture_without_agent_cancel(workspace,monkeypatch):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    panel.start_conversation()
    surface = panel._voice_surface
    until(app,lambda:len(capture.windows)==1)
    surface.mic_mute_button.invoke()
    until(app,lambda:capture.windows[0]['closed'].is_set())
    assert controller.paused and 'Mikrofonu aç' in surface.mic_mute_button.cget('text')
    surface.mic_mute_button.invoke()
    until(app,lambda:len(capture.windows)==2)
    assert not controller.paused
    surface.tk.call(surface.protocol('WM_DELETE_WINDOW'))
    until(app,lambda:capture.windows[1]['closed'].is_set())
    assert not controller.conversation_active and not streams and not speaker.spoken
    assert panel._voice_surface is None and not surface.winfo_exists()


def test_microphone_permission_error_exposes_retry_and_text_fallback(workspace,monkeypatch):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    capture.failure = VoicePermissionError('Mikrofon izni gerekli. Sentetik izin testi.')
    panel.start_conversation()
    surface = panel._voice_surface
    until(app,lambda:'Sentetik izin' in surface.error_label.cget('text'))
    assert panel.error_retry.winfo_ismapped()
    assert panel.text_fallback_button.winfo_ismapped()
    panel.error_retry.invoke()
    until(app,lambda:len(capture.windows)==1)
    panel.stop_listening()
    until(app,lambda:capture.windows[0]['closed'].is_set())
    surface.text_button.invoke()
    settle(app,.05)
    assert panel._voice_surface is None
    view.input_textbox.insert('1.0','Continue using text after microphone failure.')
    view._on_send_pressed()
    assert streams[-1]['prompt']=='Continue using text after microphone failure.'
    assert not speaker.spoken


def test_response_preview_respects_disabled_auto_reading_and_retains_text(workspace,monkeypatch):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    surface = panel.show_voice_surface()
    panel.response_completed('Synthetic private response stays on screen.')
    settle(app,.06)
    assert surface.preview.cget('text')=='Synthetic private response stays on screen.'
    assert not speaker.spoken and not app.service.voice_settings.read_replies
    assert controller.state in ('idle','ready')


@pytest.mark.parametrize('blocked',['hidden','minimized','other_window','settings_modal','pending_review','muted','disabled'])
def test_automatic_reading_is_blocked_when_window_or_input_context_is_ineligible(workspace,monkeypatch,blocked):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    app.service.config['voice'] = replace(app.service.voice_settings,read_replies=True).to_dict()
    assert panel._automatic_audio_allowed()
    auxiliary = None
    if blocked=='hidden':
        panel.deactivate()
    elif blocked=='minimized':
        app.iconify()
        settle(app,.08)
    elif blocked=='other_window':
        auxiliary = ctk.CTkToplevel(app)
        auxiliary.title('Synthetic focus test')
        auxiliary.geometry('320x160')
        entry = ctk.CTkEntry(auxiliary)
        entry.pack()
        auxiliary.lift()
        settle(app,.3)  # CTk applies native top-level state after mapping.
        entry.focus_force()
        until(app,lambda:app.focus_displayof() is entry._entry)
        assert app.focus_displayof().winfo_toplevel() is auxiliary
    elif blocked=='settings_modal':
        panel._dialog = ctk.CTkToplevel(app)
        auxiliary = panel._dialog
    elif blocked=='pending_review':
        assert panel._prepare_capture()
        panel._on_transcript('Explain this synthetic project.')
    elif blocked=='muted':
        controller.mute()
    elif blocked=='disabled':
        app.service.config['voice'] = replace(app.service.voice_settings, enabled=False).to_dict()
    assert not panel._automatic_audio_allowed()
    panel.response_completed('Never read this response automatically.')
    settle(app,.06)
    assert not speaker.spoken
    if auxiliary:
        if panel._dialog is auxiliary:
            panel._dialog=None
        auxiliary.destroy()
    if blocked=='minimized':
        app.deiconify()


def test_completion_chime_is_opt_in_and_suppressed_during_voice_capture(workspace,monkeypatch):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    bells=[]
    monkeypatch.setattr(panel,'bell',lambda:bells.append('nonprivate chime'))
    assert not app.service.voice_settings.completion_chime
    panel.coding_completion('Synthetic private task result.')
    assert not bells and not speaker.spoken
    app.service.config['voice']=replace(app.service.voice_settings,completion_chime=True).to_dict()
    panel.coding_completion('Synthetic private task result.')
    assert bells==['nonprivate chime'] and not speaker.spoken
    panel.start_conversation()
    until(app,lambda:len(capture.windows)==1)
    panel.coding_completion('Another private result while listening.')
    assert bells==['nonprivate chime'] and not speaker.spoken
    panel.exit_conversation()
    app.service.config['voice']=replace(app.service.voice_settings, enabled=False).to_dict()
    panel.coding_completion('A private result with master voice disabled.')
    assert bells==['nonprivate chime'] and not speaker.spoken


@pytest.mark.parametrize('theme',['Light','Dark'])
def test_voice_surface_and_compact_composer_fit_small_native_window_in_both_themes(workspace,monkeypatch,theme):
    ctk.set_appearance_mode(theme)
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    app.geometry('940x620')
    settle(app,.1)
    mic,send = view.voice_mic_button,view.send_btn
    assert mic.winfo_ismapped() and panel.session_button.winfo_ismapped()
    assert not panel.mic_button.winfo_ismapped()
    assert send.winfo_rootx()+send.winfo_width()<=app.winfo_rootx()+app.winfo_width()
    assert panel.winfo_height()<150
    snapshot(app,'composer-'+theme.lower())
    panel.start_conversation()
    surface = panel._voice_surface
    surface.geometry('380x540')
    until(app,lambda:len(capture.windows)==1 and panel._last_state=='listening')
    panel._on_level(.63)
    settle(app,.12)
    assert surface.exit_button.winfo_ismapped()
    assert surface.exit_button.winfo_rooty()+surface.exit_button.winfo_height()<=surface.winfo_rooty()+surface.winfo_height()
    for button in (surface.mic_mute_button, surface.listen_button, surface.stop_speech_button,
                   surface.stop_agent_button, surface.text_button, surface.exit_button):
        assert button.winfo_ismapped()
        assert button.winfo_rooty() >= surface.winfo_rooty()
        assert button.winfo_rooty()+button.winfo_height() <= surface.winfo_rooty()+surface.winfo_height()
        assert button.winfo_rootx()+button.winfo_width() <= surface.winfo_rootx()+surface.winfo_width()
    assert surface.state_label.winfo_width()>180
    assert surface.transcript.winfo_width()<=surface.winfo_width()
    viewport = surface.body._parent_canvas
    assert viewport.winfo_height()<surface.winfo_height()
    assert viewport.winfo_rooty()+viewport.winfo_height()<=surface.exit_button.winfo_rooty()
    viewport.yview_moveto(1)
    settle(app,.06)
    assert surface.body.winfo_width() <= surface.winfo_width()
    viewport.yview_moveto(0)
    settle(app,.05)
    snapshot(surface,'session-'+theme.lower())
    panel.exit_conversation()
    ctk.set_appearance_mode('System')


def test_discard_clears_unsubmitted_text_in_both_voice_views(workspace, monkeypatch):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    panel.start_conversation()
    surface = panel._voice_surface
    until(app,lambda:len(capture.windows)==1)
    capture.windows[0]['finish'].set()
    until(app,lambda:panel._pending)
    request = panel._voice_request
    surface.discard_button.invoke()
    assert not panel._pending and not panel._last_transcript
    assert not surface.transcript.get('1.0','end-1c') and not panel.command_text.get()
    assert request.cancellation_token.is_set() and not streams
    panel.exit_conversation()


def test_closing_unsaved_settings_restores_keyboard_and_audio_eligibility(workspace, monkeypatch):
    from types import SimpleNamespace
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    panel.open_settings()
    dialog = panel._dialog
    assert not panel._automatic_audio_allowed()
    dialog.destroy()
    view.input_textbox.focus_force()
    settle(app,.06)
    assert panel._dialog is None and panel._automatic_audio_allowed()
    panel._shortcut_press(SimpleNamespace())
    until(app,lambda:len(capture.windows)==1)
    panel._finish_hold()
    until(app,lambda:panel._pending)
    panel.dismiss_command()


def test_voice_surface_follows_runtime_theme_and_restores_outlined_focus(workspace, monkeypatch):
    from firik_agent.ui.theme import THEME_COLORS
    ctk.set_appearance_mode('Light')
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    panel.show_voice_surface()
    surface = panel._voice_surface
    try:
        for theme in ('Dark', 'Light', 'Dark'):
            ctk.set_appearance_mode(theme)
            settle(app,.06)
            expected = THEME_COLORS[theme.lower()]['bg_primary']
            # Read the native label color: checking CTk's configured tuple alone
            # misses stale light backgrounds inherited from a scrollable frame.
            assert surface.preview._label.cget('background') == expected
            assert surface.live_text_note._label.cget('background') == expected
        outlined = surface.exit_button
        assert outlined.cget('border_width') == 1
        settle(app,.3)
        focus_native(app,outlined._canvas)
        until(app,lambda:outlined.cget('border_width') == 2)
        assert outlined.cget('border_width') == 2
        focus_native(app,surface.stop_speech_button._canvas)
        until(app,lambda:outlined.cget('border_width') == 1)
        assert outlined.cget('border_width') == 1
    finally:
        panel._close_voice_surface()
        ctk.set_appearance_mode('System')


def test_push_to_talk_shortcut_works_in_voice_surface_and_unbinds_on_close(workspace, monkeypatch):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    surface = panel.show_voice_surface()
    button = surface.text_button._canvas
    button.focus_force()
    settle(app,.06)
    button.event_generate('<KeyPress>',keysym='V',state=0x0005)
    until(app,lambda:len(capture.windows)==1)
    assert panel._holding and panel._hold_source == 'keyboard'
    button.event_generate('<KeyRelease>',keysym='V',state=0x0005)
    until(app,lambda:panel._pending)
    assert not panel._holding and not streams
    panel.dismiss_command()
    panel._close_voice_surface()
    assert all(top is app for top,sequence,identifier in panel._shortcut_bindings)


@pytest.mark.parametrize('destination',['hidden','minimized','other_window','settings'])
def test_focus_loss_cancels_queued_automatic_speech_without_cancelling_agent(workspace,monkeypatch,destination):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    entered,cancelled = threading.Event(),threading.Event()
    spoken=[]
    class SlowSpeaker(SyntheticSpeaker):
        def speak(self,text,cancel,language,rate):
            spoken.append(text)
            entered.set()
            if cancel.wait(3):
                cancelled.set()
    controller._speaker_factory=lambda settings:SlowSpeaker()
    app.service.config['voice']=replace(app.service.voice_settings,read_replies=True,stream_replies=True).to_dict()
    view.input_textbox.insert('1.0','Synthetic foreground speech test')
    view._on_send_pressed()
    agent_cancellations=[]
    monkeypatch.setattr(app.service,'cancel_active_stream',lambda *args,**kwargs:agent_cancellations.append(args))
    panel.response_delta('First private sentence. Second private sentence. ')
    until(app,entered.is_set)
    auxiliary=None
    if destination=='hidden':
        app.withdraw()
    elif destination=='minimized':
        app.iconify()
    elif destination=='other_window':
        auxiliary=ctk.CTkToplevel(app)
        auxiliary.geometry('260x140')
        focus_native(app,auxiliary)
    else:
        panel.open_settings()
    try:
        until(app,cancelled.is_set)
        assert spoken==['First private sentence.']
        assert not controller._speech_queue and not panel._automatic_speech
        assert view.is_streaming and not agent_cancellations
    finally:
        if auxiliary:
            auxiliary.destroy()
        if destination in {'hidden','minimized'}:
            app.deiconify()


@pytest.mark.parametrize('operation',['read','replay'])
def test_manual_speech_replaces_automatic_queue_without_leaving_unguarded_items(workspace,monkeypatch,operation):
    app,view,panel,controller,capture,speaker,streams = setup_chat(workspace,monkeypatch)
    spoken=[]
    class SlowSpeaker(SyntheticSpeaker):
        def speak(self,text,cancel,language,rate):
            spoken.append(text)
            cancel.wait(3)
    controller._speaker_factory=lambda settings:SlowSpeaker()
    app.service.config['voice']=replace(app.service.voice_settings,read_replies=True,stream_replies=True).to_dict()
    view.input_textbox.insert('1.0','Synthetic explicit playback test')
    view._on_send_pressed()
    panel.response_delta('First private sentence. Second private sentence. ')
    until(app,lambda:len(spoken)==1)
    assert panel._automatic_speech and len(controller._speech_queue)==1
    if operation=='read':
        panel.read_text('Explicit manual selection.')
        expected='Explicit manual selection.'
    else:
        panel.replay_speech()
        expected='First private sentence.'
    until(app,lambda:len(spoken)==2)
    assert spoken==['First private sentence.',expected]
    assert not controller._speech_queue and not panel._automatic_speech
    assert view.is_streaming
    panel.stop_speaking()
