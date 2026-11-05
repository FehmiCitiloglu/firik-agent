"""Bounded audio decoding and cancellable, per-stream PortAudio playback.

Decoding happens on the owning worker. The native callback only copies one
bounded PCM block; completion is reported by PortAudio after its buffers drain.
No operation changes the user's global system volume.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import importlib
import io
import math
import sys
import threading
import time

from firik_agent.voice.audio_devices import AudioDeviceCatalog, load_backend
from firik_agent.voice.errors import VoiceAudioError, VoiceFormatError, VoicePlaybackError
from firik_agent.voice.models import AudioFormat, MAX_AUDIO_BYTES, PCMBuffer

MAX_PLAYBACK_SECONDS = 120


@dataclass(frozen=True)
class EncodedAudio:
    data: bytes
    media_type: str | None = None
    # Headerless PCM is accepted only with an explicit, validated format.
    raw_format: AudioFormat | None = None


def _pcm16(audio: PCMBuffer) -> PCMBuffer:
    width = audio.format.sample_width
    if width == 2:
        return audio
    output = array("h")
    data = audio.data
    for offset in range(0, len(data), width):
        value = data[offset] - 128 if width == 1 else int.from_bytes(data[offset:offset + width], "little", signed=True)
        output.append(value << 8 if width == 1 else value >> (8 * (width - 2)))
    if sys.byteorder != "little":
        output.byteswap()
    return PCMBuffer(output.tobytes(), AudioFormat(audio.format.sample_rate, audio.format.channels, 2))


def _resample_pcm(audio: PCMBuffer, target_rate: int, cancel: threading.Event,
                  *, stop: threading.Event | None = None) -> PCMBuffer | None:
    """Resample channels independently with bounded scratch blocks and no drift."""
    from firik_agent.voice.audio_capture import PCMResampler
    fmt = AudioFormat(target_rate, audio.format.channels, 2)
    source_rate, channels = audio.format.sample_rate, audio.format.channels
    frames = (audio.frames * target_rate * 2 + source_rate) // (2 * source_rate)
    if not frames or frames * fmt.frame_bytes > MAX_AUDIO_BYTES:
        raise VoicePlaybackError("Çıkış aygıtına dönüştürülen ses boyut sınırını aşıyor.")
    converters = [PCMResampler(source_rate, target_rate) for _ in range(channels)]
    output = bytearray()
    block_bytes = 1024 * audio.format.frame_bytes
    for offset in range(0, len(audio.data), block_bytes):
        if cancel.is_set() or stop is not None and stop.is_set():
            return None
        source = array("h")
        source.frombytes(audio.data[offset:offset + block_bytes])
        if sys.byteorder != "little":
            source.byteswap()
        final = offset + block_bytes >= len(audio.data)
        converted = []
        for channel, converter in enumerate(converters):
            if cancel.is_set() or stop is not None and stop.is_set():
                return None
            mono = source[channel::channels]
            if sys.byteorder != "little":
                mono.byteswap()
            values = array("h")
            values.frombytes(converter.feed(mono.tobytes(), final=final))
            if sys.byteorder != "little":
                values.byteswap()
            converted.append(values)
        count = len(converted[0])
        if any(len(values) != count for values in converted):
            raise VoicePlaybackError("Ses kanallarının dönüştürülen uzunlukları uyuşmuyor.")
        interleaved = array("h", [0]) * (count * channels)
        for channel, values in enumerate(converted):
            interleaved[channel::channels] = values
        if sys.byteorder != "little":
            interleaved.byteswap()
        if len(output) + len(interleaved) * 2 > MAX_AUDIO_BYTES:
            raise VoicePlaybackError("Çıkış aygıtına dönüştürülen ses boyut sınırını aşıyor.")
        output.extend(interleaved.tobytes())
    if cancel.is_set() or stop is not None and stop.is_set():
        return None
    if len(output) != frames * fmt.frame_bytes:
        raise VoicePlaybackError("Ses örnekleme dönüşümü beklenen süreyi koruyamadı.")
    return PCMBuffer(bytes(output), fmt)


def decode_audio(audio: EncodedAudio | PCMBuffer | bytes, *, media_type=None,
                 raw_format=None, max_seconds=MAX_PLAYBACK_SECONDS) -> PCMBuffer:
    """Decode the actual container, ignoring inaccurate Content-Type headers.

    Integer WAV needs only the standard library. MP3/Ogg/FLAC and float WAV
    require the optional ``voice-codecs`` extra (libsndfile through soundfile).
    Declared frame counts are checked before allocating a decoded buffer.
    """
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise VoicePlaybackError("Geçersiz ses oynatma süre sınırı.")
    if isinstance(audio, PCMBuffer):
        result = _pcm16(audio)
        if not result.frames or result.duration > max_seconds:
            raise VoicePlaybackError("Sesli yanıt boş veya süre sınırını aşıyor.")
        return result
    if isinstance(audio, EncodedAudio):
        media_type, raw_format, data = audio.media_type, audio.raw_format, audio.data
    else:
        data = audio
    if not isinstance(data, bytes) or not data or len(data) > MAX_AUDIO_BYTES:
        raise VoicePlaybackError("Sesli yanıt boş veya boyut sınırını aşıyor.")
    if raw_format is not None:
        try:
            return decode_audio(PCMBuffer(data, raw_format), max_seconds=max_seconds)
        except VoiceFormatError as error:
            raise VoicePlaybackError("Ham PCM sesli yanıtı geçersiz.") from error
    is_wav = data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if is_wav:
        try:
            return _pcm16(PCMBuffer.from_wav(data, max_seconds=max_seconds))
        except VoiceFormatError as error:
            # PCM errors (including oversized/truncated containers) must not
            # become a decoder fallback that bypasses the original bounds.
            cause = error.__cause__
            if cause is None or "unknown format" not in str(cause).lower():
                raise VoicePlaybackError("WAV sesli yanıtı geçersiz veya sınırı aşıyor.") from error
    compressed = (data.startswith((b"fLaC", b"OggS", b"ID3")) or
                  len(data) > 1 and data[0] == 255 and data[1] & 224 == 224)
    if not is_wav and not compressed:
        raise VoicePlaybackError("Ses kabı tanınmadı. Ham PCM için açık bir ses biçimi gerekir.")
    try:
        soundfile = importlib.import_module("soundfile")
    except (ImportError, OSError) as error:
        raise VoicePlaybackError("Bu ses biçimi için 'pip install firik-agent[voice-codecs]' kurun.") from error
    try:
        with soundfile.SoundFile(io.BytesIO(data)) as source:
            fmt = AudioFormat(int(source.samplerate), int(source.channels), 2)
            frames = int(source.frames)
            if frames <= 0 or frames > fmt.sample_rate * max_seconds or frames * fmt.frame_bytes > MAX_AUDIO_BYTES:
                raise VoicePlaybackError("Çözümlenen sesli yanıt süre veya boyut sınırını aşıyor.")
            result = source.buffer_read(frames, dtype="int16")
            pcm = bytes(result)
            if len(pcm) != frames * fmt.frame_bytes:
                raise VoicePlaybackError("Sesli yanıtın kodlanmış verisi eksik.")
            if sys.byteorder != "little":
                values = array("h")
                values.frombytes(pcm)
                values.byteswap()
                pcm = values.tobytes()
            return PCMBuffer(pcm, fmt)
    except VoiceAudioError:
        raise
    except Exception as error:
        raise VoicePlaybackError("Sesli yanıtın kabı veya kodlaması çözümlenemedi.") from error


class SoundDevicePlayer:
    """One owned output stream; ``play``/``replay`` run on a worker.

    pause/resume/stop are nonblocking and can be called on Tk. A paused callback
    writes silence without advancing the audio cursor. Only the owning worker
    aborts/closes the stream, including cancellation and device failures.
    """
    supports_barge_in = False  # This stack has no acoustic echo cancellation.

    def __init__(self, *, device=None, volume=1.0, backend=None, cache_replay=True, clock=time.monotonic):
        self.device = device
        self._backend = backend
        self._clock = clock
        self._serial = threading.Lock()
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._last = None
        self._cache_replay = cache_replay
        self._closed = False
        self._playing = False
        self.volume = volume

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise VoicePlaybackError("Çıkış ses düzeyi 0 ile 1 arasında olmalıdır.")
        self._volume = float(value)

    @property
    def playing(self):
        with self._lock:
            return self._playing

    @property
    def paused(self):
        return self._paused.is_set()

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def stop(self):
        self._stop.set()
        self._paused.clear()

    def shutdown(self):
        with self._lock:
            self._closed = True
            self._last = None
        self.stop()

    def replay(self, cancel: threading.Event, *, on_complete=None):
        with self._lock:
            audio = self._last
        if audio is None:
            return False
        self.play(audio, cancel, on_complete=on_complete)
        return not cancel.is_set() and not self._stop.is_set() and not self._closed

    def play(self, audio, cancel: threading.Event, *, on_complete=None, on_first_audio=None):
        if cancel.is_set() or self._closed:
            return
        pcm = decode_audio(audio)
        while not self._serial.acquire(timeout=.025):
            if cancel.is_set() or self._closed:
                return
        self._stop.clear()
        stream = None
        try:
            if cancel.is_set() or self._closed:
                return
            backend = self._backend if self._backend is not None else load_backend()
            device = AudioDeviceCatalog(backend).resolve(self.device, "output")
            original = pcm
            try:
                backend.check_output_settings(device=device, channels=pcm.format.channels,
                                              dtype="int16", samplerate=pcm.format.sample_rate)
            except Exception:
                # Native TTS and remote providers often return 22.05/24 kHz,
                # while Windows WASAPI/Linux devices may require native 48 kHz.
                # Keep every channel and preserve duration when adapting rate.
                info = backend.query_devices(device, "output")
                rate = float(info["default_samplerate"])
                channels = int(info["max_output_channels"])
                if channels < pcm.format.channels:
                    raise VoicePlaybackError("Seçilen çıkış aygıtı sesli yanıtın kanal sayısını desteklemiyor.")
                if not math.isfinite(rate) or not 8000 <= rate <= 192000:
                    raise VoicePlaybackError("Seçilen çıkış aygıtının örnekleme hızı desteklenmiyor.")
                target_rate = round(rate)
                if target_rate == pcm.format.sample_rate:
                    raise VoicePlaybackError("Seçilen çıkış aygıtı sesli yanıtın PCM biçimini desteklemiyor.")
                backend.check_output_settings(device=device, channels=pcm.format.channels,
                                              dtype="int16", samplerate=target_rate)
                pcm = _resample_pcm(pcm, target_rate, cancel, stop=self._stop)
                if pcm is None:
                    return
            with self._lock:
                if self._cache_replay:
                    self._last = original
                self._playing = True
            finished = threading.Event()
            errors = []
            position = 0
            first_audio_at = None
            last_callback = self._clock()
            frame_bytes = pcm.format.frame_bytes

            def callback(outdata, frames, timing, status):
                nonlocal position, last_callback, first_audio_at
                last_callback = self._clock()
                # Buffer writes never wait for a lock, queue or UI callback.
                outdata[:] = bytes(len(outdata))
                if cancel.is_set() or self._stop.is_set():
                    raise backend.CallbackAbort
                if status:
                    errors.append("underflow")
                    raise backend.CallbackAbort
                if self._paused.is_set():
                    return
                count = min(frames * frame_bytes, len(pcm.data) - position)
                block = pcm.data[position:position + count]
                volume = self._volume
                if volume != 1.0:
                    samples = array("h")
                    samples.frombytes(block)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    for index, value in enumerate(samples):
                        samples[index] = round(value * volume)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    block = samples.tobytes()
                outdata[:count] = block
                if position == 0 and count and on_first_audio is not None:
                    first_audio_at = last_callback
                position += count
                if position >= len(pcm.data):
                    # CallbackStop drains queued data before finished_callback.
                    raise backend.CallbackStop

            stream = backend.RawOutputStream(device=device, samplerate=pcm.format.sample_rate,
                                             channels=pcm.format.channels, dtype="int16",
                                             blocksize=max(1, pcm.format.sample_rate // 50),
                                             callback=callback, finished_callback=finished.set)
            stream.start()
            while True:
                drained = finished.wait(.025)
                if on_first_audio is not None and first_audio_at is not None:
                    on_first_audio(first_audio_at)
                    on_first_audio = None
                if drained:
                    break
                if cancel.is_set() or self._stop.is_set() or self._closed:
                    stream.abort()
                    return
                if self._clock() - last_callback > 5:
                    raise VoicePlaybackError("Ses çıkış aygıtı yanıt vermiyor veya bağlantısı kesildi.")
                if not stream.active:
                    # Permit finished_callback to race the inactive observation.
                    if not finished.wait(.05):
                        raise VoicePlaybackError("Ses çıkış aygıtı oynatma tamamlanmadan durdu.")
            if cancel.is_set() or self._stop.is_set() or self._closed:
                return
            if errors or position < len(pcm.data):
                raise VoicePlaybackError("Ses çıkış verisi kesildi. Çıkış aygıtını kontrol edin.")
            if on_complete is not None:
                on_complete()
        except VoiceAudioError:
            raise
        except Exception as error:
            if not cancel.is_set() and not self._stop.is_set() and not self._closed:
                raise VoicePlaybackError("Ses çıkış aygıtı açılamadı veya oynatma kesildi.") from error
        finally:
            try:
                if stream is not None:
                    try:
                        if stream.active:
                            stream.abort()
                    finally:
                        stream.close()
            finally:
                with self._lock:
                    self._playing = False
                self._serial.release()
