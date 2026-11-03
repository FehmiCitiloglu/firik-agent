"""Real application state and trusted Tk request/approval integration."""
from dataclasses import replace
import json

from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.transcription import FinalTranscript
from firik_agent.voice.requests import VoiceConfirmation, VoiceScope
from firik_agent.ui.components.operation_approval import OperationApprovalDialog
from test_ui_coding_workspace import workspace, settle


def prepare_panel(panel, transcript):
    assert panel._prepare_capture()
    panel._controller()
    panel._on_transcript(transcript)
    assert panel._pending


def chat_panel(app):
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True).to_dict()
    app.show_view('chat')
    view = app.views['chat'].current
    return view, view.voice_panel


def test_reviewed_voice_uses_existing_session_and_preserves_original_language_metadata(workspace):
    app, projects, project, streams = workspace
    view, panel = chat_panel(app)
    sid, session, model = view.session.session_id, view.session, view.session.model
    session.enabled_tool_names = {'list_skills'}
    prepare_panel(panel, FinalTranscript('Firik, explain the current architecture.', 'tr'))
    panel.command_text.delete(0, 'end')
    panel.command_text.insert(0, 'Summarize test failures.')
    panel.apply_transcript()
    assert len(streams) == 1 and streams[0]['session_id'] == sid
    request = streams[0]['voice_request']
    assert request.original_transcript == 'Firik, explain the current architecture.'
    assert request.request_text == 'Summarize test failures.'
    assert request.recognized_language == 'tr' and request.origin == 'voice'
    assert request.confirmation == VoiceConfirmation.CONFIRMED and request.dispatched
    assert app.service.sessions[sid] is session and session.model == model
    assert session.enabled_tool_names == {'list_skills'}
    assert view.messages[-2]['execution']['utterance_id'] == request.utterance_id
    panel.stop_speaking()
    panel.stop_listening()
    assert not request.cancellation_token.is_set()
    panel.apply_transcript()
    assert len(streams) == 1


def test_coding_voice_uses_real_project_context_permissions_and_same_execution_bridge(workspace):
    app, view, project, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True).to_dict()
    panel = view.coding_voice_panel
    state = view._coding_state()
    sid, permissions = state['session'].session_id, dict(state['permissions'])
    prepare_panel(panel, 'Run the permitted tests and summarize the failures.')
    assert not streams
    panel.apply_transcript()
    call = streams[-1]
    request = call['coding_options']['voice_request']
    assert request.session_id == sid and request.project_id == project.id
    from pathlib import Path
    assert request.intended_target == 'coding' and request.workspace_id == str(Path(project.local_path).resolve())
    assert call['coding_options']['permissions'] == permissions
    assert call['cwd'] == project.local_path and state['session'] is app.service.sessions[sid]
    assert state['messages'][-2]['execution']['origin'] == 'voice'
    panel.stop_speaking()
    assert state['busy'] and not request.cancellation_token.is_set()


def test_explicit_voice_navigation_restores_real_chat_without_starting_execution(workspace):
    app, _, _, streams = workspace
    view, panel = chat_panel(app)
    sid = view.session.session_id
    target = app.service.get_or_create_session()
    target.title = 'Existing restored target'
    app.service.save_chat_transcript(target, [{'role':'user','content':'Existing saved context'}])
    prepare_panel(panel, f'select conversation id {target.session_id}')
    assert app.views['chat'].current.session.session_id == sid
    panel.apply_transcript()
    assert app.views['chat'].current.session is target
    assert not streams
    assert app.service.load_chat_transcript(target.session_id)[0]['content'] == 'Existing saved context'


def test_navigation_to_existing_project_preserves_other_coding_work(workspace):
    app, projects, project, streams = workspace
    view, panel = chat_panel(app)
    target_state = projects._coding_states[project.id]
    target_state['busy'] = True
    prepare_panel(panel, f'select project id {project.id}')
    panel.apply_transcript()
    assert app.active_tab == 'projects'
    assert projects.current_project_id == project.id and target_state['busy']
    assert not streams


