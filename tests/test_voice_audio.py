"""Microphone capture contract, independent of real hardware and permissions."""
from array import array
import io
import threading
import wave

import pytest

from firik_agent.voice.audio import SoundDeviceRecorder, VoiceAudioError


class FakeAudio:
    def __init__(self, *, signal=1000, overflow=False, stop=None, cancel=None,
                 stop_after=None, failure=None, short=False):
        self.signal = signal
        self.overflow = overflow
        self.stop = stop
        self.cancel = cancel
        self.stop_after = stop_after
        self.failure = failure
        self.short = short
        self.closed = False
        self.reads = []
        self.options = None

    def query_devices(self, device, kind):
        self.selected = device, kind
        return {"default_samplerate": 16000, "max_input_channels": 1}

    def check_input_settings(self, **options):
        self.checked = options

    def RawInputStream(self, **options):
        self.options = options
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, frames):
        self.reads.append(frames)
        if self.stop_after == len(self.reads):
            (self.cancel or self.stop).set()
        if self.failure:
            raise self.failure
        values = [self.signal if i % 2 else -self.signal for i in range(frames)]
        data = array("h", values).tobytes()
        return data[:-2] if self.short else data, self.overflow


def record(backend, duration=.25):
    return SoundDeviceRecorder(backend).record(threading.Event(), threading.Event(), duration)


def test_wav_is_mono_pcm_native_rate_and_frame_bounded():
    backend = FakeAudio()
    data = record(backend, .213)
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == int(16000 * .213)
        assert len(wav.readframes(wav.getnframes())) == wav.getnframes() * 2
    assert sum(backend.reads) == int(16000 * .213)
    assert max(backend.reads) <= 800
    assert backend.checked == {"device": None, "channels": 1, "dtype": "int16", "samplerate": 16000}
    assert backend.closed


def test_explicit_device_and_stop_finalize_without_waiting_for_budget():
    stop = threading.Event()
    backend = FakeAudio(stop=stop, stop_after=4)
    data = SoundDeviceRecorder(backend).record(stop, threading.Event(), 120, device="USB mikrofon")
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getnframes() == 3200
    assert backend.selected == ("USB mikrofon", "input")
    assert backend.options["device"] == "USB mikrofon"
    assert backend.closed


def test_cancel_discards_audio_and_closes_stream():
    cancel = threading.Event()
    backend = FakeAudio(cancel=cancel, stop_after=4)
    assert SoundDeviceRecorder(backend).record(threading.Event(), cancel, 120) == b""
    assert backend.closed
    assert len(backend.reads) == 4


def test_already_cancelled_does_not_import_or_open_backend(monkeypatch):
    monkeypatch.setattr("firik_agent.voice.audio.importlib.import_module", lambda name: pytest.fail("unexpected import"))
    cancel = threading.Event()
    cancel.set()
    assert SoundDeviceRecorder().record(threading.Event(), cancel, 60) == b""


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf"), 121, "60", True])
def test_invalid_limits_fail_before_opening(duration):
    backend = FakeAudio()
    with pytest.raises(VoiceAudioError, match="Kayıt süresi"):
        record(backend, duration)
    assert backend.options is None


@pytest.mark.parametrize("signal", [0, 1, 39])
def test_silence_is_rejected_without_asr(signal):
    backend = FakeAudio(signal=signal)
    with pytest.raises(VoiceAudioError, match="Konuşma duyulamadı"):
        record(backend)
    assert backend.closed


def test_short_click_is_rejected():
    backend = FakeAudio()
    with pytest.raises(VoiceAudioError, match="çok kısa"):
        record(backend, .05)
    assert backend.closed


def test_dc_offset_is_not_mistaken_for_speech():
    backend = FakeAudio()
    backend.read = lambda count: (array("h", [1500] * count).tobytes(), False)
    with pytest.raises(VoiceAudioError, match="Konuşma duyulamadı"):
        record(backend)
    assert backend.closed


def test_cancellation_during_device_failure_discards_without_error():
    cancel = threading.Event()
    backend = FakeAudio(cancel=cancel, stop_after=1, failure=OSError("device interrupted"))
    assert SoundDeviceRecorder(backend).record(threading.Event(), cancel, 10) == b""
    assert backend.closed


@pytest.mark.parametrize("options, message", [
    ({"overflow": True}, "verisi kesildi"),
    ({"short": True}, "eksik ses verisi"),
    ({"failure": OSError("private hardware exception")}, "Mikrofon açılamadı"),
])
def test_failures_close_stream_and_do_not_expose_backend_details(options, message):
    backend = FakeAudio(**options)
    with pytest.raises(VoiceAudioError, match=message) as error:
        record(backend)
    assert "private" not in str(error.value)
    assert backend.closed


