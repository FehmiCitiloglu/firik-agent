"""Audio engine boundaries and application-owned lifecycle regressions.

These tests use PCM and controllable workers, never a microphone or network.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import io
import queue
import subprocess
import sys
import threading
import time
import wave

import pytest

from firik_agent.core.runtime import AsyncRuntime
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.controller import CaptureCoordinator, VoiceController
from firik_agent.voice.transcription import TranscriptionAdapter, TranscriptionPolicy


def wav_audio(seconds=1, *, rate=16000, channels=1, width=2):
    # A repeatable nonzero waveform lets split/reassembly detect dropped samples.
    frames = round(seconds * rate)
    pcm = (b"\x01\x00\xff\xff" * ((frames * channels * width + 3) // 4))[:frames * channels * width]
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setframerate(rate)
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.writeframes(pcm)
    return output.getvalue()


def pcm_audio(data):
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.getframerate(), wav.getnframes(), wav.readframes(wav.getnframes())


def eventually(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.005)
    assert predicate()


@pytest.mark.asyncio
async def test_transcription_splits_long_pcm_sequentially_without_sample_loss():
    calls = []
    active = 0

    async def request(audio, model, language):
        nonlocal active
        active += 1
        assert active == 1
        calls.append((audio, model, language))
        await asyncio.sleep(0)
        active -= 1
        return f"  segment {len(calls)}  "

    source = wav_audio(65)
    adapter = TranscriptionAdapter(request)
    assert await adapter.transcribe(source, "asr", "tr") == "segment 1 segment 2 segment 3"
    durations = [pcm_audio(call[0])[1] / 16000 for call in calls]
    assert len(durations) == 3 and all(0 < duration <= 30 for duration in durations)
    assert sum(durations) == pytest.approx(65)
    assert all(call[1:] == ("asr", "tr") for call in calls)
    assert b"".join(pcm_audio(call[0])[2] for call in calls) == pcm_audio(source)[2]


@pytest.mark.asyncio
@pytest.mark.parametrize("audio", [b"", b"RIFF-not-a-wav", wav_audio(channels=2), wav_audio(width=1), wav_audio(rate=96000)])
async def test_transcription_rejects_invalid_input_before_any_remote_request(audio):
    calls = []

    async def request(*args):
        calls.append(args)
        return "must not run"

    with pytest.raises(ValueError):
        await TranscriptionAdapter(request).transcribe(audio, "asr", "en")
    assert not calls


@pytest.mark.asyncio
async def test_transcription_checks_total_and_segment_budgets_before_upload():
    calls = []

    async def request(*args):
        calls.append(args)
        return "must not run"

    policy = TranscriptionPolicy(max_request_seconds=30, max_total_seconds=120, max_segments=2)
    adapter = TranscriptionAdapter(request, policy=policy)
    with pytest.raises(ValueError):
        await adapter.transcribe(wav_audio(65), "asr", "en")
    with pytest.raises(ValueError):
        await adapter.transcribe(wav_audio(121), "asr", "en")
    assert not calls


@pytest.mark.asyncio
async def test_transcription_cancellation_releases_inflight_and_drops_later_segments():
    calls = []
    entered, closed = asyncio.Event(), asyncio.Event()

    async def request(audio, model, language):
        calls.append(audio)
        if len(calls) == 2:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        return "first"

    task = asyncio.create_task(TranscriptionAdapter(request).transcribe(wav_audio(65), "asr", "auto"))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and len(calls) == 2


@pytest.mark.asyncio
async def test_transcription_rejects_no_speech_instead_of_returning_partial_text():
    calls = []

    async def request(*args):
        calls.append(args)
        return "first" if len(calls) == 1 else "  "

    with pytest.raises(ValueError):
        await TranscriptionAdapter(request).transcribe(wav_audio(65), "asr", "tr")
    assert len(calls) == 2


class Dispatch:
    def __init__(self):
        self.jobs = queue.Queue()

    def __call__(self, callback, *args):
        self.jobs.put((callback, args))

    def drain(self):
        while not self.jobs.empty():
            callback, args = self.jobs.get_nowait()
            callback(*args)


class SlowClosingSpeaker:
    supports_barge_in = False

    def __init__(self):
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.release = threading.Event()

    def speak(self, text, cancel, language, rate):
        self.started.set()
        assert cancel.wait(2)
        self.cancelled.set()
        assert self.release.wait(2)


class RecordingWorker:
    supports_barge_in = False

    def __init__(self):
        self.started = threading.Event()
        self.finished = threading.Event()

    def record(self, stop, cancel, max_seconds, device=None):
        self.started.set()
        assert cancel.wait(2)
        self.finished.set()
        return b""


@pytest.fixture
def runtime():
    value = AsyncRuntime()
    yield value
    value.shutdown()


def controller(runtime, *, recorder=None, speaker=None, coordinator=None, on_level=None):
    dispatch = Dispatch()
    received = []

    async def transcribe(*args):
        pytest.fail("cancelled input must not upload audio")

    kwargs = {} if on_level is None else {"on_level": on_level}
    instance = VoiceController(runtime, dispatch, transcribe, VoiceSettings(enabled=True),
                               received.append, received.append, received.append,
                               recorder=recorder, speaker=speaker, coordinator=coordinator, **kwargs)
    return instance, dispatch, received


def test_manual_ptt_waits_for_cancelled_playback_handle_to_close(runtime):
    speaker, recorder = SlowClosingSpeaker(), RecordingWorker()
    c, dispatch, received = controller(runtime, speaker=speaker, recorder=recorder)
    try:
        assert c.speak("A response")
        assert speaker.started.wait(1)
        assert c.start_recording()
        assert speaker.cancelled.wait(1)
        assert not recorder.started.wait(.05)
        speaker.release.set()
        assert recorder.started.wait(1)
        assert c.shutdown(timeout=2)
        assert recorder.finished.is_set()
        dispatch.drain()
        assert not received
    finally:
        speaker.release.set()
        c.shutdown(timeout=2)


def test_half_duplex_applies_across_different_conversation_controllers(runtime):
    coordinator = CaptureCoordinator()
    speaker, recorder = SlowClosingSpeaker(), RecordingWorker()
    output, _, _ = controller(runtime, speaker=speaker, coordinator=coordinator)
    capture, _, _ = controller(runtime, recorder=recorder, coordinator=coordinator)
    try:
        assert output.speak("Read one conversation")
        assert speaker.started.wait(1)
        assert capture.start_recording()
        assert not recorder.started.wait(.05)
        output.stop_speaking()
        assert speaker.cancelled.wait(1)
        assert not recorder.started.wait(.05)
        speaker.release.set()
        assert recorder.started.wait(1)
    finally:
        speaker.release.set()
        output.shutdown(timeout=2)
        capture.shutdown(timeout=2)


def test_shutdown_timeout_is_bounded_and_can_be_joined_again(runtime):
    speaker = SlowClosingSpeaker()
    c, dispatch, received = controller(runtime, speaker=speaker)
    try:
        assert c.speak("Still closing")
        assert speaker.started.wait(1)
        start = time.monotonic()
        assert c.shutdown(timeout=.03) is False
        assert time.monotonic() - start < .5
        assert speaker.cancelled.wait(1)
        speaker.release.set()
        assert c.shutdown(timeout=2) is True
        dispatch.drain()
        assert not received
        assert not c.speak("Closed") and not c.start_recording()
    finally:
        speaker.release.set()
        c.shutdown(timeout=2)


def test_level_notifications_coalesce_and_stale_capture_does_not_reach_ui(runtime):
    levels = []

    class LevelRecorder(RecordingWorker):
        def configure(self, **options):
            self.on_level = options["on_level"]

        def record(self, *args, **kwargs):
            for index in range(1000):
                self.on_level(index / 1000)
            return super().record(*args, **kwargs)

    recorder = LevelRecorder()
    c, dispatch, _ = controller(runtime, recorder=recorder, on_level=levels.append)
    try:
        assert c.start_recording()
        assert recorder.started.wait(1)
        # A slow Tk thread must not accumulate one job per microphone block.
        assert dispatch.jobs.qsize() < 10
        assert not levels
        dispatch.drain()
        assert levels and levels[-1] == pytest.approx(.999)
        assert c.shutdown(timeout=2)
        dispatch.drain()
    finally:
        c.shutdown(timeout=2)


def test_a_message_arriving_as_playback_finishes_is_not_left_in_a_dead_queue(runtime):
    class FinishingCoordinator(CaptureCoordinator):
        def __init__(self):
            super().__init__()
            self.finishing = threading.Event()
            self.close_release = threading.Event()
            self.count = 0

        def end_speech(self):
            self.count += 1
            if self.count == 1:
                self.finishing.set()
                assert self.close_release.wait(2)
            return super().end_speech()

    class Speaker:
        def __init__(self):
            self.messages = []

        def speak(self, text, cancel, language, rate):
            self.messages.append(text)

    coordinator, speaker = FinishingCoordinator(), Speaker()
    c, _, _ = controller(runtime, speaker=speaker, coordinator=coordinator)
    try:
        assert c.speak("First message")
        assert coordinator.finishing.wait(1)
        assert c.speak("Next message")
        coordinator.close_release.set()
        eventually(lambda: speaker.messages == ["First message", "Next message"])
    finally:
        coordinator.close_release.set()
        c.shutdown(timeout=2)


def test_interruption_discards_unplayed_messages_and_output_backlog_is_bounded(runtime):
    class Speaker:
        def __init__(self):
            self.messages = []
            self.started = threading.Event()
            self.finished = threading.Event()

        def speak(self, text, cancel, language, rate):
            self.messages.append(text)
            self.started.set()
            assert cancel.wait(2)
            self.finished.set()

    speaker = Speaker()
    c, _, _ = controller(runtime, speaker=speaker)
    try:
        assert c.speak("Currently audible")
        assert speaker.started.wait(1)
        accepted = [c.speak(f"Queued {index}") for index in range(100)]
        assert any(accepted) and not all(accepted)
        assert speaker.messages == ["Currently audible"]
        c.stop_speaking()
        assert speaker.finished.wait(1)
        assert c.shutdown(timeout=2)
        assert speaker.messages == ["Currently audible"]
    finally:
        c.shutdown(timeout=2)


def test_changed_output_device_and_volume_apply_to_the_next_message(runtime):
    class Speaker:
        def __init__(self):
            self.configurations = []
            self.messages = []

        def configure(self, **settings):
            self.configurations.append(settings)

        def speak(self, text, cancel, language, rate):
            self.messages.append(text)

    speaker = Speaker()
    c, _, _ = controller(runtime, speaker=speaker)
    try:
        assert c.speak("First")
        eventually(lambda: c.state == "idle" and len(speaker.messages) == 1)
        c.settings = replace(c.settings, output_device="portaudio:USB Headphones", output_volume=.25)
        c.cancel()  # The service invalidates voice work after persisting settings.
        assert c.speak("Second")
        eventually(lambda: c.state == "idle" and len(speaker.messages) == 2)
        assert speaker.configurations == [
            {"output_device": None, "volume": 1.0},
            {"output_device": "portaudio:USB Headphones", "volume": .25},
        ]
    finally:
        c.shutdown(timeout=2)


def test_desktop_and_voice_modules_import_without_any_optional_audio_or_ml_packages():
    script = """
