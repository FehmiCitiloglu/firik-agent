"""Voice lifecycle, GUI dispatch and native-resource race regressions."""
from __future__ import annotations

import asyncio
import gc
import queue
import threading
import time
import weakref

import pytest

from firik_agent.core.runtime import AsyncRuntime
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.controller import MAX_AUDIO_BYTES, MAX_TRANSCRIPT_CHARS, VoiceController


def eventually(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


class Dispatch:
    def __init__(self):
        self.jobs = queue.Queue()
        self.thread = threading.get_ident()

    def __call__(self, callback, *args):
        self.jobs.put((callback, args))

    def drain(self):
        assert threading.get_ident() == self.thread
        while not self.jobs.empty():
            callback, args = self.jobs.get_nowait()
            callback(*args)


class Recorder:
    # This injected stack explicitly simulates reliable echo cancellation.
    supports_barge_in = True
    def __init__(self, audio=b"RIFF-test", wait=True):
        self.audio = audio
        self.wait = wait
        self.calls = []
        self.started = threading.Event()
        self.finished = threading.Event()

    def record(self, stop, cancel, max_seconds, device=None):
        self.calls.append((stop, cancel, max_seconds, device))
        self.started.set()
        if self.wait:
            eventually(lambda: stop.is_set() or cancel.is_set())
        self.finished.set()
        return b"" if cancel.is_set() else self.audio


class Speaker:
    supports_barge_in = True
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.finished = threading.Event()

    def speak(self, text, cancel, language, rate):
        self.calls.append((text, cancel, language, rate))
        self.started.set()
        eventually(cancel.is_set)
        self.finished.set()


@pytest.fixture
def runtime():
    instance = AsyncRuntime()
    yield instance
    instance.shutdown()


def controller(runtime, *, recorder=None, speaker=None, transcribe=None, settings=None):
    dispatch = Dispatch()
    states, transcripts, errors, threads, requests = [], [], [], [], []

    async def default_transcribe(audio, model, language):
        requests.append((audio, model, language, threading.get_ident()))
        return "  Merhaba dünya  "

    def callback(target):
        def receive(value):
            threads.append(threading.get_ident())
            target.append(value)
        return receive

    instance = VoiceController(
        runtime, dispatch, transcribe or default_transcribe,
        settings or VoiceSettings(enabled=True), callback(states), callback(transcripts), callback(errors),
        recorder=recorder or Recorder(), speaker=speaker or Speaker(),
    )
    return instance, dispatch, states, transcripts, errors, threads, requests


def test_capture_transcribe_and_callbacks_use_existing_runtime_and_gui_dispatch(runtime):
    recorder = Recorder()
    c, d, states, transcripts, errors, threads, requests = controller(runtime, recorder=recorder,
        settings=VoiceSettings(enabled=True, language="tr", max_record_seconds=12, input_device=3))
    try:
        assert c.start_recording()
        assert recorder.started.wait(1)
        assert not c.start_recording()
        assert not c.speak("Do not overlap capture")
        c.stop_recording()
        eventually(lambda: bool(requests) and c.state == "idle")
        assert not transcripts and not states  # Workers only enqueue.
        d.drain()
        assert states == ["recording", "transcribing", "idle"]
        assert transcripts == ["Merhaba dünya"] and not errors
        assert set(threads) == {threading.get_ident()}
        assert requests[0][:3] == (b"RIFF-test", "qwen3-asr-1.7b", "tr")
        assert requests[0][3] == runtime._thread.ident
        assert recorder.calls[0][2:] == (12, 3)
    finally:
        c.close()


def test_session_cancel_discards_already_queued_transcript(runtime):
    c, d, states, transcripts, errors, _, requests = controller(runtime, recorder=Recorder(wait=False))
    assert c.start_recording()
    eventually(lambda: requests and c.state == "idle")
    c.cancel()  # A tab switch can happen before the UI drains worker callbacks.
    d.drain()
    assert states == ["idle"] and not transcripts and not errors
    c.close()


def test_completed_agent_speech_cannot_invalidate_pending_dictation(runtime):
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=Recorder(wait=False))
    try:
        assert c.start_recording()
        eventually(lambda: requests and c.state == "idle")
        # Agent completion can precede STT's queued GUI delivery even though
        # the runtime has already marked capture complete.
        assert not c.speak("An earlier agent reply has just completed")
        assert not c.start_recording()
        d.drain()
        assert transcripts == ["Merhaba dünya"] and not errors
        assert c.speak("Manual playback works after dictation was delivered")
    finally:
        c.close()


