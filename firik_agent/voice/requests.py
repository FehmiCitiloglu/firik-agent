"""Immutable, scoped voice requests; these values grant no tool permission."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import threading
from uuid import uuid4

from firik_agent.voice.commands import VoiceCommand


class VoiceImpact(str, Enum):
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'


class VoiceCategory(str, Enum):
    CONVERSATION = 'conversation'
    TASK = 'task'
    READ_ONLY = 'read_only'
    CODING = 'coding'
    SESSION_CONTROL = 'session_control'
    NAVIGATION = 'navigation'
    AGENT_CONTROL = 'agent_control'
    SPEECH_CONTROL = 'speech_control'
    APP_STATE = 'app_state'


class VoiceConfirmation(str, Enum):
    PENDING = 'pending'
    CONFIRMED = 'confirmed'


class VoiceRouteError(ValueError):
    """Safe routing failure for transcript review."""


class VoiceNeedsClarification(VoiceRouteError):
    pass


class VoiceConfirmationRequired(VoiceRouteError):
    pass


class VoiceScopeError(VoiceRouteError):
    pass


class VoiceDuplicateRequest(VoiceRouteError):
    pass


class VoiceApprovalRejected(VoiceRouteError):
    pass


class VoiceUnsupportedCommand(VoiceRouteError):
    pass


@dataclass(frozen=True)
class VoiceScope:
    session_id: str
    project_id: str | None = None
    workspace_id: str | None = None
    target: str = 'conversation'
    revision: str | int | None = None

    def __post_init__(self):
        for name in ('session_id', 'project_id', 'workspace_id'):
            value = getattr(self, name)
            if value is None and name != 'session_id':
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > 4096:
                raise VoiceScopeError('Ses isteği mevcut bir oturum ve geçerli kapsam gerektirir.')
        if self.target not in ('conversation', 'coding'):
            raise VoiceScopeError('Ses isteğinin hedefi geçersiz.')
        if self.revision is not None and (isinstance(self.revision, bool) or not isinstance(self.revision, (str, int))):
            raise VoiceScopeError('Ses oturumu sürümü geçersiz.')


@dataclass(frozen=True)
class VoiceDestination:
    kind: str
    id: str
    label: str
    project_id: str | None = None
    workspace_id: str | None = None
    session_id: str | None = None

    def __post_init__(self):
        if self.kind not in ('conversation', 'project', 'tab') or not isinstance(self.id, str) or not self.id.strip():
            raise VoiceScopeError('Ses gezinme hedefi geçersiz.')
        if not isinstance(self.label, str) or not self.label.strip():
            raise VoiceScopeError('Ses gezinme hedefinin adı geçersiz.')


class _DispatchGuard:
    """Shared across immutable review replacements, bounded by request lifetime."""
    def __init__(self):
        self.lock = threading.Lock()
        self.state = 'pending'
        self.ui_confirmed = False
        self.confirmed_request = None
        self.issued_request = None


@dataclass(frozen=True)
class VoiceRequest:
    session_id: str
    original_transcript: str
    request_text: str
    utterance_id: str = field(default_factory=lambda: str(uuid4()))
    project_id: str | None = None
    workspace_id: str | None = None
    recognized_language: str | None = None
    intended_target: str = 'conversation'
    origin: str = 'voice'
    confirmation: VoiceConfirmation = VoiceConfirmation.PENDING
    impact: VoiceImpact = VoiceImpact.MEDIUM
    category: VoiceCategory = VoiceCategory.TASK
    command: VoiceCommand | None = None
    destination: VoiceDestination | None = None
    operation_display: str = ''
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cancellation_token: threading.Event = field(default_factory=threading.Event, compare=False, repr=False)
    scope_revision: str | int | None = None
    _dispatch_guard: _DispatchGuard = field(default_factory=_DispatchGuard, compare=False, repr=False)

    def __post_init__(self):
        VoiceScope(self.session_id, self.project_id, self.workspace_id, self.intended_target, self.scope_revision)
        if self.origin != 'voice':
            raise VoiceScopeError('Ses isteğinin kaynağı geçersiz.')
        for text in (self.original_transcript, self.request_text):
            if not isinstance(text, str) or not text.strip() or len(text) > 32000:
                raise VoiceRouteError('Ses isteği boş veya metin sınırını aşıyor.')
        if not isinstance(self.utterance_id, str) or not self.utterance_id or len(self.utterance_id) > 200:
            raise VoiceRouteError('Ses isteği kimliği geçersiz.')
        if self.recognized_language is not None and (not isinstance(self.recognized_language, str) or not self.recognized_language or len(self.recognized_language) > 64):
            raise VoiceRouteError('Tanınan konuşma dili geçersiz.')
        if not isinstance(self.created_at,datetime) or self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise VoiceRouteError('Ses isteği zaman damgası saat dilimi gerektirir.')
        object.__setattr__(self, 'created_at', self.created_at.astimezone(timezone.utc))
        if not isinstance(self.confirmation, VoiceConfirmation) or not isinstance(self.impact, VoiceImpact) or not isinstance(self.category, VoiceCategory):
            raise VoiceRouteError('Ses isteğinin inceleme bilgisi geçersiz.')
        if not hasattr(self.cancellation_token, 'is_set') or not hasattr(self.cancellation_token, 'set'):
            raise VoiceRouteError('Ses isteği iptal belirteci gerektirir.')

    @property
    def scope(self):
        return VoiceScope(self.session_id, self.project_id, self.workspace_id, self.intended_target, self.scope_revision)

    @property
    def requires_confirmation(self):
        return self.impact != VoiceImpact.LOW

    @property
    def pending(self):
        with self._dispatch_guard.lock:
            return self._dispatch_guard.state == 'pending' and not self.cancellation_token.is_set()

    @property
    def dispatched(self):
        with self._dispatch_guard.lock:
            return self._dispatch_guard.state in ('dispatching', 'consumed')

    def to_metadata(self):
        """Host-owned execution metadata; transcripts stay in the user message."""
        return dict(origin=self.origin, utterance_id=self.utterance_id, session_id=self.session_id,
                    project_id=self.project_id, workspace_id=self.workspace_id,
                    intended_target=self.intended_target, recognized_language=self.recognized_language,
                    confirmation=self.confirmation.value, impact=self.impact.value,
                    category=self.category.value, created_at=self.created_at.isoformat())

    host_metadata = to_metadata
