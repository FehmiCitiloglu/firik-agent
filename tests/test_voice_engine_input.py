"""Input engine contracts with synthetic PCM and injected PortAudio adapters."""
from array import array
import io
import math
import threading
import time
import wave
from types import SimpleNamespace

import pytest

from firik_agent.voice.audio_capture import PCMResampler, SoundDeviceRecorder
from firik_agent.voice.audio_devices import AudioDeviceCatalog
from firik_agent.voice.errors import (VoiceAudioError, VoiceDeviceError, VoiceFormatError,
                                      VoiceOverflowError, VoicePermissionError)
from firik_agent.voice.models import AudioFormat, PCMBuffer
from firik_agent.voice.vad import VADConfig, UtteranceSegmenter


def pcm(count, amplitude=1000):
    return array('h', (amplitude if i % 2 else -amplitude for i in range(count))).tobytes()


class CallbackBackend:
    def __init__(self, *, blocks=4, status=False, disconnected=False, cancel=None, permission=False):
        self.blocks, self.status, self.disconnected = blocks, status, disconnected
        self.cancel, self.permission = cancel, permission
        self.active = True
        self.closed = self.aborted = False
        self.open_count = self.start_count = 0

    def query_devices(self, *args):
        return {'default_samplerate': 16000, 'max_input_channels': 1}

    def check_input_settings(self, **kwargs):
        pass

    def RawInputStream(self, **options):
        self.open_count += 1
        if self.permission:
            raise OSError('private host: permission denied')
        self.options = options
        return self

    def start(self):
        self.start_count += 1
        for _ in range(self.blocks):
            count = self.options['blocksize']
            self.options['callback'](pcm(count), count, None, self.status)
        if self.disconnected:
            self.active = False
            self.options['finished_callback']()
        if self.cancel:
            self.cancel.set()

    def abort(self):
        self.aborted = True
        self.active = False

    def close(self):
        self.closed = True


def record(backend, **options):
    return SoundDeviceRecorder(backend, **options).record(threading.Event(), threading.Event(), .2)


def test_native_callback_queue_captures_and_releases_handles():
    backend = CallbackBackend()
    result = PCMBuffer.from_wav(record(backend))
    assert result.frames == 3200
    assert result.format == AudioFormat(16000)
    assert backend.aborted and backend.closed
    assert backend.options['callback'] is not None


@pytest.mark.parametrize('backend', [CallbackBackend(blocks=100), CallbackBackend(status=True)])
def test_native_overflow_fails_closed_and_releases_handles(backend):
    with pytest.raises(VoiceOverflowError):
        record(backend, queue_blocks=2)
    assert backend.aborted and backend.closed


def test_native_disconnection_is_actionable_and_releases_handles():
    backend = CallbackBackend(blocks=0, disconnected=True)
    with pytest.raises(VoiceDeviceError, match='bağlantısı kesildi'):
        record(backend)
    assert backend.aborted and backend.closed


def test_native_disconnection_discards_queued_data_even_at_frame_limit():
    backend = CallbackBackend(disconnected=True)
    with pytest.raises(VoiceDeviceError, match='bağlantısı kesildi'):
        record(backend)
    assert backend.aborted and backend.closed


def test_push_to_talk_release_before_worker_runs_never_probes_or_opens_audio(monkeypatch):
    stop = threading.Event()
    stop.set()
    recorder = SoundDeviceRecorder()
    monkeypatch.setattr(recorder, '_load_backend', lambda: pytest.fail('Unexpected audio backend access'))
    assert recorder.record(stop, threading.Event(), 1) == b''