def test_cancel_inflight_stt_and_reject_late_results(runtime):
    entered, cancelled = threading.Event(), threading.Event()

    async def transcribe(*args):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    c, d, states, transcripts, errors, *_ = controller(runtime, recorder=Recorder(wait=False), transcribe=transcribe)
    try:
        c.start_recording()
        assert entered.wait(1)
        c.cancel()
        assert cancelled.wait(1)
        d.drain()
        assert c.state == "idle" and not transcripts and not errors
        assert states == ["idle"]
    finally:
        c.close()


def test_cancel_during_capture_stops_without_upload(runtime):
    recorder = Recorder()
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=recorder)
    c.start_recording()
    assert recorder.started.wait(1)
    c.cancel()
    assert recorder.finished.wait(1)
    d.drain()
    assert recorder.calls[0][1].is_set()
    assert not requests and not transcripts and not errors
    c.close()


def test_serializes_native_jobs_even_when_old_backend_is_slow_to_cancel(runtime):
    class SlowRecorder:
        def __init__(self):
            self.release = threading.Event()
            self.starts = 0
            self.active = 0
            self.max_active = 0

        def record(self, stop, cancel, max_seconds, device=None):
            self.starts += 1
            self.active += 1
            self.max_active = max(self.active, self.max_active)
            assert self.release.wait(2)
            self.active -= 1
            return b"RIFF-test"

    recorder = SlowRecorder()
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=recorder)
    try:
        c.start_recording()
        eventually(lambda: recorder.starts == 1)
        c.cancel()
        assert c.start_recording()
        time.sleep(0.03)
        assert recorder.starts == 1
        recorder.release.set()
        eventually(lambda: requests and c.state == "idle")
        d.drain()
        assert recorder.starts == 2 and recorder.max_active == 1
        assert len(requests) == 1 and transcripts == ["Merhaba dünya"] and not errors
    finally:
        recorder.release.set()
        c.close()


def test_session_switch_serializes_native_devices_across_controllers(runtime):
    class SlowClosingRecorder:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()

        def record(self, *args, **kwargs):
            self.entered.set()
            assert self.release.wait(2)
            return b"RIFF-old-page"

    old_recorder, new_recorder = SlowClosingRecorder(), Recorder()
    old, old_dispatch, _, old_transcripts, _, _, old_requests = controller(runtime, recorder=old_recorder)
    new, new_dispatch, _, new_transcripts, _, _, new_requests = controller(runtime, recorder=new_recorder)
    try:
        old.start_recording()
        assert old_recorder.entered.wait(1)
        old.cancel()
        assert new.start_recording()
        assert not new_recorder.started.wait(.03)
        old_recorder.release.set()
        assert new_recorder.started.wait(1)
        new.cancel()
        assert new_recorder.finished.wait(1)
        old_dispatch.drain()
        new_dispatch.drain()
        assert not old_requests and not new_requests
        assert not old_transcripts and not new_transcripts
    finally:
        old_recorder.release.set()
        old.close()
        new.close()


def test_close_releases_bound_ui_owners_while_native_worker_unwinds(runtime):
    class SlowRecorder:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()

        def record(self, *args, **kwargs):
            self.entered.set()
            assert self.release.wait(2)
            self.finished.set()
            return b"RIFF-cancelled"

    class UI:
        def dispatch(self, callback, *args):
            pass

        def receive(self, value):
            pass

        async def transcribe(self, *args):
            pytest.fail("cancelled capture cannot submit transcription")

    owner, recorder = UI(), SlowRecorder()
    reference = weakref.ref(owner)
    c = VoiceController(runtime, owner.dispatch, owner.transcribe, VoiceSettings(enabled=True),
                        owner.receive, owner.receive, owner.receive, recorder=recorder)
    try:
        c.start_recording()
        assert recorder.entered.wait(1)
        del owner
        assert reference() is not None
        c.close()
        gc.collect()
        assert reference() is None
        assert not recorder.finished.is_set()
    finally:
        recorder.release.set()
        c.close()
        assert recorder.finished.wait(1)


def test_start_mic_interrupts_speech_and_close_suppresses_callbacks(runtime):
    recorder, speaker = Recorder(), Speaker()
    c, d, states, transcripts, errors, *_ = controller(runtime, recorder=recorder, speaker=speaker)
    assert c.speak("Yanıt")
    assert speaker.started.wait(1)
    assert c.start_recording()
    assert speaker.finished.wait(1) and recorder.started.wait(1)
    assert speaker.calls[0][1].is_set()
    c.close()
    assert recorder.finished.wait(1)
    d.drain()
    assert not states and not transcripts and not errors
    assert not c.start_recording() and not c.speak("Closed")
    c.close()  # Idempotent.


