"""Deterministic voice requests use actual session IDs and existing handlers."""
from dataclasses import FrozenInstanceError, replace
import json
import threading

import pytest

from firik_agent.voice.commands import VoiceCommand
from firik_agent.voice.intent_router import (VoiceApprovalRejected,VoiceCategory,VoiceConfirmation,
    VoiceConfirmationRequired,VoiceDestination,VoiceDuplicateRequest,VoiceImpact,
    VoiceNeedsClarification,VoiceRequestRouter,VoiceRouteError,VoiceScope,VoiceScopeError,
    VoiceUnsupportedCommand,classify_impact)


SCOPE = VoiceScope('actual-session','actual-project','actual-workspace','coding',17)


def router(*,handler=None,commands=None,inventory=(),history=512):
    calls = []
    instance = VoiceRequestRouter(handler or (lambda request:calls.append(request) or True),
        commands,scope_provider=lambda:SCOPE,inventory_provider=lambda:inventory,max_history=history)
    return instance,calls


def test_scoped_request_preserves_original_language_and_actual_identifiers():
    instance,calls = router()
    original = 'Firik, mevcut proje mimarisini açıkla.'
    request = instance.create_request(original,SCOPE,recognized_language='tr')
    assert request.original_transcript == original
    assert request.request_text == 'mevcut proje mimarisini açıkla.'
    assert request.scope == SCOPE
    assert request.recognized_language == 'tr'
    assert request.origin == 'voice' and request.created_at.utcoffset().total_seconds() == 0
    assert request.impact == VoiceImpact.LOW and request.pending
    assert instance.route(request)
    assert calls == [request] and request.dispatched and not request.pending
    assert not request.cancellation_token.is_set()
    with pytest.raises(FrozenInstanceError):
        request.session_id = 'invented'
    metadata = request.to_metadata()
    assert metadata == request.host_metadata()
    assert metadata['session_id'] == 'actual-session'
    assert original not in json.dumps(metadata)


@pytest.mark.parametrize('text',['fix the failing tests','run the tests','unknown request'])
def test_medium_tasks_require_explicit_transcript_review(text):
    instance,calls = router()
    request = instance.create_request(text,SCOPE)
    assert request.impact == VoiceImpact.MEDIUM
    with pytest.raises(VoiceConfirmationRequired):
        instance.route(request)
    assert not calls
    confirmed = instance.confirm(request,source='ui')
    assert confirmed.confirmation == VoiceConfirmation.CONFIRMED
    assert instance.route(confirmed)
    assert calls == [confirmed]


@pytest.mark.parametrize('text',[
    'delete the project','send the report','deploy this application','publish the changes',
    'use my API key','rm -rf /tmp/project','format disk D','drop table users',
    'explain what "delete the project" means','dosyaları sil','şifreyi göster',
    'değişiklikleri gönder','projeyi yayınla','kodları paylaş',
])
def test_high_impact_remains_high_after_ui_review_even_when_quoted(text):
    instance,calls = router()
    request = instance.create_request(text,SCOPE)
    assert request.impact == VoiceImpact.HIGH
    assert request.operation_display == text
    with pytest.raises(VoiceConfirmationRequired):
        instance.route(request)
    confirmed = instance.confirm(request)
    assert confirmed.impact == VoiceImpact.HIGH
    assert instance.route(confirmed)
    assert calls[0].to_metadata()['impact'] == 'high'


@pytest.mark.parametrize('text',['delete it','remove that','erase','sil onu','onu sil','bunu kaldır','hepsini sil'])
def test_ambiguous_destructive_utterance_never_reaches_agent(text):
    instance,calls = router()
    with pytest.raises(VoiceNeedsClarification):
        instance.create_request(text,SCOPE)
    assert not calls


@pytest.mark.parametrize('text',['approve','yes','allow all','approve all tools','approve pending tool calls',
                                'Firik, onayla','tüm araçları onayla','planı onayla'])
def test_speech_cannot_approve_a_tool_or_plan(text):
    instance,calls = router()
    with pytest.raises(VoiceApprovalRejected):
        instance.create_request(text,SCOPE)
    assert not calls


def test_forged_review_flag_and_spoken_confirmation_source_are_rejected():
    instance,calls = router()
    request = instance.create_request('delete the project',SCOPE)
    with pytest.raises(VoiceApprovalRejected):
        instance.confirm(request,source='voice')
    forged = replace(request,confirmation=VoiceConfirmation.CONFIRMED)
    with pytest.raises(VoiceScopeError):
        instance.route(forged)
    assert not calls


@pytest.mark.parametrize('changed',[
    replace(SCOPE,session_id='other-session'),replace(SCOPE,project_id='other-project'),
    replace(SCOPE,workspace_id='other-workspace'),replace(SCOPE,target='conversation'),
    replace(SCOPE,revision=18),
])
def test_switching_session_project_workspace_or_revision_invalidates_pending_request(changed):
    instance,calls = router()
    request = instance.create_request('explain architecture',SCOPE)
    with pytest.raises(VoiceScopeError):
        instance.route(request,current_scope=changed)
    assert not calls and request.cancellation_token.is_set()