@pytest.mark.parametrize('boundary', ['settings', 'open'])
@pytest.mark.parametrize('signal', ['stop', 'cancel'])
def test_interruption_during_native_setup_never_starts_stream(boundary, signal):
    stop, cancel = threading.Event(), threading.Event()
    interrupt = stop if signal == 'stop' else cancel

    class InterruptedBackend(CallbackBackend):
        def check_input_settings(self, **kwargs):
            if boundary == 'settings':
                interrupt.set()

        def RawInputStream(self, **kwargs):
            stream = super().RawInputStream(**kwargs)
            interrupt.set()
            return stream

    backend = InterruptedBackend()
    recorder = SoundDeviceRecorder(backend)
    if signal == 'stop':
        with pytest.raises(VoiceAudioError, match='çok kısa'):
            recorder.record(stop, cancel, 1)
    else:
        assert recorder.record(stop, cancel, 1) == b''
    assert not backend.start_count
    assert backend.open_count == (1 if boundary == 'open' else 0)
    assert backend.closed is (boundary == 'open')


def test_push_to_talk_release_drains_only_audio_accepted_before_release():
    stop = threading.Event()

    class ReleaseAfterCallbacks(CallbackBackend):
        def start(self):
            super().start()
            stop.set()
            count = self.options['blocksize']
            self.options['callback'](pcm(count, 2000), count, None, False)

        def abort(self):
            super().abort()
            self.options['finished_callback']()

    backend = ReleaseAfterCallbacks()
    result = PCMBuffer.from_wav(SoundDeviceRecorder(backend).record(stop, threading.Event(), 1))
    assert result.frames == 3200
    assert set(array('h', result.data)) == {-1000, 1000}
    assert backend.aborted and backend.closed


def test_missing_default_input_device_reports_safe_actionable_error():
    class MissingDefault(CallbackBackend):
        def query_devices(self, *args):
            raise RuntimeError('Error querying device -1 (private host)')

    backend = MissingDefault()
    with pytest.raises(VoiceDeviceError, match='Mikrofon bulunamadı') as error:
        record(backend)
    assert 'private' not in str(error.value)
    assert backend.open_count == 0


