"""Application-wide native audio ownership and voice session states."""
from __future__ import annotations

from enum import Enum
import threading

class VoiceState(str, Enum):
    DISABLED = "disabled"
    IDLE = "idle"
    PERMISSION_REQUIRED = "permission_required"
    READY = "ready"
    LISTENING = "listening"
    SPEECH_DETECTED = "speech_detected"
    TRANSCRIBING = "transcribing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    THINKING = "thinking"
    EXECUTING = "executing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ERROR = "error"


# Cancellation is valid from every state. A new explicit action can recover
# from ERROR. Only the controller changes state; views consume queued events.
_TRANSITIONS = {
    VoiceState.DISABLED: {VoiceState.READY, VoiceState.IDLE, VoiceState.LISTENING, VoiceState.SPEAKING},
    VoiceState.IDLE: {VoiceState.READY, VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.THINKING, VoiceState.EXECUTING},
    VoiceState.READY: {VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.THINKING, VoiceState.EXECUTING},
    VoiceState.PERMISSION_REQUIRED: {VoiceState.LISTENING, VoiceState.READY},
    VoiceState.LISTENING: {VoiceState.SPEECH_DETECTED, VoiceState.TRANSCRIBING, VoiceState.READY},
    VoiceState.SPEECH_DETECTED: {VoiceState.TRANSCRIBING, VoiceState.READY},
    VoiceState.TRANSCRIBING: {VoiceState.AWAITING_CONFIRMATION},
    VoiceState.AWAITING_CONFIRMATION: {VoiceState.THINKING, VoiceState.READY, VoiceState.TRANSCRIBING, VoiceState.SPEAKING, VoiceState.LISTENING},
    VoiceState.THINKING: {VoiceState.EXECUTING, VoiceState.SPEAKING, VoiceState.READY, VoiceState.LISTENING},
    VoiceState.EXECUTING: {VoiceState.THINKING, VoiceState.SPEAKING, VoiceState.READY, VoiceState.LISTENING},
    VoiceState.SPEAKING: {VoiceState.READY, VoiceState.LISTENING, VoiceState.TRANSCRIBING, VoiceState.SPEECH_DETECTED},
    VoiceState.INTERRUPTED: {VoiceState.READY, VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.THINKING, VoiceState.EXECUTING},
    VoiceState.ERROR: {VoiceState.READY, VoiceState.LISTENING, VoiceState.SPEAKING, VoiceState.TRANSCRIBING, VoiceState.THINKING},
}


class CaptureCoordinator:
    """Application-owned microphone lease and native close serialization.

    A cancelled controller releases its logical lease immediately. Its worker
    retains ``capture_lock`` until the native stream actually closes, so a slow
    backend cannot overlap with its replacement. Playback has a separate lock
    to permit local VAD barge-in while speech is playing.
    """
    def __init__(self) -> None:
        self.capture_lock = threading.Lock()
        self.speech_lock = threading.Lock()
        self._lock = threading.Lock()
        self._owner = None
        self._duplex = threading.Condition()
        self._capture_active = False
        self._speech_active = False
        self._capture_duplex = False
        self._speech_duplex = False

    def begin_capture(self, cancel, *, duplex=False) -> bool:
        with self._duplex:
            while self._speech_active and not (duplex and self._speech_duplex):
                if cancel.is_set():
                    return False
                self._duplex.wait(.02)
            if cancel.is_set():
                return False
            self._capture_active = True
            self._capture_duplex = duplex
            return True

    def end_capture(self):
        with self._duplex:
            self._capture_active = False
            self._capture_duplex = False
            self._duplex.notify_all()

    def begin_speech(self, cancel, *, duplex=False) -> bool:
        with self._duplex:
            while self._capture_active and not (duplex and self._capture_duplex):
                if cancel.is_set():
                    return False
                self._duplex.wait(.02)
            if cancel.is_set():
                return False
            self._speech_active = True
            self._speech_duplex = duplex
            return True

    def end_speech(self):
        with self._duplex:
            self._speech_active = False
            self._speech_duplex = False
            self._duplex.notify_all()

    def claim(self, owner) -> bool:
        with self._lock:
            if self._owner is not None and self._owner is not owner:
                return False
            self._owner = owner
            return True

    def release(self, owner) -> None:
        with self._lock:
            if self._owner is owner:
                self._owner = None