def test_route_requires_current_scope_evidence():
    instance = VoiceRequestRouter(lambda request:True)
    request = instance.create_request('explain architecture',SCOPE)
    with pytest.raises(VoiceScopeError):
        instance.route(request)


@pytest.mark.parametrize('text,command,destination',[
    ('open settings',VoiceCommand.OPEN_TAB,'settings'),
    ('Ayarlar sekmesini aç',VoiceCommand.OPEN_TAB,'settings'),
    ('pause agent',VoiceCommand.PAUSE_AGENT,None),
    ('ajanı durdur',VoiceCommand.STOP_RESPONSE,None),
    ('resume task',VoiceCommand.RESUME_AGENT,None),
    ('sesi kapat',VoiceCommand.MUTE_SPEECH,None),
    ('unmute speech',VoiceCommand.UNMUTE_SPEECH,None),
    ('replay speech',VoiceCommand.REPLAY_SPEECH,None),
    ('view activity',VoiceCommand.VIEW_ACTIVITY,None),
    ('Firik, describe the active project',VoiceCommand.DESCRIBE_PROJECT,None),
    ('etkin projeyi anlat',VoiceCommand.DESCRIBE_PROJECT,None),
    ('which agent is working',VoiceCommand.AGENT_STATUS,None),
    ('çalışan ajanlar',VoiceCommand.AGENT_STATUS,None),
])
def test_local_commands_use_exact_handlers_and_no_agent_dispatch(text,command,destination):
    local = []
    instance,calls = router(commands={command:lambda request:local.append(request)})
    request = instance.create_request(text,SCOPE)
    assert request.command == command
    assert (request.destination.id if request.destination else None) == destination
    if request.requires_confirmation:
        request = instance.confirm(request)
    assert instance.route(request)
    assert local == [request] and not calls


@pytest.mark.parametrize('text,kind',[
    ('open conversation Design Review','conversation'),('select chat id chat-123','conversation'),
    ('open project Evren','project'),('proje Evren aç','project'),('Evren projesini aç','project'),
])
def test_existing_destinations_resolve_only_actual_ids_or_unique_exact_names(text,kind):
    inventory = [VoiceDestination('conversation','chat-123','Design Review','project-123'),
                 VoiceDestination('project','project-123','Evren')]
    selected = []
    command = VoiceCommand.SELECT_PROJECT if kind == 'project' else VoiceCommand.SELECT_CONVERSATION
    instance,calls = router(commands={command:lambda request:selected.append(request.destination)},inventory=inventory)
    request = instance.create_request(text,SCOPE)
    assert request.destination == next(d for d in inventory if d.kind == kind)
    assert instance.route(instance.confirm(request))
    assert len(selected) == 1 and not calls


@pytest.mark.parametrize('text',['open project Evr','open conversation id missing','open project','select chat'])
def test_unknown_or_incomplete_navigation_does_not_fall_back_to_agent(text):
    instance,calls = router(inventory=[VoiceDestination('project','p','Evren')])
    with pytest.raises(VoiceNeedsClarification):
        instance.create_request(text,SCOPE)
    assert not calls


def test_duplicate_names_and_removed_inventory_require_clarification():
    inventory = [VoiceDestination('conversation','a','Review'),VoiceDestination('conversation','b','Review')]
    instance,calls = router(inventory=inventory,commands={VoiceCommand.SELECT_CONVERSATION:lambda request:True})
    with pytest.raises(VoiceNeedsClarification):
        instance.create_request('open chat Review',SCOPE)
    request = instance.confirm(instance.create_request('open chat id a',SCOPE))
    inventory.clear()
    with pytest.raises(VoiceNeedsClarification):
        instance.route(request)
    assert not calls


@pytest.mark.parametrize('text',['continue','repeat that','summarize your answer','devam et'])
def test_conversation_requests_reuse_existing_agent_handler(text):
    instance,calls = router()
    request = instance.create_request(text,SCOPE)
    assert request.command is None and request.category == VoiceCategory.CONVERSATION
    if request.requires_confirmation:
        request = instance.confirm(request)
    assert instance.route(request)
    assert calls[0].session_id == SCOPE.session_id


def test_unsupported_pause_does_not_invent_agent_api():
    instance,calls = router()
    request = instance.confirm(instance.create_request('pause agent',SCOPE))
    with pytest.raises(VoiceUnsupportedCommand):
        instance.route(request)
    assert not calls


def test_one_utterance_is_dispatched_once_even_after_history_eviction():
    instance,calls = router(history=1)
    request = instance.create_request('explain architecture',SCOPE,utterance_id='same-utterance')
    assert instance.route(request)
    with pytest.raises(VoiceDuplicateRequest):
        instance.route(request)
    with pytest.raises(VoiceDuplicateRequest):
        instance.create_request('explain architecture',SCOPE,utterance_id='same-utterance')
    instance.create_request('explain architecture again',SCOPE)
    assert not request.cancellation_token.is_set()
    with pytest.raises(VoiceDuplicateRequest):
        instance.route(request)
    assert calls == [request] and len(instance._requests) == 1