def test_stop_speaking_cancels_process_but_does_not_cancel_capture(runtime):
    recorder, speaker = Recorder(), Speaker()
    c, d, _, _, errors, *_ = controller(runtime, recorder=recorder, speaker=speaker)
    try:
        assert c.speak("Read manually even when read_replies is false")
        assert speaker.started.wait(1)
        c.stop_speaking()
        assert speaker.finished.wait(1)
        assert c.state == "idle"
        c.start_recording()
        assert recorder.started.wait(1)
        c.stop_speaking()
        assert c.state == "recording" and not recorder.calls[0][1].is_set()
        d.drain()
        assert not errors
    finally:
        c.close()


@pytest.mark.parametrize("stage", ["capture", "transcribe", "speech"])
def test_backend_errors_are_actionable_without_raw_secret_or_transcript(runtime, stage):
    class BrokenRecorder:
        def record(self, *args, **kwargs):
            raise RuntimeError("secret-key transcript-private /home/private.wav")

    class BrokenSpeaker:
        def speak(self, *args, **kwargs):
            raise RuntimeError("secret-key transcript-private")

    async def broken_transcribe(*args):
        raise RuntimeError("secret-key transcript-private")

    c, d, _, transcripts, errors, *_ = controller(runtime,
        recorder=BrokenRecorder() if stage == "capture" else Recorder(wait=False),
        speaker=BrokenSpeaker(), transcribe=broken_transcribe if stage == "transcribe" else None)
    try:
        c.speak("private") if stage == "speech" else c.start_recording()
        eventually(lambda: c.state == "idle")
        d.drain()
        assert len(errors) == 1 and not transcripts
        assert "secret" not in errors[0] and "private" not in errors[0]
    finally:
        c.close()


@pytest.mark.parametrize("result", ["", "  ", None, {"text": "not a string"}, "x" * (MAX_TRANSCRIPT_CHARS + 1)])
def test_invalid_or_unbounded_transcripts_are_not_dispatched(runtime, result):
    async def transcribe(*args):
        return result

    c, d, _, transcripts, errors, *_ = controller(runtime, recorder=Recorder(wait=False), transcribe=transcribe)
    try:
        c.start_recording()
        eventually(lambda: c.state == "idle")
        d.drain()
        assert len(errors) == 1 and not transcripts
    finally:
        c.close()


def test_disabled_voice_never_opens_device(runtime):
    recorder = Recorder()
    c, d, states, _, errors, *_ = controller(runtime, recorder=recorder, settings=VoiceSettings())
    assert not c.start_recording() and not c.speak("Disabled")
    d.drain()
    assert not recorder.calls and not states and not errors
    c.close()


def test_saved_conversation_preferences_do_not_open_audio_on_startup_or_session_sync(runtime):
    recorder, speaker = UtteranceRecorder(), Speaker()
    c, d, states, transcripts, errors, _, requests = controller(runtime,
        recorder=recorder, speaker=speaker,
        settings=VoiceSettings(enabled=True, interaction_mode="conversation", vad_enabled=True))
    try:
        c.bind_session("restored-session")
        c.sync_agent_state(True)
        c.sync_agent_state(False)
        c.response_started()
        c.response_completed("An ordinary typed conversation remains usable.")
        d.drain()
        assert not c._workers and not c.conversation_active
        assert not recorder.calls and not speaker.calls and not requests
        assert not transcripts and not errors
    finally:
        c.shutdown(2)


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_completed_asr_futures_release_microphone_bytes(runtime, outcome):
    released, entered = threading.Event(), threading.Event()
    proceed = asyncio.Event()

    class MicrophoneBytes(bytes):
        def __del__(self):
            released.set()

    class EphemeralRecorder:
        def record(self, *args, **kwargs):
            return MicrophoneBytes(b"RIFF-synthetic-private-audio")

    async def transcribe(audio, model, language):
        entered.set()
        await proceed.wait()
        if outcome == "failure":
            raise RuntimeError("A failed provider traceback must release its audio argument.")
        return "Final user transcript"

    c, d, _, transcripts, errors, *_ = controller(runtime, recorder=EphemeralRecorder(), transcribe=transcribe)
    try:
        assert c.start_recording() and entered.wait(1)
        eventually(lambda: c._future is not None)
        future = c._future
        assert future is not None
        if outcome == "cancel":
            c.stop_listening()
        else:
            runtime.loop.call_soon_threadsafe(proceed.set)
        eventually(future.done)
        eventually(lambda: not c._workers)
        gc.collect()
        assert released.wait(1)
        d.drain()
        assert transcripts == (["Final user transcript"] if outcome == "success" else [])
        assert len(errors) == (1 if outcome == "failure" else 0)
    finally:
        c.shutdown(2)