def test_cancellation_without_callbacks_still_closes_stream_promptly():
    cancel = threading.Event()
    backend = CallbackBackend(blocks=0)
    timer = threading.Timer(.05, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        assert SoundDeviceRecorder(backend).record(threading.Event(), cancel, 120) == b''
        assert time.monotonic()-started < .5
        assert backend.aborted and backend.closed
    finally:
        timer.join()


def test_permission_errors_do_not_disclose_native_details():
    with pytest.raises(VoicePermissionError) as error:
        record(CallbackBackend(permission=True))
    assert 'private' not in str(error.value)


def test_gain_changes_only_samples_and_levels_are_bounded():
    levels = []
    result = PCMBuffer.from_wav(record(CallbackBackend(), input_gain=2, on_level=levels.append))
    assert set(array('h', result.data)) == {-2000, 2000}
    assert levels and all(0 <= level <= 1 for level in levels)
    assert result.duration == .2


@pytest.mark.parametrize('source,target,count', [(44100,16000,44101), (48000,16000,48001),
                                                 (8000,16000,8001), (16000,48000,16001)])
def test_resampling_has_no_chunk_rounding_drift(source, target, count):
    values = array('h', (int(10000*math.sin(2*math.pi*500*i/source)) for i in range(count)))
    data = values.tobytes()
    whole = PCMResampler(source, target).feed(data, final=True)
    resampler = PCMResampler(source, target)
    chunks = []
    offset = 0
    for size in (1, 7, 441, 2048, 83):
        while offset < min(len(data), size*90):
            end = min(offset+size*2, len(data))
            chunks.append(resampler.feed(data[offset:end]))
            offset = end
            assert len(resampler._samples) <= 2
    chunks.append(resampler.feed(data[offset:], final=True))
    result = b''.join(chunks)
    assert result == whole
    assert len(result)//2 == round(count*target/source)
    assert abs(len(result)/2/target-count/source) <= .5/target


@pytest.mark.parametrize('source', [16000, 44100])
def test_resampler_rejects_partial_samples_and_reuse_after_final(source):
    converter = PCMResampler(source, 16000)
    with pytest.raises(VoiceFormatError):
        converter.feed(b'x')
    converter.feed(b'',final=True)
    with pytest.raises(VoiceFormatError):
        converter.feed(pcm(100))


class CatalogBackend:
    default = SimpleNamespace(device=(0,1))
    def __init__(self, names=('USB Mic', 'Speakers')):
        self.devices = [dict(name=name, hostapi=0, default_samplerate=48000,
                             max_input_channels=1, max_output_channels=2) for name in names]
    def query_devices(self):
        return self.devices
    def query_hostapis(self):
        return [{'name':'CoreAudio'}]


def test_saved_device_id_survives_portaudio_index_reordering():
    backend = CatalogBackend()
    catalog = AudioDeviceCatalog(backend)
    selected = catalog.list_devices()[0]
    backend.devices.reverse()
    assert catalog.resolve(selected.id) == 1
    assert catalog.resolve(None) is None
    assert catalog.resolve('USB Mic') == 1
    assert catalog.list_devices('output')[0].channels == 2


def test_duplicate_or_removed_saved_device_is_never_silently_replaced():
    backend = CatalogBackend(('USB Mic', 'USB Mic'))
    catalog = AudioDeviceCatalog(backend)
    selected = catalog.list_devices()[0]
    with pytest.raises(VoiceDeviceError):
        catalog.resolve(selected.id)
    backend.devices.clear()
    with pytest.raises(VoiceDeviceError):
        catalog.resolve(selected.id)


@pytest.mark.parametrize('fmt', [AudioFormat(16000),AudioFormat(48000,2),AudioFormat(44100,2,3)])
def test_pcm_container_roundtrip_validates_duration_and_frame_alignment(fmt):
    original = PCMBuffer(bytes(fmt.frame_bytes*100),fmt)
    decoded = PCMBuffer.from_wav(original.to_wav())
    assert decoded == original
    assert decoded.duration == 100/fmt.sample_rate
    with pytest.raises(VoiceFormatError):
        PCMBuffer.from_wav(original.to_wav(),max_seconds=1/100000)
    with pytest.raises(VoiceFormatError):
        PCMBuffer.from_wav(original.to_wav()[:-1])


@pytest.mark.parametrize('args', [(0,), (44100,0), (44100,1,5), (True,), (44100,True)])
def test_invalid_audio_format_fails_before_native_access(args):
    with pytest.raises(VoiceFormatError):
        AudioFormat(*args)


@pytest.mark.parametrize('language,cadence', [('tr',(15,8,20)),('en',(10,12,25))])
def test_language_independent_synthetic_sentence_pauses_are_debounced(language,cadence):
    # Acoustic fixtures model TR/EN cadence only, not recognition accuracy.
    segmenter = UtteranceSegmenter(16000,16000*5,VADConfig(silence_ms=700))
    first,pause,last = cadence
    frames = [bytes(640)]*10 + [pcm(320)]*first + [bytes(640)]*pause + [pcm(320)]*last
    for frame in frames:
        assert not segmenter.feed(frame)
    assert segmenter.started
    for _ in range(35):
        finished = segmenter.feed(bytes(640))
    assert finished and segmenter.pcm


def test_optional_webrtc_missing_is_actionable_and_does_not_load_ml(monkeypatch):
    def missing(name):
        assert name == 'webrtcvad'
        raise ImportError(name)
    monkeypatch.setattr('firik_agent.voice.vad.importlib.import_module',missing)
    with pytest.raises(VoiceAudioError,match='voice-vad'):
        UtteranceSegmenter(16000,16000,VADConfig(engine='webrtc'))


def test_resampler_preserves_speech_band_and_suppresses_aliasing():
    from firik_agent.voice.vad import pcm_rms
    for frequency, low, high in ((500,.98,1.02),(10000,0,.02)):
        values = array('h',(int(10000*math.sin(2*math.pi*frequency*i/44100)) for i in range(44100)))
        output = PCMResampler(44100,16000).feed(values.tobytes(),final=True)
        ratio = pcm_rms(output[400:])/pcm_rms(values.tobytes()[400:])
        assert low <= ratio <= high


@pytest.mark.parametrize('frames',[0,-1,True,16000*121])
def test_standalone_vad_rejects_unbounded_or_invalid_windows(frames):
    with pytest.raises(VoiceAudioError):
        UtteranceSegmenter(16000,frames)