@pytest.mark.parametrize('error', [ImportError('not installed'), OSError('PortAudio library not found')])
def test_missing_optional_dependency_has_actionable_error(monkeypatch, error):
    def missing(name):
        raise error
    monkeypatch.setattr("firik_agent.voice.audio.importlib.import_module", missing)
    with pytest.raises(VoiceAudioError, match=r"firik-agent\[voice\]"):
        SoundDeviceRecorder().record(threading.Event(), threading.Event(), 10)


@pytest.mark.parametrize("rate,channels", [(0, 1), (float("nan"), 1), (384000, 1), (48000, 0)])
def test_unsupported_devices_fail_without_opening(rate, channels):
    backend = FakeAudio()
    backend.query_devices = lambda *args: {"default_samplerate": rate, "max_input_channels": channels}
    with pytest.raises(VoiceAudioError, match="desteklenmiyor"):
        record(backend)
    assert backend.options is None


def test_very_high_rate_long_recordings_fail_before_allocating_or_opening():
    backend = FakeAudio()
    backend.query_devices = lambda *args: {"default_samplerate": 192000, "max_input_channels": 1}
    with pytest.raises(VoiceAudioError, match="kayıt sınırı 68 saniyedir"):
        record(backend, 120)
    assert backend.options is None


class SegmentedAudio(FakeAudio):
    def __init__(self, signals, **kwargs):
        super().__init__(**kwargs)
        self.signals = iter(signals)

    def read(self, frames):
        self.signal = next(self.signals, 0)
        return super().read(frames)


def utterance(backend, duration=2, **kwargs):
    return SoundDeviceRecorder(backend).record_utterance(
        threading.Event(), threading.Event(), duration, **kwargs)


def test_vad_idle_silence_and_short_clicks_never_produce_audio():
    for signals in ([0] * 100, [0] * 10 + [32767] * 2 + [0] * 88):
        backend = SegmentedAudio(signals)
        assert utterance(backend) == b""
        assert backend.closed and sum(backend.reads) == 32000


def test_vad_detects_sustained_speech_and_finalizes_at_silence():
    backend = SegmentedAudio([0] * 10 + [1000] * 20 + [0] * 100)
    onset = []
    data = utterance(backend, on_speech_start=lambda: onset.append(len(backend.reads)))
    assert onset == [20]  # 200ms sustained speech before onset callback.
    # 200ms leading silence, 400ms speech, then 700ms end silence.
    assert len(backend.reads) == 65 and backend.closed
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnframes() == 16000 * 1.3


def test_vad_endpoint_metrics_exclude_silence_windows_maximum_duration_and_cancellation(monkeypatch):
    from firik_agent.voice import audio_capture

    clock = [10.]
    records = []
    monkeypatch.setattr(audio_capture.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(audio_capture, 'log_voice_timing',
                        lambda *args, **kwargs: records.append((args, kwargs)))

    class ClockedAudio(SegmentedAudio):
        def read(self, count):
            clock[0] += count / 16000
            return super().read(count)

    assert utterance(ClockedAudio([0] * 10 + [1000] * 20 + [0] * 100))
    [(metric, status, started_at), timing] = records.pop()
    assert (metric, status) == ('vad_endpoint', 'ok')
    assert timing['finished_at'] - started_at == pytest.approx(.7)
    assert utterance(ClockedAudio([1000] * 50), duration=1)
    assert utterance(ClockedAudio([0] * 50), duration=1) == b''
    cancel = threading.Event()
    backend = ClockedAudio([1000] * 20 + [0] * 100, cancel=cancel, stop_after=54)
    assert SoundDeviceRecorder(backend).record_utterance(threading.Event(), cancel, 2) == b''
    assert not records


def test_vad_rejects_dc_offset_and_uses_selected_device():
    backend = FakeAudio()
    backend.read = lambda frames: (array("h", [2000] * frames).tobytes(), False)
    assert utterance(backend, device="USB") == b""
    assert backend.selected == ("USB", "input") and backend.closed


def test_vad_cancel_discards_inflight_speech_and_closes_device():
    cancel = threading.Event()
    backend = FakeAudio(cancel=cancel, stop_after=20)
    assert SoundDeviceRecorder(backend).record_utterance(threading.Event(), cancel, 2) == b""
    assert backend.closed


def test_vad_bounded_continuous_speech_finalizes_at_limit():
    backend = FakeAudio(signal=1000)
    data = utterance(backend, duration=1)
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getnframes() == 16000
    assert backend.closed


def test_vad_preserves_entire_configured_onset_for_slow_speakers():
    backend = SegmentedAudio([0] * 10 + [1000] * 60 + [0] * 100)
    data = utterance(backend, duration=3, min_speech_ms=1000)
    with wave.open(io.BytesIO(data)) as wav:
        assert wav.getnframes() == 16000 * 2.1


@pytest.mark.parametrize("options", [{"threshold": True}, {"threshold": 10}, {"silence_ms": 10}, {"min_speech_ms": 20}])
def test_invalid_vad_preferences_fail_without_opening(options):
    backend = FakeAudio()
    with pytest.raises(VoiceAudioError):
        utterance(backend, **options)
    assert backend.options is None
