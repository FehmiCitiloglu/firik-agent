"""Assistant-console actions exercise real scopes, navigation and controllers."""
from dataclasses import replace
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from firik_agent.core.events import AgentEventType
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.transcription import FinalTranscript
from test_ui_chat import emit
from test_ui_coding_workspace import workspace, settle
from test_ui_voice_experience import setup_chat, SyntheticCapture, until, snapshot


def reviewed(panel, text):
    assert panel._prepare_capture()
    panel._controller()
    panel._on_transcript(text)
    assert panel._pending
    panel.apply_transcript()


def test_local_status_inventory_and_summary_are_visible_without_model_requests(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    project = workspace[2]
    spoken = []
    monkeypatch.setattr(panel, 'read_text', spoken.append)
    view.messages.append({'role': 'assistant', 'content': 'Son görevde arama alanı eklendi.'})
    surface = panel.show_voice_surface()
    for phrase, expected in [('Jarvis, projelerimi listele', project.name),
                             ('Sohbetlerimi listele', 'Sohbetleriniz'),
                             ('Ne yapıyorsun?', 'çalışan ajan yok'),
                             ('Son görevi özetle', 'arama alanı eklendi'),
                             ('Neler yapabilirsin?', 'görev durumunu')]:
        reviewed(panel, phrase)
        assert expected in surface.preview.cget('text')
        assert spoken[-1] == surface.preview.cget('text')
        assert not panel._pending and not streams and not capture.windows


def test_command_suggestion_cancels_capture_and_requires_review_with_auto_send_enabled(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(
        workspace, monkeypatch, auto_send=True, review_before_send=False)
    panel.start_conversation()
    until(app, lambda: len(capture.windows) == 1)
    surface = panel._voice_surface
    next(button for button in surface.command_buttons if button.cget('text') == 'Durum raporu').invoke()
    until(app, lambda: panel._pending and capture.windows[0]['closed'].is_set())
    settle(app, .08)
    assert not streams and not speaker.spoken
    assert surface.transcript.get('1.0', 'end-1c') == 'Ne yapıyorsun?'
    assert panel._voice_request.session_id == view.session.session_id
    surface.transcript.delete('1.0', 'end')
    surface.transcript.insert('1.0', 'Veri yapılarını açıkla.')
    surface.send_button.invoke()
    surface.send_button.invoke()
    assert len(streams) == 1 and streams[0]['prompt'] == 'Veri yapılarını açıkla.'
    assert streams[0]['voice_request'].original_transcript == 'Ne yapıyorsun?'


def test_opted_in_spoken_status_answers_and_resumes_listening_without_a_send_click(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(
        workspace, monkeypatch, auto_send=True, review_before_send=False)
    async def transcribe(audio, model, language):
        return FinalTranscript('Jarvis, ne yapıyorsun?', 'tr')
    controller._transcribe = transcribe
    panel.start_conversation()
    until(app, lambda: len(capture.windows) == 1)
    capture.windows[0]['finish'].set()
    until(app, lambda: bool(speaker.spoken) and len(capture.windows) == 2)
    assert 'çalışan ajan yok' in speaker.spoken[0]
    assert not streams and not panel._pending and controller.conversation_active


def test_coding_plan_pause_resume_and_stop_use_existing_task_control(workspace, monkeypatch):
    app, projects, project, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True).to_dict()
    panel = projects.coding_voice_panel
    monkeypatch.setattr(panel, 'read_text', lambda text: None)
    state = projects._coding_state()
    state['plan'] = [{'title': 'Hatayı yeniden üret', 'status': 'completed'},
                     {'title': 'Düzeltmeyi doğrula', 'status': 'pending'}]
    surface = panel.show_voice_surface()
    reviewed(panel, 'Görev planını göster')
    assert 'Hatayı yeniden üret' in surface.preview.cget('text')
    assert 'Düzeltmeyi doğrula' in surface.preview.cget('text') and not streams
    assert projects._submit_coding_voice_transcript('main.py dosyasını incele')
    calls = []
    monkeypatch.setattr(app.service, 'coding_control', lambda sid, action: calls.append((sid, action)) or True)
    reviewed(panel, 'İşlemi duraklat')
    assert state['paused'] and state['busy']
    reviewed(panel, 'Göreve devam et')
    assert not state['paused'] and state['busy']
    panel._sync_voice_surface()
    assert surface.stop_agent_button.cget('state') == 'normal'
    surface.stop_agent_button.invoke()
    assert [action for _, action in calls] == ['pause', 'resume', 'cancel']
    assert all(sid == state['session'].session_id for sid, _ in calls)
    assert len(streams) == 1


def test_spoken_task_controls_reach_existing_coding_run_through_real_capture_pipeline(workspace, monkeypatch):
    app, projects, project, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True).to_dict()
    panel = projects.coding_voice_panel
    controller = panel._controller()
    capture = SyntheticCapture()
    controller._recorder = capture
    calls, spoken = [], []
    monkeypatch.setattr(panel, 'read_text', spoken.append)
    monkeypatch.setattr(app.service, 'coding_control', lambda sid, action: calls.append((sid, action)) or True)
    utterances = iter(['Jarvis, görev ne durumda?', 'Görevi duraklat', 'Göreve devam et'])
    async def transcribe(audio, model, language):
        return FinalTranscript(next(utterances), 'tr')
    controller._transcribe = transcribe
    assert projects._submit_coding_voice_transcript('main.py dosyasını incele')
    surface = panel.show_voice_surface()
    for index in range(3):
        surface.mic_mute_button.invoke()
        until(app, lambda: len(capture.windows) == index + 1 and surface.mic_mute_button.cget('text') == 'Kaydı bitir')
        surface.mic_mute_button.invoke()
        until(app, lambda: panel._pending)
        assert panel._voice_request.project_id == project.id
        surface.send_button.invoke()
        assert not panel._pending and len(streams) == 1
    assert [action for _, action in calls] == ['pause', 'resume']
    assert len(spoken) == 3 and projects._coding_state()['busy']


def test_voice_project_navigation_continues_in_destination_scope_without_starting_task(workspace, monkeypatch):
    app, view, source, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    project = workspace[2]
    monkeypatch.setattr('firik_agent.voice.audio.SoundDeviceRecorder', lambda: capture)
    source.start_conversation()
    until(app, lambda: len(capture.windows) == 1)
    old_surface = source._voice_surface
    old_sid = source.get_session_id()
    assert source.stage_prompt(f'{project.name} projesine geç')
    until(app, lambda: source._pending)
    old_surface.send_button.invoke()
    until(app, lambda: app.active_tab == 'projects' and projects_panel(app)._conversation)
    target = projects_panel(app)
    until(app, lambda: len(capture.windows) == 2)
    assert source._voice_surface is None and not old_surface.winfo_exists()
    assert not source._active and not controller.conversation_active
    assert target.get_session_id() != old_sid and target.get_project_id() == project.id
    assert target._voice_surface.owner is target and not streams
    assert project.name in target._voice_surface.context_label.cget('text')
    assert capture.windows[0]['closed'].is_set()
    assert target.stage_prompt('main.py dosyasını incele')
    until(app, lambda: target._pending)
    target._voice_surface.send_button.invoke()
    assert len(streams) == 1 and streams[0]['session_id'] == target.get_session_id()
    assert streams[0]['coding_options']['voice_request'].project_id == project.id


def projects_panel(app):
    return app.views['projects'].coding_voice_panel


def test_actual_progress_and_approval_wait_are_visible_without_exposing_tool_arguments(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    surface = panel.show_voice_surface()
    assert view._submit_voice_transcript('Bu projeyi incele')
    stream = streams[0]
    emit(app, stream, AgentEventType.TOOL_CALL_STARTED, tool_name='read_file', arguments={'token': 'hidden-value'})
    assert 'Dosya inceleniyor' in surface.activity_label.cget('text')
    assert 'hidden-value' not in surface.activity_label.cget('text')
    emit(app, stream, AgentEventType.APPROVAL_REQUIRED, tool_name='write_file', arguments={'content': 'hidden-source'})
    assert 'Onay bekleniyor' in surface.activity_label.cget('text')
    assert not controller.paused and view.is_streaming


def test_navigation_handoff_does_not_restart_capture_after_user_changes_destination(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    app._continue_voice_navigation()
    app.show_view('settings')
    settle(app, .1)
    assert not capture.windows and not controller.conversation_active


def test_rejected_suggestion_cannot_rebind_queued_transcript_to_changed_scope(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    assert panel._prepare_capture()
    original_scope = panel._capture_scope
    assert controller.review_text('Önceki kapsamda hazırlanmış istek')
    app.service._voice_revision += 1
    assert not panel.stage_prompt('Projelerimi listele')
    until(app, lambda: panel._pending)
    assert panel._voice_request.scope == original_scope
    panel.apply_transcript()
    assert not streams and panel._voice_request.cancellation_token.is_set()


def test_early_window_close_cancels_initial_layout_even_after_configure_event(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    surface = panel.show_voice_surface()
    job = surface._layout_job
    surface._resize(SimpleNamespace(widget=surface))
    assert surface._layout_job == job
    panel._close_voice_surface()
    assert job not in app.tk.splitlist(app.tk.call('after', 'info'))


def test_reopening_console_preserves_long_prose_but_filters_secrets_and_hidden_reasoning(workspace, monkeypatch):
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    text = ('Uzun yanıtın görünür bölümü. ' * 100) + '<think>hidden-reasoning</think> API_KEY=private-value'
    panel._update_response_preview(text)
    surface = panel.show_voice_surface()
    shown = surface.preview.cget('text')
    assert len(shown) > 2048 and 'hidden-reasoning' not in shown and 'private-value' not in shown
    panel._close_voice_surface()
    assert panel.show_voice_surface().preview.cget('text') == shown


def test_coding_prose_streams_into_console_without_narrating_the_raw_task_stream(workspace, monkeypatch):
    app, projects, project, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True, read_replies=True, stream_replies=True).to_dict()
    panel = projects.coding_voice_panel
    controller = panel._controller()
    narrated = []
    monkeypatch.setattr(controller, 'response_delta', narrated.append)
    surface = panel.show_voice_surface()
    assert projects._submit_coding_voice_transcript('main.py dosyasını incele')
    emit(app, streams[0], AgentEventType.TEXT_DELTA, content='Dosyadaki işlevleri inceliyorum.')
    assert 'Dosyadaki işlevleri' in surface.preview.cget('text') and not narrated


@pytest.mark.parametrize('theme', ['Dark', 'Light'])
def test_console_orb_tracks_input_and_stops_animation_when_hidden_or_reduced(workspace, monkeypatch, theme):
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    ctk.set_appearance_mode(theme)
    panel.start_conversation()
    surface = panel._voice_surface
    until(app, lambda: len(capture.windows) == 1 and surface.orb._job is not None)
    until(app, lambda: surface.orb.level == .63)
    panel._on_level(.82)
    count = len(surface.orb.canvas.find_all())
    settle(app, .15)
    assert surface.orb.level == .82 and len(surface.orb.canvas.find_all()) == count
    snapshot(surface, 'assistant-listening-' + theme.lower())
    surface.motion_button.invoke()
    phase = surface.orb._phase
    settle(app, .12)
    assert surface.orb._job is None and surface.orb._phase == phase
    surface.motion_button.invoke()
    surface.withdraw()
    settle(app, .08)
    assert surface.orb._job is None
    surface.deiconify()
    until(app, lambda: surface.orb._job is not None)
    capture.windows[0]['finish'].set()
    until(app, lambda: panel._pending)
    assert surface.orb.level == 0
    snapshot(surface, 'assistant-review-' + theme.lower())
    surface.send_button.invoke()
    until(app, lambda: len(streams) == 1)
    emit(app, streams[0], AgentEventType.TOOL_CALL_STARTED, tool_name='read_file')
    snapshot(surface, 'assistant-working-' + theme.lower())
    orb = surface.orb
    panel.exit_conversation()
    assert orb._job is None and not surface.winfo_exists()
    ctk.set_appearance_mode('System')


def test_console_replays_level_delivered_during_native_window_construction(workspace, monkeypatch):
    from firik_agent.ui.components.voice_session import VoiceSessionPanel
    app, view, panel, controller, capture, speaker, streams = setup_chat(workspace, monkeypatch)
    initialize = VoiceSessionPanel.__init__

    def initialize_with_level(surface, owner):
        initialize(surface, owner)
        # Native window setup may process the queued capture event before the
        # newly constructed surface has been assigned to its owner.
        owner._on_level(.74)

    monkeypatch.setattr(VoiceSessionPanel, '__init__', initialize_with_level)
    panel.start_conversation()
    assert panel._voice_surface.orb.level == .74