import importlib.abc
import sys

blocked = {'sounddevice', 'soundfile', 'webrtcvad', 'numpy', 'torch', 'onnxruntime'}
class MissingAudio(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in blocked:
            raise ImportError('Optional audio intentionally absent')
sys.meta_path.insert(0, MissingAudio())
import firik_agent.ui.app
import firik_agent.ui.service
import firik_agent.voice.audio_capture
import firik_agent.voice.audio_devices
import firik_agent.voice.vad
import firik_agent.voice.playback
import firik_agent.voice.synthesis
assert not blocked.intersection(sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_failed_streamed_playback_cancels_later_audio_until_a_new_response(runtime):
    class BrokenSpeaker:
        def __init__(self):
            self.attempts = []
            self.entered = threading.Event()

        def speak(self, text, cancel, language, rate):
            self.attempts.append(text)
            self.entered.set()
            raise RuntimeError("Native output unavailable")

    speaker = BrokenSpeaker()
    c, dispatch, received = controller(runtime, speaker=speaker)
    c.settings = replace(c.settings, read_replies=True, stream_replies=True)
    try:
        c.response_started()
        c.response_delta("A first complete sentence. ")
        assert speaker.entered.wait(1)
        eventually(lambda: c.state_id.value == "error")
        speaker.entered.clear()
        c.response_delta("A later complete sentence. ")
        assert not speaker.entered.wait(.1)
        c.response_completed("A first complete sentence. A later complete sentence.")
        assert c.shutdown(timeout=2)
        dispatch.drain()
        assert len(speaker.attempts) == 1
    finally:
        c.shutdown(timeout=2)


def test_failed_transcription_requires_new_capture_for_retry(runtime):
    class Recorder:
        def __init__(self):
            self.calls = 0

        def record(self, stop, cancel, max_seconds, device=None):
            self.calls += 1
            return wav_audio(.2)

    requests = []

    async def transcribe(audio, model, language):
        requests.append(audio)
        if len(requests) == 1:
            raise RuntimeError("Transient provider failure")
        return "A retried transcript"

    recorder, dispatch, errors, transcripts = Recorder(), Dispatch(), [], []
    c = VoiceController(runtime, dispatch, transcribe, VoiceSettings(enabled=True),
                        lambda state: None, transcripts.append, errors.append, recorder=recorder)
    try:
        assert c.start_recording()
        eventually(lambda: c.state_id.value == "error")
        dispatch.drain()
        assert len(errors) == 1 and not transcripts and len(requests) == recorder.calls == 1
        assert c.start_recording()
        eventually(lambda: c.state_id.value == "awaiting_confirmation")
        dispatch.drain()
        assert transcripts == ["A retried transcript"]
        assert len(requests) == 2 and requests[0] == requests[1]
        assert recorder.calls == 2
    finally:
        c.shutdown(timeout=2)


@pytest.mark.parametrize("active_kind", ["capture", "speech"])
@pytest.mark.parametrize("active_duplex,entering_duplex", [(False, True), (True, False), (True, True)])
def test_audio_overlap_requires_both_active_participants_to_support_duplex(
        active_kind, active_duplex, entering_duplex):
    coordinator = CaptureCoordinator()
    entering_kind = "speech" if active_kind == "capture" else "capture"
    begin_active = getattr(coordinator, "begin_" + active_kind)
    end_active = getattr(coordinator, "end_" + active_kind)
    begin_entering = getattr(coordinator, "begin_" + entering_kind)
    end_entering = getattr(coordinator, "end_" + entering_kind)
    cancel, attempted, returned = threading.Event(), threading.Event(), threading.Event()
    results = []
    assert begin_active(threading.Event(), duplex=active_duplex)

    def enter():
        attempted.set()
        results.append(begin_entering(cancel, duplex=entering_duplex))
        returned.set()

    worker = threading.Thread(target=enter, daemon=True)
    worker.start()
    try:
        assert attempted.wait(1)
        if active_duplex and entering_duplex:
            assert returned.wait(1)
        else:
            assert not returned.wait(.05)
        end_active()
        assert returned.wait(1)
        assert results == [True]
    finally:
        cancel.set()
        end_active()
        worker.join(1)
        end_entering()
        assert not worker.is_alive()


@pytest.mark.parametrize('mapping', [
    {'input_gain': float('nan')}, {'input_gain': 0}, {'input_gain': 5},
    {'output_volume': float('inf')}, {'output_volume': -1}, {'output_volume': True},
    {'sample_rate': 16000.0}, {'sample_rate': 44100}, {'asr_request_seconds': 31},
    {'asr_request_seconds': 0}, {'vad_pre_roll_ms': 1001}, {'stream_replies': 'true'},
])
def test_invalid_engine_preferences_cannot_enable_unsafe_audio(mapping):
    with pytest.raises(ValueError):
        VoiceSettings.from_mapping(mapping)