def test_stop_listening_cancels_asr_without_terminating_unrelated_runtime_work(runtime):
    entered, cancelled, unrelated_entered = threading.Event(), threading.Event(), threading.Event()
    release_unrelated = asyncio.Event()

    async def transcribe(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def unrelated_agent_work():
        unrelated_entered.set()
        await release_unrelated.wait()
        return "Typed agent completed"

    unrelated = runtime.submit(unrelated_agent_work())
    c, d, _, transcripts, errors, *_ = controller(runtime, recorder=Recorder(wait=False), transcribe=transcribe)
    try:
        assert unrelated_entered.wait(1)
        assert c.start_recording() and entered.wait(1)
        c.stop_listening()
        assert cancelled.wait(1) and not unrelated.done()
        runtime.loop.call_soon_threadsafe(release_unrelated.set)
        assert unrelated.result(1) == "Typed agent completed"
        d.drain()
        assert not transcripts and not errors
    finally:
        runtime.loop.call_soon_threadsafe(release_unrelated.set)
        c.shutdown(2)


@pytest.mark.parametrize("stage,message", [
    ("capture", "Konuşma duyulamadı. Mikrofon ses düzeyini kontrol edip yeniden deneyin."),
    ("speech", "Sesli yanıt için espeak-ng kurun ve ses çıkış aygıtını kontrol edin."),
])
def test_trusted_backend_errors_preserve_actionable_turkish_guidance(runtime, stage, message):
    from firik_agent.voice.audio import VoiceAudioError
    from firik_agent.voice.speech import VoiceSpeechError

    class BrokenRecorder:
        def record(self, *args, **kwargs):
            raise VoiceAudioError(message)

    class BrokenSpeaker:
        def speak(self, *args, **kwargs):
            raise VoiceSpeechError(message)

    c, d, _, _, errors, *_ = controller(runtime, recorder=BrokenRecorder(), speaker=BrokenSpeaker())
    try:
        c.start_recording() if stage == "capture" else c.speak("Merhaba")
        eventually(lambda: c.state == "idle")
        d.drain()
        assert errors == [message]
    finally:
        c.close()


def test_full_120_second_48khz_recording_fits_api_upload_bound(runtime):
    audio = bytes(120 * 48_000 * 2 + 44)
    assert len(audio) <= MAX_AUDIO_BYTES
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=Recorder(audio=audio, wait=False))
    try:
        c.start_recording()
        eventually(lambda: requests and c.state == "idle")
        d.drain()
        assert len(requests[0][0]) == len(audio)
        assert transcripts == ["Merhaba dünya"] and not errors
    finally:
        c.close()


@pytest.mark.parametrize("audio", [b"", bytes(MAX_AUDIO_BYTES + 1)])
def test_empty_or_oversized_audio_never_reaches_http(runtime, audio):
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=Recorder(audio=audio, wait=False))
    try:
        c.start_recording()
        eventually(lambda: c.state == "idle")
        d.drain()
        assert not transcripts and not requests and len(errors) == 1
    finally:
        c.close()


def test_reentrant_cancel_from_state_callback_prevents_stt_submission(runtime):
    requests, states = [], []

    async def transcribe(*args):
        requests.append(args)
        return "Should be suppressed"

    def on_state(state):
        states.append(state)
        if state == "transcribing":
            c.cancel()

    c = VoiceController(runtime, lambda callback: callback(), transcribe, VoiceSettings(enabled=True),
                        on_state, lambda text: pytest.fail("stale transcript"),
                        lambda error: pytest.fail(error), recorder=Recorder(wait=False))
    try:
        assert c.start_recording()
        eventually(lambda: "idle" in states)
        assert states == ["recording", "transcribing", "idle"] and not requests
    finally:
        c.close()


def test_settings_roundtrip_and_missing_settings_are_safe():
    assert not VoiceSettings.from_mapping(None).enabled
    settings = VoiceSettings.from_mapping({"enabled": True, "language": "en", "input_device": "USB Microphone", "unknown_future_key": 1})
    assert VoiceSettings.from_mapping(settings.to_dict()) == settings


@pytest.mark.parametrize("mapping", [
    {"enabled": "false"}, {"read_replies": 1}, {"model": " "}, {"language": "de"},
    {"max_record_seconds": 4}, {"max_record_seconds": 121}, {"max_record_seconds": True},
    {"speech_rate": 79}, {"speech_rate": 351}, {"input_device": False},
    {"input_device": -1}, {"input_device": " "}, [],
])
def test_invalid_settings_never_enable_recording_implicitly(mapping):
    with pytest.raises(ValueError):
        VoiceSettings.from_mapping(mapping)


