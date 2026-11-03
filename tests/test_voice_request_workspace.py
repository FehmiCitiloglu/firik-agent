"""Existing workspace ownership remains bound through the real service bridge."""
from pathlib import Path

import pytest

from firik_agent.voice.intent_router import VoiceRequestRouter
from firik_agent.voice.requests import VoiceScope
from test_chat_streams import service


def scoped(service, tmp_path):
    root = tmp_path / 'project-root'
    root.mkdir()
    project = service.projects.create_project('Existing project', str(root))
    session, _ = service.restore_coding(project.id)
    router = VoiceRequestRouter(lambda request: True)
    request = router.create_request('Inspect actual.py', VoiceScope(session.session_id,
        project_id=project.id, workspace_id=str(root.resolve()), target='coding'))
    return root, project, session, request


def test_valid_workspace_is_accepted_but_execution_cwd_cannot_be_substituted(service, tmp_path):
    root, project, session, request = scoped(service, tmp_path)
    service.validate_voice_request(request)
    other = tmp_path / 'other-root'
    other.mkdir()
    sessions = set(service.sessions)
    with pytest.raises(ValueError, match='çalışma alan'):
        service.chat_agent_stream_async(session.session_id, request.request_text, cwd=str(other),
            coding_options={'project_id':project.id, 'voice_request':request})
    assert set(service.sessions) == sessions and not service._streams


def test_workspace_changed_in_existing_database_rejects_pending_request(service, tmp_path):
    root, project, session, request = scoped(service, tmp_path)
    other = tmp_path / 'replacement'
    other.mkdir()
    project.local_path = str(other)
    service.projects.db.update_project(project)
    with pytest.raises(ValueError, match='çalışma alan'):
        service.validate_voice_request(request)
    assert service.sessions[session.session_id] is session


@pytest.mark.parametrize('changed', ['prompt','cwd','image'])
def test_chat_request_cannot_gain_unreviewed_content_or_workspace(service, tmp_path, changed):
    session = service.get_or_create_session()
    router = VoiceRequestRouter(lambda request:True)
    request = router.create_request('Explain the current session', VoiceScope(session.session_id))
    kwargs = {'prompt':request.request_text}
    if changed == 'prompt':
        kwargs['prompt'] = 'Run another unreviewed task'
    else:
        kwargs['cwd' if changed == 'cwd' else 'image_path'] = str(tmp_path)
    with pytest.raises(ValueError, match='incelenen'):
        service.chat_agent_stream_async(session.session_id, voice_request=request, **kwargs)
    assert not service._streams
