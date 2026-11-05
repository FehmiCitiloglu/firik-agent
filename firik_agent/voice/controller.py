"""Generation-bound voice sessions; native audio and network work never run on Tk."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from collections import deque
from dataclasses import replace
import threading
import time
import traceback
from typing import Any, Awaitable, Callable

from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.telemetry import log_voice_timing

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 32_000
MAX_SPEECH_CHARS = 8_000


from firik_agent.voice.session import CaptureCoordinator, VoiceState, _TRANSITIONS


_DEFAULT_COORDINATOR = CaptureCoordinator()


def _ignore_callback(*args: Any) -> None:
    pass


async def _ignore_transcription(audio: bytes, model: str, language: str) -> str:
    return ""


class VoiceController:
    """Own one session's capture, transcription and speech generations.

    ``session_id`` is rebound only by ``bind_session`` which cancels pending
    work. Every queued delivery validates its generation and captured session.
    Ending listening never invokes agent cancellation; the view owns that
    independent, authorized operation.
    """
    def __init__(self, runtime: Any, dispatch: Callable[..., None],
                 transcribe: Callable[[bytes, str, str], Awaitable[str]],
                 settings: VoiceSettings, on_state: Callable[[str], None],
                 on_transcript: Callable[[str], None], on_error: Callable[[str], None],
                 recorder: Any = None, speaker: Any = None,
                 coordinator: CaptureCoordinator | None = None,
                 session_id: str | None = None, on_level=None, speaker_factory=None,
                 speaker_revision=None) -> None:
        self.runtime, self.settings = runtime, settings
        self._dispatch, self._transcribe = dispatch, transcribe
        self._on_state, self._on_transcript, self._on_error = on_state, on_transcript, on_error
        self._recorder, self._speaker = recorder, speaker
        self._speaker_factory = speaker_factory
        self._speaker_revision = speaker_revision
        self._speaker_key = None
        self._lock = threading.RLock()
        self.coordinator = coordinator or _DEFAULT_COORDINATOR
        self._device_lock = self.coordinator.capture_lock  # compatibility
        self._generation = 0
        self._speech_generation = 0
        self.session_id = session_id
        self._state = VoiceState.IDLE if settings.enabled else VoiceState.DISABLED
        self._closed = False
        self._record_stop = self._record_cancel = self._speech_cancel = None
        self._future: Future | None = None
        self._completed_transcription = -1
        self._transcript_pending = False
        self._review_pending = False
        self._utterance_ended_at = None
        self._response_audio_started_at = None
        self._conversation = False
        self._paused = False
        self._capture_mode = "ptt"
        self._agent_busy = False
        self._workers: set[threading.Thread] = set()
        self._speech_queue = deque()
        self._last_speech = None
        self._stream_buffer = None
        self._stream_chars = 0
        self._stream_cancelled = False
        self._muted = False
        from firik_agent.voice.events import LatestEvent
        def deliver_level(event):
            generation, value = event
            if self._active(generation):
                (on_level or _ignore_callback)(value)
        self._levels = LatestEvent(dispatch, deliver_level)

    @property
    def supports_barge_in(self) -> bool:
        """Duplex capture is opt-in only for stacks with verified echo handling."""
        return (getattr(self._recorder, "supports_barge_in", False) is True
                and getattr(self._speaker, "supports_barge_in", False) is True)

    def _start_worker(self, target, args, name) -> bool:
        def work():
            try:
                target(*args)
            finally:
                with self._lock:
                    self._workers.discard(threading.current_thread())
        with self._lock:
            if self._closed or len(self._workers) >= 4:
                return False
            worker = threading.Thread(target=work, name=name, daemon=True)
            self._workers.add(worker)
            try:
                worker.start()
            except RuntimeError:
                self._workers.discard(worker)
                return False
        return True

    @property
    def generation(self) -> int:
        """Review controls can reject work invalidated by provider/tab changes."""
        with self._lock:
            return self._generation

    @property
    def state(self) -> str:
        # Keep existing dictation integrations working; state_id is canonical.
        with self._lock:
            if self._capture_mode == "ptt":
                if self._state == VoiceState.LISTENING:
                    return "recording"
                if self._state in (VoiceState.AWAITING_CONFIRMATION, VoiceState.ERROR):
                    return "idle"
            return self._state.value

    @property
    def state_id(self) -> VoiceState:
        with self._lock:
            return self._state

    @property
    def conversation_active(self) -> bool:
        with self._lock:
            return self._conversation

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def _active(self, generation: int) -> bool:
        with self._lock:
            return not self._closed and generation == self._generation

    def _post(self, generation: int, callback: Callable[..., None], *args: Any) -> None:
        session_id = self.session_id
        def deliver() -> None:
            with self._lock:
                if not self._active(generation) or session_id != self.session_id:
                    return
                callback(*args)
        self._dispatch(deliver)

    def _invalidate_locked(self, *, speech=True, release_capture=True) -> int:
        self._generation += 1
        for event in (self._record_stop, self._record_cancel):
            if event is not None:
                event.set()
        if speech:
            self._speech_queue.clear()
            self._speech_generation += 1
            if self._speech_cancel is not None:
                self._speech_cancel.set()
            self._speech_cancel = None
        if self._future is not None:
            self._future.cancel()
        self._future = None
        self._transcript_pending = self._review_pending = False
        self._utterance_ended_at = None
        self._record_stop = self._record_cancel = None
        if release_capture:
            self.coordinator.release(self)
        return self._generation

    def _set_state(self, generation: int, state: VoiceState | str) -> bool:
        state = VoiceState(state)
        with self._lock:
            if not self._active(generation):
                return False
            current = self._state
            universal = {VoiceState.IDLE, VoiceState.DISABLED, VoiceState.INTERRUPTED, VoiceState.ERROR, VoiceState.PERMISSION_REQUIRED}
            if state != current and state not in universal and state not in _TRANSITIONS.get(current, set()):
                return False
            self._state = state
            display = self.state
        self._post(generation, self._on_state, display)
        return True

    def bind_session(self, session_id: str | None) -> None:
        with self._lock:
            if session_id == self.session_id:
                return
            self.cancel()
            self._agent_busy = False
            self._last_speech = None
            self.session_id = session_id

    def sync_agent_state(self, busy: bool) -> None:
        """Refresh the selected session's status without starting audio work."""
        with self._lock:
            if self._closed:
                return
            self._agent_busy = busy
            if self._state in (VoiceState.LISTENING, VoiceState.SPEECH_DETECTED,
                               VoiceState.TRANSCRIBING, VoiceState.AWAITING_CONFIRMATION,
                               VoiceState.SPEAKING):
                return
            generation = self._generation
            if not self.settings.enabled:
                state = VoiceState.DISABLED
            elif busy:
                state = VoiceState.THINKING
            elif self._conversation and self._paused:
                state = VoiceState.INTERRUPTED
            else:
                state = VoiceState.IDLE
        self._set_state(generation, state)

    @property
    def can_replay_speech(self) -> bool:
        with self._lock:
            return not self._closed and self._last_speech is not None

    def start_recording(self) -> bool:
        self.settings = replace(self.settings, interaction_mode="push_to_talk")
        return self._begin_capture("ptt")

    def retry_transcription(self) -> bool:
        """Compatibility entry point for an explicit fresh push-to-talk capture.

        A retry replaces transcript review or recovers from an input error.
        Microphone bytes from previous attempts are released after ASR and are
        never reused by this method.
        """
        with self._lock:
            if self._closed or self._state not in (
                    VoiceState.AWAITING_CONFIRMATION, VoiceState.ERROR, VoiceState.PERMISSION_REQUIRED):
                return False
            self.settings = replace(self.settings, interaction_mode="push_to_talk")
            return self._begin_capture("ptt", replace_review=True)

    def _begin_capture(self, mode: str, *, preserve_speech=False, replace_review=False) -> bool:
        with self._lock:
            if (self._closed or not self.settings.enabled or (self._transcript_pending and not replace_review)
                    or self._state in (VoiceState.LISTENING, VoiceState.SPEECH_DETECTED, VoiceState.TRANSCRIBING)
                    or (self._agent_busy and mode == "conversation")):
                return False
            if not self.coordinator.claim(self):
                return False
            generation = self._invalidate_locked(speech=not preserve_speech, release_capture=False)
            self._capture_mode = mode
            if mode == "ptt":
                self._stream_cancelled = True
            settings = self.settings
            stop, cancel = threading.Event(), threading.Event()
            self._record_stop, self._record_cancel = stop, cancel
        if not self._set_state(generation, VoiceState.LISTENING):
            self.coordinator.release(self)
            return False
        if not self._start_worker(self._capture, (generation, settings, stop, cancel, mode), "FirikVoiceCapture"):
            self._fail(generation, "Ses aygıtı kapanıyor; kısa süre sonra yeniden deneyin.")
            return False
        return True

    def stop_recording(self) -> None:
        with self._lock:
            if self._state in (VoiceState.LISTENING, VoiceState.SPEECH_DETECTED) and self._record_stop is not None:
                self._record_stop.set()

    def start_conversation(self) -> bool:
        with self._lock:
            if self._closed or not self.settings.enabled or self._conversation:
                return False
            # This explicit user action enables local VAD for this session;
            # saved preferences never start microphone capture on startup.
            self.settings = replace(self.settings, interaction_mode="conversation", vad_enabled=True)
            self._conversation, self._paused = True, False
        started = self._begin_capture("conversation")
        if not started:
            with self._lock:
                self._conversation = False
        return started

    def pause_conversation(self) -> None:
        with self._lock:
            if not self._conversation or self._closed:
                return
            self._paused = True
            generation = self._invalidate_locked(speech=False)
        self._set_state(generation, VoiceState.INTERRUPTED)

    def resume_conversation(self) -> bool:
        with self._lock:
            if self._closed or not self._conversation or not self._paused:
                return False
            self._paused = False
            busy = self._agent_busy
        if busy:
            self._set_state(self._generation, VoiceState.THINKING)
            return True
        if self._begin_capture("conversation"):
            return True
        with self._lock:
            self._paused = self._conversation
        return False

    def exit_conversation(self) -> None:
        self.cancel()

    def stop_listening(self) -> None:
        with self._lock:
            if self._conversation:
                self.pause_conversation()
                return
            # A listening stop discards input and does not stop speech or agent.
            if self._state == VoiceState.SPEAKING:
                return
            generation = self._invalidate_locked(speech=False)
        self._set_state(generation, VoiceState.IDLE if self.settings.enabled else VoiceState.DISABLED)

    def _speech_started(self, generation: int) -> None:
        with self._lock:
            if not self._active(generation):
                return
            # VAD detected the user's interruption locally. Never cancel agent.
            if self.settings.barge_in and self.supports_barge_in and self._speech_cancel is not None:
                self._speech_cancel.set()
                self._speech_generation += 1
                self._speech_cancel = None
                self._speech_queue.clear()
        self._set_state(generation, VoiceState.SPEECH_DETECTED)

    def _capture(self, generation, settings, stop, cancel, mode) -> None:
        started = time.monotonic()
        try:
            with self.coordinator.native_lock(self.coordinator.capture_lock, cancel) as acquired:
                if not acquired or not self._active(generation):
                    log_voice_timing("capture", "cancelled", started)
                    return
                if self._recorder is None:
                    from firik_agent.voice.audio import SoundDeviceRecorder
                    self._recorder = SoundDeviceRecorder()
                if not self.coordinator.begin_capture(cancel, duplex=self.supports_barge_in and settings.barge_in):
                    log_voice_timing("capture", "cancelled", started)
                    return
                try:
                    if hasattr(self._recorder, "configure"):
                        self._recorder.configure(target_rate=settings.sample_rate, input_gain=settings.input_gain,
                            vad_engine=settings.vad_engine, pre_roll_ms=settings.vad_pre_roll_ms,
                            on_level=lambda level: self._levels.publish((generation, level)))
                    if mode == "conversation":
                        audio = self._recorder.record_utterance(
                            stop, cancel, settings.max_record_seconds, device=settings.input_device,
                            threshold=settings.vad_threshold, silence_ms=settings.vad_silence_ms,
                            min_speech_ms=settings.vad_min_speech_ms,
                            on_speech_start=lambda: self._speech_started(generation))
                    else:
                        audio = self._recorder.record(stop, cancel, settings.max_record_seconds, device=settings.input_device)
                finally:
                    self.coordinator.end_capture()
            if not self._active(generation) or cancel.is_set():
                log_voice_timing("capture", "cancelled", started)
                return
            # Empty VAD windows are local silence. Keep listening without an
            # HTTP call; bounded native windows still release resources.
            if mode == "conversation" and audio == b"":
                log_voice_timing("capture", "silence", started)
                self._set_state(generation, VoiceState.READY)
                self._post(generation, self._resume_if_needed)
                return
            if not isinstance(audio, bytes) or not audio or len(audio) > MAX_AUDIO_BYTES:
                log_voice_timing("capture", "empty_or_invalid", started)
                self._fail(generation, "Kayıt boş veya izin verilen ses boyutunu aşıyor. Lütfen yeniden konuşun.")
                return
            ended = time.monotonic()
            with self._lock:
                if not self._active(generation):
                    return
                self._utterance_ended_at = ended
            log_voice_timing("capture", "ok", started, finished_at=ended)
            self._submit_transcription(generation, audio, settings)
        except Exception as error:
            from firik_agent.voice.audio import VoiceAudioError, VoicePermissionError
            message = (str(error) if isinstance(error, VoiceAudioError) else
                       "Mikrofon kaydı veya ses çözümleme başlatılamadı. Mikrofon iznini ve ses kurulumunu kontrol edin.")
            permission = isinstance(error, VoicePermissionError)
            log_voice_timing("capture", "permission" if permission else "failed", started)
            self._fail(generation, message, permission=permission)
        finally:
            # A newer generation can be queued behind our native close. Only
            # release its lease if it is still this generation's capture.
            with self._lock:
                if self._active(generation):
                    self.coordinator.release(self)

    def _submit_transcription(self, generation, audio, settings) -> bool:
        if not self._set_state(generation, VoiceState.TRANSCRIBING):
            return False

        async def transcribe():
            nonlocal audio
            from firik_agent.voice.providers import voice_error_message

            started = time.monotonic()
            status = "failed"
            try:
                result = await self._transcribe(audio, settings.model, settings.language)
                if isinstance(result, str) and result.strip() and len(result) <= MAX_TRANSCRIPT_CHARS:
                    status = "ok"
                return result, None
            except asyncio.CancelledError as error:
                status = "cancelled"
                # A retained cancelled Future can retain its asyncio Task's
                # exception. Clear unwound provider frames before propagation.
                traceback.clear_frames(error.__traceback__)
                raise
            except Exception as error:
                # The Future retains its result during delivery. An exception
                # traceback can retain this coroutine and the microphone bytes.
                return None, voice_error_message(error)
            finally:
                audio = b""
                log_voice_timing("asr", status, started)

        def deliver(outcome):
            result, error = outcome
            if error is not None:
                self._fail(generation, error)
            else:
                self._transcription_done(generation, result)

        coroutine = transcribe()
        with self._lock:
            if not self._active(generation):
                coroutine.close()
                return False
            try:
                future = self.runtime.submit(coroutine, on_done=deliver,
                    on_error=lambda error: self._transcription_error(generation, error))
            except Exception:
                coroutine.close()
                raise
            if self._active(generation) and not future.done():
                self._future = future
            elif not self._active(generation):
                future.cancel()
        return True

    def _transcription_error(self, generation, error):
        from firik_agent.voice.providers import voice_error_message
        self._fail(generation, voice_error_message(error))

    def _transcription_done(self, generation, result) -> None:
        with self._lock:
            if (not self._active(generation) or self._state != VoiceState.TRANSCRIBING
                    or self._completed_transcription == generation):
                return
            self._completed_transcription = generation
        if not isinstance(result, str) or not result.strip() or len(result) > MAX_TRANSCRIPT_CHARS:
            self._fail(generation, "Anlaşılır bir konuşma alınamadı veya metin sınırı aşıldı. Lütfen yeniden konuşun.")
            return
        with self._lock:
            if not self._active(generation):
                return
            self._future = None
            self._transcript_pending = self._review_pending = True
        if self._set_state(generation, VoiceState.AWAITING_CONFIRMATION):
            self._post(generation, self._deliver_transcript, result.strip())

    def _deliver_transcript(self, text) -> None:
        self._transcript_pending = False
        self._on_transcript(text)

    def transcript_resolved(self, submitted: bool = False) -> bool:
        with self._lock:
            if self._closed or not self._review_pending:
                return False
            self._review_pending = self._transcript_pending = False
            self._utterance_ended_at = None
            generation = self._generation
            if submitted:
                self._agent_busy = True
        self._set_state(generation, VoiceState.THINKING if submitted else VoiceState.READY)
        if not submitted:
            self._resume_if_needed()
        return True

    def review_text(self, text: str) -> bool:
        """Stage a typed/suggested request with the same scoped review lifecycle.

        This explicit UI action cancels unfinished input and playback. It does
        not transcribe, submit an agent request, or grant an operation approval.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TRANSCRIPT_CHARS:
            return False
        with self._lock:
            if self._closed or not self.settings.enabled or self._review_pending or self._transcript_pending:
                return False
            generation = self._invalidate_locked()
            self._review_pending = self._transcript_pending = True
            # Typed review has no capture/transcribing states to pass through.
            self._state = VoiceState.AWAITING_CONFIRMATION
        self._post(generation, self._on_state, self.state)
        self._post(generation, self._deliver_transcript, text.strip())
        return True

    def response_started(self, state="thinking", *, from_voice=False) -> None:
        self.stop_speaking()
        with self._lock:
            self._response_audio_started_at = self._utterance_ended_at if from_voice else None
            self._stream_buffer = None
            self._stream_chars = 0
            self._stream_cancelled = False
        self.mark_agent_state(state)

    def response_delta(self, text: str) -> None:
        """Accept visible assistant prose deltas; partial syntax stays local."""
        with self._lock:
            if (self._closed or self._stream_cancelled or not self.settings.enabled
                    or not self.settings.read_replies or not self.settings.stream_replies
                    or not self._agent_busy or not isinstance(text, str)):
                return
            if self._stream_chars + len(text) > MAX_SPEECH_CHARS:
                self.stop_speaking()
                return
            if self._stream_buffer is None:
                from firik_agent.voice.synthesis import SentenceBoundaryBuffer
                self._stream_buffer = SentenceBoundaryBuffer()
            self._stream_chars += len(text)
            try:
                sentences = self._stream_buffer.feed(text)
                for sentence in sentences:
                    if not self.speak(sentence, response_audio=True):
                        self.stop_speaking()
                        break
            except Exception:
                self.stop_speaking()

    def mark_agent_state(self, state="thinking") -> None:
        if state not in ("thinking", "executing"):
            raise ValueError("Agent state must be thinking or executing")
        with self._lock:
            self._agent_busy = True
            # Typed submissions suspend voice without terminating conversation.
            if self._state in (VoiceState.LISTENING, VoiceState.SPEECH_DETECTED, VoiceState.TRANSCRIBING):
                generation = self._invalidate_locked()
                self._set_state(generation, VoiceState.INTERRUPTED)
            else:
                generation = self._generation
            self._review_pending = False
            self._utterance_ended_at = None
        self._set_state(generation, state)

    def response_completed(self, text: str | None = None) -> None:
        with self._lock:
            streamed = self._stream_buffer is not None or self._stream_cancelled
            if self._stream_buffer is not None and not text:
                self.stop_speaking()
            if self._stream_buffer is not None and not self._stream_cancelled and text:
                try:
                    for sentence in self._stream_buffer.feed("", final=True):
                        if not self.speak(sentence, response_audio=True):
                            break
                except Exception:
                    self.stop_speaking()
            self._stream_buffer = None
            if streamed:
                text = None
            speaking = self._speech_cancel is not None
            self._agent_busy = False
            generation = self._generation
            if self._closed:
                return
            if speaking:
                return
            if not self.settings.enabled:
                self._set_state(generation, VoiceState.DISABLED)
                return
            # An unrelated completion cannot consume the user's new utterance.
            if self._state in (VoiceState.LISTENING, VoiceState.SPEECH_DETECTED, VoiceState.TRANSCRIBING, VoiceState.AWAITING_CONFIRMATION):
                return
        self._set_state(generation, VoiceState.READY)
        if text and self.settings.read_replies and self.speak(text, response_audio=True):
            return
        self._resume_if_needed()

    def _resume_if_needed(self) -> None:
        with self._lock:
            resume = self._conversation and not self._paused and not self._agent_busy and not self._review_pending and not self._closed
            preserve_speech = (self.settings.barge_in and self.supports_barge_in and self._speech_cancel is not None
                               and not self._speech_cancel.is_set())
            if self._speech_cancel is not None and not preserve_speech:
                resume = False
        if resume:
            # A bounded silent VAD window may expire while long playback is
            # still running. Renew local monitoring without cutting it off.
            self._begin_capture("conversation", preserve_speech=preserve_speech)

    def _fail(self, generation, message, *, permission=False) -> None:
        with self._lock:
            if not self._active(generation):
                return
            generation = self._invalidate_locked(speech=False)
            self._paused = self._conversation
            if self._speech_cancel is None:
                self._stream_cancelled = True
                self._stream_buffer = None
            state = VoiceState.PERMISSION_REQUIRED if permission else VoiceState.ERROR
        if self._set_state(generation, state):
            self._post(generation, self._on_error, message)

    def cancel(self) -> None:
        with self._lock:
            if self._closed:
                return
            generation = self._invalidate_locked()
            self._conversation = self._paused = False
            self._response_audio_started_at = None
            self._stream_buffer = None
            self._stream_cancelled = True
        self._set_state(generation, VoiceState.IDLE if self.settings.enabled else VoiceState.DISABLED)

    def speak(self, text: str, *, response_audio=False) -> bool:
        with self._lock:
            if (self._closed or self._muted or not self.settings.enabled or self._transcript_pending
                    or self._state in (VoiceState.LISTENING, VoiceState.SPEECH_DETECTED, VoiceState.TRANSCRIBING)
                    or not isinstance(text, str) or not text.strip()):
                return False
            started = self._response_audio_started_at if response_audio else None
            if self._speech_cancel is not None and not self._speech_cancel.is_set():
                if len(self._speech_queue) >= 8:
                    return False
                if response_audio:
                    self._response_audio_started_at = None
                self._speech_queue.append((text[:MAX_SPEECH_CHARS], self.settings, started))
                return True
            if response_audio:
                self._response_audio_started_at = None
            generation = self._invalidate_locked()
            settings = self.settings
            cancel = threading.Event()
            self._speech_cancel = cancel
            ticket = self._speech_generation
            self._last_speech = text[:MAX_SPEECH_CHARS]
        if not self._set_state(generation, VoiceState.SPEAKING):
            return False
        if not self._start_worker(self._speak, (ticket, text[:MAX_SPEECH_CHARS], settings, cancel, started), "FirikVoiceSpeech"):
            self.stop_speaking()
            self._fail(generation, "Ses aygıtı kapanıyor; kısa süre sonra yeniden deneyin.")
            return False
        if self._conversation and not self._paused and settings.barge_in and self.supports_barge_in:
            # Capture has a separate generation/lock. Speech validity uses its
            # own ticket so starting local VAD does not stop playback.
            self._begin_capture("conversation", preserve_speech=True)
        return True

    def _speak(self, ticket, text, settings, cancel, started) -> None:
        from firik_agent.voice.synthesis import EngineSpeaker

        speech_started = time.monotonic()
        status = "cancelled"
        try:
            with self.coordinator.native_lock(self.coordinator.speech_lock, cancel) as acquired:
                if not acquired or self._closed or ticket != self._speech_generation:
                    return
                revision = self._speaker_revision() if self._speaker_revision is not None else None
                key = (revision, settings.tts_provider, settings.tts_model, settings.tts_voice,
                       settings.tts_format, settings.playback_speed)
                if self._speaker_factory is not None and (self._speaker is None or key != self._speaker_key):
                    if self._speaker is not None and hasattr(self._speaker, "shutdown"):
                        self._speaker.shutdown()
                    self._speaker = self._speaker_factory(settings)
                    self._speaker_key = key
                elif self._speaker is None:
                    self._speaker = EngineSpeaker(output_device=settings.output_device, volume=settings.output_volume)
                if not self.coordinator.begin_speech(cancel, duplex=self.supports_barge_in and settings.barge_in):
                    return
                try:
                    audio_timing = getattr(self._speaker, "supports_audio_timing", False)
                    if audio_timing:
                        self._speaker.resume()
                    while not cancel.is_set():
                        if hasattr(self._speaker, "configure"):
                            self._speaker.configure(output_device=settings.output_device, volume=settings.output_volume)
                        timing = {"utterance_ended_at": started} if audio_timing else {}
                        self._speaker.speak(text, cancel, language=settings.language, rate=settings.speech_rate, **timing)
                        with self._lock:
                            if cancel.is_set() or ticket != self._speech_generation or not self._speech_queue:
                                if ticket == self._speech_generation:
                                    self._speech_cancel = None
                                break
                            text, settings, started = self._speech_queue.popleft()
                            self._last_speech = text
                finally:
                    self.coordinator.end_speech()
            with self._lock:
                if self._closed or ticket != self._speech_generation or cancel.is_set():
                    return
                status = "ok"
                self._speech_cancel = None
                # Barge-in capture may already own current state/generation.
                if self._state in (VoiceState.LISTENING, VoiceState.SPEECH_DETECTED, VoiceState.TRANSCRIBING, VoiceState.AWAITING_CONFIRMATION):
                    return
                current = self._generation
            self._set_state(current, VoiceState.READY if self._conversation else VoiceState.IDLE)
            self._post(current, self._resume_if_needed)
        except Exception as error:
            from firik_agent.voice.speech import VoiceSpeechError
            from firik_agent.voice.errors import VoiceAudioError
            message = str(error) if isinstance(error, (VoiceSpeechError, VoiceAudioError)) else "Yanıt seslendirilemedi. İşletim sisteminin konuşma motorunu kontrol edin."
            with self._lock:
                # The sentence queue also sets cancel when its provider fails.
                # An intentional stop invalidates the ticket; a matching ticket
                # must still surface authentication, credit and network errors.
                if ticket != self._speech_generation:
                    return
                status = "failed"
                current = self._generation
                self._speech_cancel = None
                self._speech_queue.clear()
                self._response_audio_started_at = None
                self._stream_cancelled = True
                self._stream_buffer = None
                if self._state == VoiceState.SPEAKING:
                    self._paused = self._conversation
                    self._set_state(current, VoiceState.ERROR)
            self._post(current, self._on_error, message)
        finally:
            log_voice_timing("speech", status, speech_started)

    def stop_speaking(self) -> None:
        with self._lock:
            self._response_audio_started_at = None
            self._stream_cancelled = True
            self._stream_buffer = None
            if self._speech_cancel is None:
                return
            self._speech_cancel.set()
            self._speech_cancel = None
            self._speech_queue.clear()
            self._speech_generation += 1
            generation = self._generation
            speaking = self._state == VoiceState.SPEAKING
        if speaking:
            self._set_state(generation, VoiceState.IDLE)
            self._resume_if_needed()

    def pause_speaking(self) -> bool:
        speaker = self._speaker
        if speaker is not None and hasattr(speaker, "pause"):
            speaker.pause()
            return True
        return False

    def mute(self) -> bool:
        with self._lock:
            self._muted = True
        self.stop_speaking()
        return True

    def unmute(self) -> bool:
        with self._lock:
            self._muted = False
        return True

    def resume_speaking(self) -> bool:
        speaker = self._speaker
        if speaker is not None and hasattr(speaker, "resume"):
            speaker.resume()
            return True
        return False

    def replay_speech(self) -> bool:
        with self._lock:
            text = self._last_speech
        return bool(text and self.speak(text))

    def shutdown(self, timeout=3.0) -> bool:
        """Called by the service cleanup worker, never by Tk or an audio callback."""
        self.close()
        deadline = time.monotonic() + max(0, timeout)
        with self._lock:
            workers = tuple(self._workers)
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(max(0, deadline - time.monotonic()))
        done = all(not worker.is_alive() for worker in workers)
        if done and self._speaker is not None and hasattr(self._speaker, "shutdown"):
            self._speaker.shutdown()
        return done

    def close(self) -> None:
        """Invalidate callbacks and release ownership without joining native I/O."""
        with self._lock:
            if self._closed:
                return
            self._invalidate_locked()
            self._response_audio_started_at = None
            self._last_speech = None
            self._stream_buffer = None
            self._conversation = self._paused = False
            self._state = VoiceState.DISABLED
            self._closed = True
            self._levels.close()
            self._on_state = self._on_transcript = self._on_error = _ignore_callback
            self._dispatch = _ignore_callback
            self._transcribe = _ignore_transcription