class UtteranceRecorder(Recorder):
    """Simulate local VAD; each explicit end releases exactly one utterance."""
    def record_utterance(self, stop, cancel, max_seconds, device=None, on_speech_start=None, **options):
        if on_speech_start:
            on_speech_start()
        return self.record(stop, cancel, max_seconds, device)


def test_conversation_reuses_session_across_followups_and_waits_for_agent(runtime):
    recorder = UtteranceRecorder()
    c, d, states, transcripts, errors, _, requests = controller(runtime, recorder=recorder)
    c.bind_session("chat-a")
    try:
        assert c.start_conversation()
        assert recorder.started.wait(1)
        assert c.conversation_active and c.session_id == "chat-a"
        c.stop_recording()
        eventually(lambda: len(requests) == 1 and c.state == "awaiting_confirmation")
        d.drain()
        assert transcripts == ["Merhaba dünya"]
        assert c.transcript_resolved(True)
        assert not c.transcript_resolved(True)  # No repeated submission.
        assert c.state == "thinking" and len(recorder.calls) == 1
        c.mark_agent_state("executing")
        assert c.state == "executing"
        c.response_completed()
        eventually(lambda: len(recorder.calls) == 2)
        assert c.state in ("listening", "speech_detected")
        assert c.session_id == "chat-a" and not errors
    finally:
        c.close()


def test_pause_resume_and_exit_only_cancel_voice_not_agent(runtime):
    recorder = UtteranceRecorder()
    c, d, _, transcripts, _, _, requests = controller(runtime, recorder=recorder)
    try:
        assert c.start_conversation()
        assert recorder.started.wait(1)
        c.mark_agent_state("thinking")  # A typed request can suspend listening.
        assert c.state == "thinking"
        c.pause_conversation()
        assert c.conversation_active and c.paused and c.state == "interrupted"
        assert c.resume_conversation()
        assert c.state == "thinking" and len(recorder.calls) == 1
        c.response_completed()
        eventually(lambda: len(recorder.calls) == 2)
        c.exit_conversation()
        d.drain()
        assert not c.conversation_active and not requests and not transcripts
        assert not c.resume_conversation()
    finally:
        c.close()


def test_conversation_session_switch_discards_queued_transcript(runtime):
    recorder = UtteranceRecorder(wait=False)
    c, d, _, transcripts, _, _, requests = controller(runtime, recorder=recorder)
    try:
        c.bind_session("old")
        assert c.start_conversation()
        eventually(lambda: requests and c.state == "awaiting_confirmation")
        c.bind_session("new")
        d.drain()
        assert c.session_id == "new" and not transcripts
        assert not c.conversation_active
    finally:
        c.close()


def test_application_coordinator_rejects_second_live_capture_controller(runtime):
    from firik_agent.voice.controller import CaptureCoordinator
    coordinator = CaptureCoordinator()
    a, da, _, _, _, _, _ = controller(runtime)
    b, db, _, _, _, _, _ = controller(runtime)
    a.coordinator = b.coordinator = coordinator
    try:
        assert a.start_recording()
        assert a._recorder.started.wait(1)
        assert not b.start_recording()
        a.cancel()
        assert b.start_recording()
        assert b._recorder.started.wait(1)
    finally:
        a.close()
        b.close()


def test_review_can_be_replaced_by_explicit_new_capture(runtime):
    recorder = Recorder(wait=False)
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=recorder)
    try:
        assert c.start_recording()
        eventually(lambda: requests and c.state == "idle")
        d.drain()
        assert c.start_recording()
        eventually(lambda: len(requests) == 2 and c.state == "idle")
        d.drain()
        assert len(recorder.calls) == 2 and len(transcripts) == 2 and not errors
        assert c.transcript_resolved(False)
    finally:
        c.close()