def test_rejected_busy_submission_can_retry_without_duplicate_acceptance():
    responses = iter([False,True])
    instance,_ = router(handler=lambda request:next(responses))
    request = instance.create_request('explain architecture',SCOPE)
    assert not instance.route(request) and request.pending
    assert instance.route(request) and request.dispatched
    with pytest.raises(VoiceDuplicateRequest):
        instance.route(request)


def test_partial_handler_failure_is_never_retried():
    calls = []
    def fail(request):
        calls.append(request)
        raise RuntimeError('partial submit')
    instance,_ = router(handler=fail)
    request = instance.create_request('explain architecture',SCOPE)
    with pytest.raises(RuntimeError):
        instance.route(request)
    with pytest.raises(VoiceDuplicateRequest):
        instance.route(request)
    assert len(calls) == 1


def test_history_and_close_cancel_only_unsent_requests():
    instance,calls = router(history=2)
    sent = instance.create_request('explain architecture',SCOPE)
    instance.route(sent)
    pending = instance.create_request('fix bug',SCOPE)
    instance.close()
    assert pending.cancellation_token.is_set()
    assert not sent.cancellation_token.is_set()
    with pytest.raises(VoiceScopeError):
        instance.route(pending)


def test_review_edit_preserves_provenance_and_resets_confirmation_and_risk():
    instance,calls = router()
    first = instance.confirm(instance.create_request('Firik, explain architecture',SCOPE,recognized_language='en'))
    edited = instance.revise(first,'delete the project')
    assert edited.original_transcript == first.original_transcript
    assert edited.utterance_id == first.utterance_id and edited.scope == first.scope
    assert edited.created_at == first.created_at and edited.cancellation_token is first.cancellation_token
    assert edited.recognized_language == 'en' and edited.impact == VoiceImpact.HIGH
    assert edited.confirmation == VoiceConfirmation.PENDING
    with pytest.raises(VoiceConfirmationRequired):
        instance.route(edited)
    with pytest.raises(VoiceScopeError):
        instance.route(first)
    assert instance.route(instance.confirm(edited))


def test_parallel_route_attempts_cannot_submit_twice():
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def handler(request):
        calls.append(request)
        entered.set()
        release.wait(1)
        return True
    instance,_ = router(handler=handler)
    request = instance.create_request('explain architecture',SCOPE)
    worker = threading.Thread(target=instance.route,args=(request,))
    worker.start()
    assert entered.wait(1)
    try:
        with pytest.raises(VoiceDuplicateRequest):
            instance.route(request)
    finally:
        release.set()
        worker.join(1)
    assert len(calls) == 1


@pytest.mark.parametrize('text,category,impact',[
    ('inspect source files',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('read README.md',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('show git status',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('git diff',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('summarize code',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('summarize test failures',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('günlükleri oku',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('dosyaları incele',VoiceCategory.READ_ONLY,VoiceImpact.LOW),
    ('fix failing tests',VoiceCategory.CODING,VoiceImpact.MEDIUM),
    ('run lint',VoiceCategory.CODING,VoiceImpact.MEDIUM),
    ('build this project',VoiceCategory.CODING,VoiceImpact.MEDIUM),
    ('debug the failure',VoiceCategory.CODING,VoiceImpact.MEDIUM),
    ('plan the implementation',VoiceCategory.CODING,VoiceImpact.MEDIUM),
    ('test authentication',VoiceCategory.CODING,VoiceImpact.MEDIUM),
    ('read API key from file',VoiceCategory.READ_ONLY,VoiceImpact.HIGH),
    ('edit the code and publish it',VoiceCategory.CODING,VoiceImpact.HIGH),
    ('new chat',VoiceCategory.SESSION_CONTROL,VoiceImpact.MEDIUM),
    ('open settings',VoiceCategory.NAVIGATION,VoiceImpact.MEDIUM),
])
def test_deterministic_request_taxonomy(text,category,impact):
    instance,_ = router()
    request = instance.create_request(text,SCOPE)
    assert request.category == category
    assert request.impact == impact


def test_review_guard_tracks_only_exact_issued_and_confirmed_request():
    instance,_ = router()
    request = instance.create_request('fix this bug',SCOPE)
    assert request._dispatch_guard.issued_request is request
    assert request._dispatch_guard.confirmed_request is None
    confirmed = instance.confirm(request)
    assert confirmed._dispatch_guard.issued_request is confirmed
    assert confirmed._dispatch_guard.confirmed_request is confirmed
    forged = replace(confirmed,request_text='delete the project')
    assert forged._dispatch_guard.issued_request is not forged
    edited = instance.revise(confirmed,'build the project')
    assert edited._dispatch_guard.issued_request is edited
    assert edited._dispatch_guard.confirmed_request is None