def test_exact_operation_dialog_cannot_be_approved_by_a_transcript(workspace):
    app, _, _, streams = workspace
    view, panel = chat_panel(app)
    view.input_textbox.insert('1.0', 'A typed request')
    view._on_send_pressed()
    decisions = []
    arguments = {'path':'actual.py','content':'full operation ' * 200}
    view._show_voice_tool_approval({'session_id':view.session.session_id,'tool_name':'write_file',
        'arguments':arguments,'reason':'Exact operation'}, decisions.append)
    dialog = next(iter(view._voice_approval_dialogs))
    assert isinstance(dialog, OperationApprovalDialog)
    assert json.loads(dialog.operation.get('1.0','end-1c')) == arguments
    prepare_panel(panel, 'approve the tool')
    panel.apply_transcript()
    assert not decisions and 'Sesli onay' in panel.status.cget('text')
    dialog.allow_button.invoke()
    assert decisions == [True]
    assert len(streams) == 1


def test_cancelled_chat_rejects_and_closes_pending_exact_operation_dialog(workspace):
    app, _, _, streams = workspace
    view, panel = chat_panel(app)
    view.input_textbox.insert('1.0', 'A typed request')
    view._on_send_pressed()
    decisions = []
    view._show_voice_tool_approval({'session_id':view.session.session_id,'tool_name':'delete_file',
        'arguments':{'path':'actual.py'}}, decisions.append)
    dialog = next(iter(view._voice_approval_dialogs))
    view._on_send_pressed()
    assert decisions == [False] and not dialog.winfo_exists()


def test_stale_scope_after_project_change_does_not_submit_or_create_ids(workspace):
    app, projects, project, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True).to_dict()
    panel = projects.coding_voice_panel
    prepare_panel(panel, 'Inspect main.py')
    sessions = set(app.service.sessions)
    panel.get_project_id = lambda: 'missing-project'
    panel.apply_transcript()
    assert not streams and set(app.service.sessions) == sessions
    assert 'kapsam' in panel.status.cget('text') or 'oturum' in panel.status.cget('text')


def test_navigation_can_destroy_originating_coding_widgets_without_touching_an_agent(workspace):
    from pathlib import Path
    app, projects, project, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True).to_dict()
    root = Path(project.local_path).parent / 'other'
    root.mkdir()
    (root / 'app.py').write_text('pass\n')
    other = projects.project_service.create_project('Other existing project', str(root))
    panel = projects.coding_voice_panel
    prepare_panel(panel, f'select project id {other.id}')
    panel.apply_transcript()
    assert panel._closed
    assert projects.current_project_id == other.id and not streams


def test_replaced_workspace_root_under_same_project_and_session_requires_new_capture(workspace):
    from pathlib import Path
    app, projects, project, streams = workspace
    app.service.config['voice'] = replace(VoiceSettings(), enabled=True).to_dict()
    panel = projects.coding_voice_panel
    prepare_panel(panel, 'Inspect the current module')
    prior = panel._voice_request
    other = Path(project.local_path).parent / 'changed-root'
    other.mkdir()
    project.local_path = str(other)
    # Reflect a legitimate workspace change in the existing owning database.
    projects.project_service.db.update_project(project)
    panel.apply_transcript()
    assert not streams and prior.cancellation_token.is_set()
    assert 'kapsam' in panel.status.cget('text') or 'alan' in panel.status.cget('text')


def test_voice_bridge_start_failure_keeps_existing_chat_editable(workspace, monkeypatch):
    app, _, _, streams = workspace
    view, panel = chat_panel(app)
    def unavailable(**options):
        raise RuntimeError('Existing runtime unavailable')
    monkeypatch.setattr(app.service, 'chat_agent_stream_async', unavailable)
    prepare_panel(panel, 'Explain the current session')
    panel.apply_transcript()
    assert not view.is_streaming and not streams
    assert 'Existing runtime unavailable' in view.messages[-1]['content']