@pytest.mark.parametrize("initial", ["review", "error", "queued_review"])
def test_legacy_retry_transcription_captures_fresh_audio_for_each_asr_attempt(runtime, initial):
    class FreshRecorder:
        def __init__(self):
            self.calls = 0

        def record(self, *args, **kwargs):
            self.calls += 1
            return f"RIFF-synthetic-attempt-{self.calls}".encode()

    requests = []

    async def transcribe(audio, model, language):
        requests.append(audio)
        if initial == "error" and len(requests) == 1:
            raise RuntimeError("First ASR attempt failed")
        return f"Transcript {len(requests)}"

    recorder = FreshRecorder()
    c, d, _, transcripts, errors, *_ = controller(runtime, recorder=recorder, transcribe=transcribe)
    try:
        assert c.start_recording()
        eventually(lambda: c.state_id.value in {"awaiting_confirmation", "error"})
        if initial != "queued_review":
            d.drain()
        assert callable(c.retry_transcription) and c.retry_transcription()
        eventually(lambda: len(requests) == 2 and c.state_id.value == "awaiting_confirmation")
        d.drain()
        assert recorder.calls == 2 and requests[0] != requests[1]
        assert transcripts == (["Transcript 1", "Transcript 2"] if initial == "review" else ["Transcript 2"])
        assert len(errors) == (1 if initial == "error" else 0)
    finally:
        c.shutdown(2)


def test_legacy_retry_transcription_can_recover_after_microphone_permission_changes(runtime):
    from firik_agent.voice.errors import VoicePermissionError

    class PermissionRecorder:
        def __init__(self):
            self.calls = 0

        def record(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise VoicePermissionError("Mikrofon izni gerekli.")
            return b"RIFF-new-permitted-capture"

    recorder = PermissionRecorder()
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=recorder)
    try:
        assert c.start_recording()
        eventually(lambda: c.state_id.value == "permission_required")
        d.drain()
        assert c.retry_transcription()
        eventually(lambda: requests and c.state_id.value == "awaiting_confirmation")
        d.drain()
        assert recorder.calls == 2 and len(requests) == 1
        assert transcripts == ["Merhaba dünya"] and errors == ["Mikrofon izni gerekli."]
    finally:
        c.shutdown(2)


@pytest.mark.parametrize("state", ["disabled", "idle", "ready", "listening", "speech_detected",
                                  "transcribing", "thinking", "executing", "speaking", "interrupted"])
def test_legacy_retry_transcription_rejects_unrelated_states_without_invalidating_work(runtime, state):
    from firik_agent.voice.session import VoiceState

    recorder, speaker = Recorder(), Speaker()
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=recorder, speaker=speaker)
    try:
        c._state = VoiceState(state)
        generation = c.generation
        assert c.retry_transcription() is False
        assert c.generation == generation and c.state_id.value == state
        assert not recorder.calls and not speaker.calls and not requests
        assert not transcripts and not errors
    finally:
        c.shutdown(2)
    assert c.retry_transcription() is False


def test_stop_listening_preserves_playback_and_stop_speaking_preserves_input(runtime):
    recorder, speaker = UtteranceRecorder(), Speaker()
    c, d, _, _, errors, *_ = controller(runtime, recorder=recorder, speaker=speaker)
    try:
        assert c.speak("Response")
        assert speaker.started.wait(1)
        c.stop_listening()
        assert not speaker.calls[0][1].is_set()
        c.stop_speaking()
        assert speaker.finished.wait(1)
        assert c.start_conversation()
        assert recorder.started.wait(1)
        c.stop_speaking()
        assert not recorder.calls[0][1].is_set() and not errors
    finally:
        c.close()


def test_cancelled_speech_waiter_exits_while_other_output_owns_native_lock(runtime):
    c, _, _, _, _, _, _ = controller(runtime, speaker=Speaker())
    with c.coordinator.speech_lock:
        try:
            assert c.speak("Queued response")
            c.stop_speaking()
            eventually(lambda: not c._workers)
            assert not c._speaker.calls
        finally:
            c.close()


def test_barge_in_uses_local_vad_and_interrupts_only_playback(runtime):
    class DeferredOnset(UtteranceRecorder):
        def __init__(self):
            super().__init__()
            self.onset = threading.Event()

        def record_utterance(self, stop, cancel, max_seconds, device=None, on_speech_start=None, **options):
            self.started.set()
            eventually(lambda: self.onset.is_set() or cancel.is_set())
            if not cancel.is_set():
                on_speech_start()
            return self.record(stop, cancel, max_seconds, device)

    recorder, speaker = DeferredOnset(), Speaker()
    c, d, _, _, errors, *_ = controller(runtime, recorder=recorder, speaker=speaker,
        settings=VoiceSettings(enabled=True, barge_in=True))
    try:
        c._conversation = True
        assert c.speak("Response in progress")
        assert speaker.started.wait(1) and recorder.started.wait(1)
        assert not speaker.calls[0][1].is_set()  # Ambient monitoring alone is not interruption.
        recorder.onset.set()
        assert speaker.finished.wait(1)
        eventually(lambda: bool(recorder.calls))
        assert not recorder.calls[0][1].is_set() and not errors
    finally:
        recorder.onset.set()
        c.close()


