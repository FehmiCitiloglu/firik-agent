"""Deterministic, scoped routing into existing UI/agent entry points.

No classifier model, tool execution, chat persistence, or permission granting
lives here. Confirmation records transcript review only; execution still passes
through the application's existing tool and policy approval boundaries.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
import re
import threading
import unicodedata
from typing import Mapping

from firik_agent.voice.commands import (VoiceCommand, _normalize, command_tab,
    parse_command, requires_review, selection_command, strip_invocation)
from firik_agent.voice.requests import (VoiceApprovalRejected, VoiceCategory, VoiceConfirmation,
    VoiceConfirmationRequired, VoiceDestination, VoiceDuplicateRequest, VoiceImpact,
    VoiceNeedsClarification, VoiceRequest, VoiceRouteError, VoiceScope,
    VoiceScopeError, VoiceUnsupportedCommand)


_HIGH_IMPACT = re.compile(
    r'\b(?:delete|remove|erase|wipe|destroy|purge|truncate|format|fdisk|mkfs|shred|overwrite|'
    r'drop|deploy|publish|upload|share|post|invite|notify|send|email|message|'
    r'purchase|buy|sell|pay|refund|checkout|transfer|trade|approve|authorize|grant|allow|revoke|passwords?|secrets?|tokens?|credentials?|'
    r'api[ _-]?keys?|private[ _-]?keys?|access[ _-]?keys?|ssh[ _-]?keys?|bearer|oauth|push|merge|commit|sudo|rm|rmdir|'
    r'sil|silin|silme|kaldir|kaldır|temizle|yayinla|yayınla|gönder|gonder|'
    r'satin|satın|öde|ode|aktar|parola|şifre|sifre|anahtar|formatla|biçimlendir|sifirla|kimlik bilgileri)\b|'
    r'\b(?:yok et|pull request|paylaş|paylas)\b|'
    r'\b(?:şifre|sifre|parola|anahtar|sil|gönder|gonder|yayinla|yayınla|paylaş|paylas|formatla)\w*\b|'
    r'\bdrop\s+(?:table|database)\b|\breset\s+--hard\b', re.IGNORECASE)
_AMBIGUOUS_DESTRUCTIVE = re.compile(
    r'(?:(?:please )?(?:delete|remove|erase|wipe|destroy)(?: (?:it|that|them|this|everything|all))?'
    r'|(?:lütfen )?(?:sil|kaldir|kaldır|temizle)(?: (?:onu|bunu|hepsini|onlari|onları))?'
    r'|(?:lütfen )?(?:onu|bunu|hepsini|onlari|onları) (?:sil|kaldir|kaldır|temizle)'
    r'|(?:can|could|would) you (?:please )?(?:delete|remove|erase|wipe|destroy) (?:it|that|them|this|all|everything)'
    r'|(?:onu|bunu|hepsini|onlari|onları) (?:siler|kaldirir|temizler) misin)')
_APPROVAL_PHRASES = {
    'approve', 'approve tool', 'approve the tool', 'approve plan', 'approve the plan',
    'approve all', 'allow all', 'allow tool', 'allow the tool', 'confirm', 'confirmed',
    'yes', 'yes proceed', 'go ahead', 'onayla', 'onayliyorum', 'onaylıyorum',
    'hepsini onayla', 'araci onayla', 'aracı onayla', 'plani onayla', 'planı onayla',
    'evet', 'izin ver', 'tumune izin ver', 'tümüne izin ver',
}
_APPROVAL_COMMAND = re.compile(
    r'(?:approve|allow|authorize|confirm)(?:\s+(?:all|the|this|these|pending|current))*'
    r'\s+(?:tools?|tool calls?|actions?|plans?|requests?|permissions?)(?:\s+.*)?|'
    r'(?=.*\b(?:araç\w*|arac\w*|plan\w*|izin\w*)\b).*\b(?:onayla|onayliyorum|onay ver|izin ver)')
_SPEECH = {VoiceCommand.STOP_SPEECH,VoiceCommand.READ_LAST,VoiceCommand.REPLAY_SPEECH,
           VoiceCommand.MUTE_SPEECH,VoiceCommand.UNMUTE_SPEECH}
_AGENT = {VoiceCommand.STOP_RESPONSE,VoiceCommand.PAUSE_AGENT,VoiceCommand.RESUME_AGENT}
_APP_STATE = {VoiceCommand.DESCRIBE_PROJECT,VoiceCommand.AGENT_STATUS,VoiceCommand.VIEW_ACTIVITY,
              VoiceCommand.LIST_PROJECTS,VoiceCommand.LIST_CONVERSATIONS,VoiceCommand.TASK_SUMMARY,
              VoiceCommand.SHOW_PLAN,VoiceCommand.HELP}
_CONVERSATION = {'repeat that','repeat your answer','say that again','summarize','summarize that',
                 'summarize your answer','continue','continue your answer','tekrar et',
                 'yaniti tekrar et','yanıtı tekrar et','özetle','ozetle','devam et'}
_SESSION = {VoiceCommand.NEW_CHAT,VoiceCommand.SELECT_CONVERSATION,VoiceCommand.SWITCH_CODING}
_CODING_TASK = re.compile(
    r'\b(?:fix|modify|change|edit|implement|refactor|write|create|add|update|patch|debug|'
    r'lint|build|plan|configure|optimize|run|execute|düzelt|duzelt|değiştir|degistir|'
    r'uygula|yaz|ekle|güncelle|guncelle|derle|planla|çalıştır|calistir)\b')
_READ_ONLY_VERB = re.compile(
    r'\b(?:inspect|show|read|list|summarize|explain|describe|analyze|compare|'
    r'incele|göster|goster|oku|listele|özetle|ozetle|açikla|acikla|anlat|karşilaştir|karsilastir)\b')
_READ_ONLY_TARGET = re.compile(
    r'\b(?:files?|directories|source|code|logs?|git|diff|status|tests?|failures?|errors?|stacktrace)\b|'
    r'\b(?:dosya|klasör|klasor|kaynak|kod|günlük|gunluk|hata|test|durum)\w*\b|'
    r'(?:^|\s)\S+\.(?:py|js|ts|tsx|jsx|json|md|txt|yaml|yml|log|toml)\b')


def classify_impact(text: str, command: VoiceCommand | None = None) -> VoiceImpact:
    """Conservative text risk; even quoted sensitive instructions stay reviewed."""
    value = _normalize(strip_invocation(text))
    if _HIGH_IMPACT.search(value):
        return VoiceImpact.HIGH
    if command in _SPEECH or command in _APP_STATE:
        return VoiceImpact.LOW
    if command is not None:
        return VoiceImpact.MEDIUM
    return VoiceImpact.MEDIUM if requires_review(text) else VoiceImpact.LOW


def _label(value):
    return ' '.join(unicodedata.normalize('NFC',value).casefold().replace('i\u0307','i').split())


class VoiceRequestRouter:
    def __init__(self, on_submit, command_handlers=None, *, scope_provider=None,
                 inventory_provider=None, max_history=512):
        if not callable(on_submit):
            raise TypeError('on_submit must be callable')
        if isinstance(max_history,bool) or not isinstance(max_history,int) or not 1 <= max_history <= 4096:
            raise ValueError('max_history must be between 1 and 4096')
        self.on_submit = on_submit
        self.command_handlers = dict(command_handlers or {})
        self.scope_provider = scope_provider
        self.inventory_provider = inventory_provider
        self.max_history = max_history
        self._requests = OrderedDict()
        self._lock = threading.RLock()

    def _inventory(self):
        supplied = self.inventory_provider() if self.inventory_provider else ()
        if isinstance(supplied, Mapping):
            supplied = supplied.values()
        result = []
        for item in supplied:
            if isinstance(item,Mapping):
                item = VoiceDestination(**item)
            if not isinstance(item,VoiceDestination):
                raise VoiceNeedsClarification('Gezinme hedefleri doğrulanamadı. Mevcut sohbet veya projeyi arayüzden seçin.')
            result.append(item)
            if len(result) > 10000:
                raise VoiceNeedsClarification('Çok fazla gezinme hedefi var. Arayüzden bir hedef seçin.')
        return tuple(result)

    def _resolve_selection(self, selection):
        command, selector, explicit = selection
        kind = 'conversation' if command == VoiceCommand.SELECT_CONVERSATION else 'project'
        candidates = [d for d in self._inventory() if d.kind == kind]
        # Exact IDs take precedence; labels require a unique exact match.
        matches = [d for d in candidates if d.id == selector]
        if not matches and not explicit:
            matches = [d for d in candidates if _label(d.label) == _label(selector)]
        if len(matches) != 1:
            raise VoiceNeedsClarification('Hedef bulunamadı veya adı belirsiz. Mevcut sohbet/proje kimliğini ya da tam adını belirtin.')
        return matches[0]

    def _build_request(self, transcript: str, scope: VoiceScope, *, recognized_language=None,
                       request_text=None, utterance_id=None):
        if not isinstance(scope,VoiceScope):
            raise VoiceScopeError('Ses isteği mevcut oturum kapsamına bağlanmalıdır.')
        if not isinstance(transcript,str) or not transcript.strip() or len(transcript) > 32000:
            raise VoiceRouteError('Ses isteği boş veya metin sınırını aşıyor.')
        normalized = strip_invocation(transcript if request_text is None else request_text).strip()
        if not normalized or len(normalized) > 32000:
            raise VoiceRouteError('İncelenen ses isteği boş veya metin sınırını aşıyor.')
        value = _normalize(normalized)
        approval_value = re.sub(r'^(?:lütfen|please)\s+|\s+(?:lütfen|please)$', '', value).strip()
        if approval_value in {_normalize(t) for t in _APPROVAL_PHRASES} or _APPROVAL_COMMAND.fullmatch(approval_value):
            raise VoiceApprovalRejected('Sesli onay kabul edilmez. Araç veya plan onayını ilgili arayüz denetiminden verin.')
        if _AMBIGUOUS_DESTRUCTIVE.fullmatch(value):
            raise VoiceNeedsClarification('Neyin silineceğini veya değiştirileceğini açıkça belirtin; belirsiz yıkıcı istek gönderilmedi.')
        command = parse_command(normalized)
        destination = None
        selection = selection_command(normalized)
        if command == VoiceCommand.OPEN_TAB:
            tab = command_tab(normalized)
            destination = VoiceDestination('tab',tab,tab)
        elif selection:
            command = selection[0]
            destination = self._resolve_selection(selection)
        elif value in ('open project','select project','open conversation','select conversation',
                       'select chat','proje aç','proje seç','sohbet aç','sohbet seç'):
            raise VoiceNeedsClarification('Mevcut sohbet veya projenin tam adını ya da kimliğini belirtin.')
        impact = classify_impact(normalized,command)
        if command in _SPEECH:
            category = VoiceCategory.SPEECH_CONTROL
        elif command in _AGENT:
            category = VoiceCategory.AGENT_CONTROL
        elif command in _APP_STATE:
            category = VoiceCategory.APP_STATE
        elif command in _SESSION:
            category = VoiceCategory.SESSION_CONTROL
        elif command is not None:
            category = VoiceCategory.NAVIGATION
        elif value in _CONVERSATION:
            category = VoiceCategory.CONVERSATION
        elif _CODING_TASK.search(value):
            category = VoiceCategory.CODING
            if impact == VoiceImpact.LOW:
                impact = VoiceImpact.MEDIUM
        elif (_READ_ONLY_VERB.search(value) and _READ_ONLY_TARGET.search(value)) or value in ('git status','git diff'):
            category = VoiceCategory.READ_ONLY
            if impact != VoiceImpact.HIGH:
                impact = VoiceImpact.LOW
        elif value in ('test','tests') or value.startswith('test '):
            category = VoiceCategory.CODING
            if impact == VoiceImpact.LOW:
                impact = VoiceImpact.MEDIUM
        elif impact == VoiceImpact.LOW:
            category = VoiceCategory.CONVERSATION
        else:
            category = VoiceCategory.TASK
        fields = dict(session_id=scope.session_id,project_id=scope.project_id,workspace_id=scope.workspace_id,
                      intended_target=scope.target,scope_revision=scope.revision,original_transcript=transcript,
                      request_text=normalized,recognized_language=recognized_language,command=command,
                      destination=destination,impact=impact,category=category,operation_display=normalized)
        if utterance_id is not None:
            fields['utterance_id'] = utterance_id
        request = VoiceRequest(**fields)
        return request

    def create_request(self, transcript: str, scope: VoiceScope, *, recognized_language=None,
                       request_text=None, utterance_id=None):
        request = self._build_request(transcript, scope, recognized_language=recognized_language,
                                      request_text=request_text, utterance_id=utterance_id)
        with self._lock:
            if request.utterance_id in self._requests:
                raise VoiceDuplicateRequest('Bu konuşma bölümü zaten inceleniyor.')
            while len(self._requests) >= self.max_history:
                _,old = self._requests.popitem(last=False)
                if old.pending:
                    old.cancellation_token.set()
            self._requests[request.utterance_id] = request
            request._dispatch_guard.issued_request = request
        return request

    def revise(self, request: VoiceRequest, text: str):
        """Reclassify an edited transcript while preserving its original scope.

        Every edit returns to pending review; it never inherits an earlier UI
        confirmation. No agent conversation or destination ID is generated.
        """
        with self._lock:
            self._registered(request)
            with request._dispatch_guard.lock:
                if request._dispatch_guard.state != 'pending':
                    raise VoiceDuplicateRequest('Gönderilmiş ses isteği düzenlenemez.')
                revised = self._build_request(request.original_transcript,request.scope,
                    request_text=text,utterance_id=request.utterance_id,
                    recognized_language=request.recognized_language)
                revised = replace(revised,cancellation_token=request.cancellation_token,
                                  _dispatch_guard=request._dispatch_guard,created_at=request.created_at)
                request._dispatch_guard.ui_confirmed = False
                request._dispatch_guard.confirmed_request = None
                request._dispatch_guard.issued_request = revised
                self._requests[request.utterance_id] = revised
                return revised

    def _registered(self,request):
        if not isinstance(request,VoiceRequest):
            raise VoiceRouteError('Geçerli bir ses isteği gereklidir.')
        if request.cancellation_token.is_set():
            raise VoiceScopeError('Ses isteği iptal edildi; yeni bir kayıt başlatın.')
        if self._requests.get(request.utterance_id) is not request:
            raise VoiceScopeError('Ses isteği eski veya inceleme bilgisi değiştirildi; yeniden inceleyin.')

    def confirm(self, request: VoiceRequest, *, source='ui'):
        """Called only by trusted UI review controls, never by transcript text.

        Confirmation is not an execution/tool approval and is retained in host
        metadata so the execution layer can require its own exact action review.
        """
        if source != 'ui':
            raise VoiceApprovalRejected('Transkript onayı yalnızca arayüz incelemesinden verilebilir.')
        with self._lock:
            self._registered(request)
            with request._dispatch_guard.lock:
                if request._dispatch_guard.state != 'pending':
                    raise VoiceDuplicateRequest('Bu ses isteği zaten gönderildi.')
                confirmed = replace(request,confirmation=VoiceConfirmation.CONFIRMED)
                request._dispatch_guard.ui_confirmed = True
                request._dispatch_guard.confirmed_request = confirmed
                request._dispatch_guard.issued_request = confirmed
                self._requests[request.utterance_id] = confirmed
                return confirmed

    def route(self, request: VoiceRequest, *, current_scope: VoiceScope | None = None):
        if not isinstance(request,VoiceRequest):
            raise VoiceRouteError('Geçerli bir ses isteği gereklidir.')
        guard = request._dispatch_guard
        with self._lock:
            with guard.lock:
                if guard.state != 'pending':
                    raise VoiceDuplicateRequest('Bu ses isteği zaten gönderildi.')
                self._registered(request)
                scope = current_scope if current_scope is not None else self.scope_provider() if self.scope_provider else None
                if not isinstance(scope,VoiceScope) or scope != request.scope:
                    request.cancellation_token.set()
                    raise VoiceScopeError('Etkin oturum, proje veya çalışma alanı değişti. Yeni kapsamda yeniden konuşun.')
                if request.requires_confirmation and (request.confirmation != VoiceConfirmation.CONFIRMED or not guard.ui_confirmed):
                    raise VoiceConfirmationRequired('Bu isteğin metnini ve etkisini arayüzde inceleyip onaylayın.')
                if request.command is not None:
                    handler = self.command_handlers.get(request.command) or self.command_handlers.get(request.command.value)
                    if not callable(handler):
                        raise VoiceUnsupportedCommand('Bu işlem mevcut oturumda desteklenmiyor; ilgili arayüz denetimini kullanın.')
                    if request.destination and request.destination.kind != 'tab':
                        # Confirm the item still exists, without ever replacing it
                        # with another conversation sharing its old display name.
                        if request.destination not in self._inventory():
                            raise VoiceNeedsClarification('Seçilen hedef artık mevcut değil. Yeni bir hedef seçin.')
                else:
                    handler = self.on_submit
                guard.state = 'dispatching'
        try:
            result = handler(request)
        except BaseException:
            with guard.lock:
                guard.state = 'consumed'  # Partial side effects must not repeat.
            raise
        accepted = result is not False if request.command is not None else result is True
        with self._lock:
            with guard.lock:
                guard.state = 'consumed' if accepted else 'pending'
        return accepted

    def cancel(self,request):
        with self._lock:
            self._registered(request)
            request.cancellation_token.set()
            self._requests.pop(request.utterance_id,None)

    def close(self):
        with self._lock:
            requests = tuple(self._requests.values())
            self._requests.clear()
        for request in requests:
            if request.pending:
                request.cancellation_token.set()


__all__ = ['VoiceCommand','VoiceRequestRouter','VoiceRequest','VoiceScope','VoiceDestination',
           'VoiceImpact','VoiceCategory','VoiceConfirmation','VoiceRouteError',
           'VoiceNeedsClarification','VoiceConfirmationRequired','VoiceScopeError',
           'VoiceDuplicateRequest','VoiceApprovalRejected','VoiceUnsupportedCommand',
           'classify_impact','parse_command','strip_invocation','requires_review']