def test_unvalidated_wake_word_and_unsafe_settings_remain_gated():
    for mapping in ({"wake_word_enabled": True}, {"auto_send": 1}, {"barge_in": "true"},
                    {"shortcut": "<Button-1>"}, {"shortcut": "<Control-v><Control-v>"},
                    {"vad_threshold": False}, {"vad_silence_ms": 0}):
        with pytest.raises(ValueError):
            VoiceSettings.from_mapping(mapping)


def test_permission_failure_exposes_explicit_permission_required_state(runtime):
    from firik_agent.voice.audio import VoicePermissionError

    class DeniedRecorder:
        def record(self, *args, **kwargs):
            raise VoicePermissionError("Mikrofon izni gerekli.")

    c, d, states, _, errors, *_ = controller(runtime, recorder=DeniedRecorder())
    try:
        assert c.start_recording()
        eventually(lambda: c.state == "permission_required")
        d.drain()
        assert states[-1] == "permission_required" and errors == ["Mikrofon izni gerekli."]
    finally:
        c.close()


@pytest.mark.parametrize("recover_with", ["playback", "typed_response"])
def test_microphone_refusal_preserves_playback_and_typed_agent_lifecycle(runtime, recover_with):
    from firik_agent.voice.errors import VoicePermissionError

    class DeniedRecorder:
        def __init__(self):
            self.calls = 0

        def record(self, *args, **kwargs):
            self.calls += 1
            raise VoicePermissionError("Mikrofon izni gerekli.")

    recorder, speaker = DeniedRecorder(), Speaker()
    c, d, states, transcripts, errors, *_ = controller(runtime, recorder=recorder, speaker=speaker)
    try:
        assert c.start_recording()
        eventually(lambda: c.state == "permission_required")
        d.drain()
        if recover_with == "typed_response":
            c.response_started("executing")
            assert c.state == "executing" and c._agent_busy
            c.response_completed()
            assert c.state == "ready" and not c._agent_busy
        assert c.speak("A response remains audible after microphone refusal.")
        assert speaker.started.wait(1)
        assert c.state == "speaking"
        c.stop_speaking()
        assert speaker.finished.wait(1)
        assert c.shutdown(2)
        d.drain()
        assert recorder.calls == 1
        assert errors == ["Mikrofon izni gerekli."] and not transcripts
    finally:
        c.shutdown(2)


def test_new_idle_session_can_start_conversation_while_previous_session_agent_is_busy(runtime):
    recorder = UtteranceRecorder()
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=recorder)
    try:
        c.bind_session("busy-session")
        c.response_started()
        assert c._agent_busy
        c.bind_session("idle-session")
        assert not c._agent_busy
        assert c.start_conversation()
        assert recorder.started.wait(1)
        assert c.session_id == "idle-session" and c.conversation_active
        assert not transcripts and not requests and not errors
    finally:
        c.shutdown(2)


def test_session_status_sync_preserves_pending_dictation_and_never_starts_audio(runtime):
    recorder = Recorder(wait=False)
    c, d, states, transcripts, errors, _, requests = controller(runtime, recorder=recorder)
    try:
        c.sync_agent_state(True)
        assert c.state == "thinking" and c._agent_busy
        c.sync_agent_state(False)
        assert c.state == "idle" and not c._agent_busy
        assert not recorder.calls
        assert c.start_recording()
        eventually(lambda: requests and c.state == "idle")
        c.sync_agent_state(True)
        c.sync_agent_state(False)
        d.drain()
        assert transcripts == ["Merhaba dünya"]
        assert c.transcript_resolved(False)
        assert len(recorder.calls) == 1 and not errors
    finally:
        c.shutdown(2)


@pytest.mark.parametrize("stage", ["capture", "speech"])
def test_worker_start_failure_releases_ownership_and_allows_new_work(runtime, monkeypatch, stage):
    recorder, speaker = Recorder(), Speaker()
    c, d, _, transcripts, errors, _, requests = controller(runtime, recorder=recorder, speaker=speaker)
    action = c.start_recording if stage == "capture" else lambda: c.speak("Response")
    try:
        with monkeypatch.context() as patch:
            def rejected_start(thread):
                raise RuntimeError("Native thread limit")
            patch.setattr(threading.Thread, "start", rejected_start)
            assert action() is False
        assert c.state_id.value == "error"
        assert not c._workers
        assert c.coordinator._owner is None
        assert c._speech_cancel is None
        d.drain()
        assert len(errors) == 1 and not transcripts and not requests
        assert action()
        assert (recorder.started if stage == "capture" else speaker.started).wait(1)
    finally:
        c.shutdown(2)


def test_generation_is_public_and_illegal_transition_is_rejected(runtime):
    c, d, _, _, _, *_ = controller(runtime)
    try:
        generation = c.generation
        assert not c._set_state(generation, "transcribing")
        assert c.state == "idle"
        c.cancel()
        assert c.generation > generation
        assert not c._set_state(generation, "listening")
    finally:
        c.close()


def test_duplicate_transcription_callback_cannot_duplicate_delivery(runtime):
    c, d, _, transcripts, _, _, requests = controller(runtime, recorder=Recorder(wait=False))
    try:
        c.start_recording()
        eventually(lambda: requests and c.state == "idle")
        c._transcription_done(c.generation, "A duplicate callback")
        d.drain()
        assert transcripts == ["Merhaba dünya"]
    finally:
        c.close()


def test_barge_in_silent_window_renewal_preserves_long_playback(runtime):
    class EmptyFirstWindow(UtteranceRecorder):
        def __init__(self):
            super().__init__()
            self.windows = 0
            self.silent_window_finished = threading.Event()
            self.renewed = threading.Event()

        def record_utterance(self, stop, cancel, max_seconds, **options):
            self.windows += 1
            if self.windows == 1:
                self.silent_window_finished.set()
                return b""  # Local silence window expired, not a user interruption.
            self.renewed.set()
            eventually(cancel.is_set)
            return b""

    recorder, speaker = EmptyFirstWindow(), Speaker()
    c, d, _, _, errors, _, requests = controller(runtime, recorder=recorder, speaker=speaker,
        settings=VoiceSettings(enabled=True, barge_in=True))
    try:
        c._conversation = True
        assert c.speak("A long answer that outlasts one local silent VAD window")
        assert speaker.started.wait(1) and recorder.silent_window_finished.wait(1)
        eventually(lambda: c.state == "ready")
        d.drain()  # The GUI starts the next bounded monitoring window.
        assert recorder.renewed.wait(1)
        assert not speaker.calls[0][1].is_set()
        assert not speaker.finished.is_set()
        assert not requests and not errors
        c.stop_speaking()
        assert speaker.finished.wait(1)
    finally:
        c.close()


def test_executing_event_suspends_capture_and_sets_actual_agent_phase(runtime):
    recorder = UtteranceRecorder()
    c, d, _, _, errors, _, requests = controller(runtime, recorder=recorder)
    try:
        assert c.start_conversation()
        assert recorder.started.wait(1)
        c.mark_agent_state("executing")
        assert c.state == "executing"
        assert recorder.finished.wait(1) and recorder.calls[0][1].is_set()
        assert c.conversation_active and not c.paused
        assert not requests and not errors
        c.response_completed()
        eventually(lambda: len(recorder.calls) == 2)
    finally:
        c.close()


@pytest.mark.parametrize("initial", ["idle", "ready"])
def test_late_created_controller_accepts_existing_executing_phase(runtime, initial):
    c, d, _, _, _, *_ = controller(runtime)
    try:
        if initial == "ready":
            assert c._set_state(c.generation, "ready")
        c.mark_agent_state("executing")
        assert c.state == "executing"
    finally:
        c.close()


def test_playback_failure_preserves_visible_simultaneous_barge_in_microphone(runtime):
    class FailingSpeaker:
        supports_barge_in = True
        def speak(self, text, cancel, language, rate):
            assert recorder.started.wait(1)
            raise RuntimeError("device removed")

    recorder = UtteranceRecorder()
    c, d, _, _, errors, _, requests = controller(runtime, recorder=recorder,
        speaker=FailingSpeaker(), settings=VoiceSettings(enabled=True, barge_in=True))
    try:
        c._conversation = True
        # Delay VAD onset so the playback failure owns this interruption.
        recorder.record_utterance = lambda stop, cancel, duration, **options: recorder.record(stop, cancel, duration)
        assert c.speak("Response")
        eventually(lambda: (d.drain() or bool(errors)))
        assert c.state == "listening"
        assert not c.paused and not recorder.calls[0][1].is_set()
        assert len(errors) == 1 and not requests
    finally:
        c.close()


def test_binding_another_session_discards_previous_private_speech(runtime):
    speaker = Speaker()
    instance, dispatch, *_ = controller(runtime, speaker=speaker)
    try:
        assert instance.speak('Private answer in the previous session')
        assert speaker.started.wait(1)
        assert instance.can_replay_speech
        instance.bind_session('another-existing-session')
        assert not instance.can_replay_speech
        assert not instance.replay_speech()
        assert len(speaker.calls) == 1
    finally:
        instance.shutdown(2)
