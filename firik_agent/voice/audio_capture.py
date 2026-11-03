"""Bounded PortAudio capture with a nonblocking callback/worker boundary."""
from __future__ import annotations
from array import array
import math
import queue
import sys
import threading
import time

from firik_agent.voice.audio_devices import AudioDeviceCatalog
from firik_agent.voice.errors import (VoiceAudioError, VoiceDeviceError, VoiceFormatError,
                                      VoiceOverflowError, VoicePermissionError)
from firik_agent.voice.models import AudioFormat, MAX_AUDIO_BYTES, PCMBuffer
from firik_agent.voice.vad import VADConfig, UtteranceSegmenter, pcm_rms, pcm_samples


def _encode_samples(values):
    result = array('h', values)
    if sys.byteorder != 'little':
        result.byteswap()
    return result.tobytes()


class PCMResampler:
    """Stateful rational-position mono int16 resampling with bounded history.

    Integer positions avoid per-block duration drift. An eighth-order low pass
    precedes downsampling. Finalization emits the nearest N*out/in frame count.
    """
    def __init__(self, source_rate, target_rate):
        AudioFormat(source_rate)
        AudioFormat(target_rate)
        self.source_rate, self.target_rate = source_rate, target_rate
        self._samples = []
        self._base = self._total = self._next = 0
        self._closed = False
        self._filters = []
        if source_rate > target_rate:
            omega = 2 * math.pi * .4 * target_rate / source_rate
            cosine = math.cos(omega)
            # Cascaded Butterworth biquads: bounded state and no local models.
            for stage in range(4):
                q = 1 / (2 * math.cos((2 * stage + 1) * math.pi / 16))
                alpha = math.sin(omega) / (2 * q)
                a0 = 1 + alpha
                self._filters.append([(1-cosine)/(2*a0), (1-cosine)/a0, (1-cosine)/(2*a0),
                                      -2*cosine/a0, (1-alpha)/a0, 0., 0., 0., 0.])

    def feed(self, pcm, *, final=False):
        if self._closed:
            raise VoiceFormatError('Örnekleme dönüştürücüsü zaten kapatıldı.')
        values = pcm_samples(pcm)
        if self.source_rate == self.target_rate:
            self._closed = final
            return pcm
        if self._filters:
            filtered = []
            for value in values:
                for stage in self._filters:
                    b0, b1, b2, a1, a2, x1, x2, y1, y2 = stage
                    y = b0*value + b1*x1 + b2*x2 - a1*y1 - a2*y2
                    stage[5], stage[6], stage[7], stage[8] = value, x1, y, y1
                    value = y
                filtered.append(value)
            self._samples.extend(filtered)
        else:
            self._samples.extend(values)
        self._total += len(values)
        limit = (self._total*self.target_rate*2+self.source_rate)//(2*self.source_rate)
        output = []
        while self._next < limit:
            index, fraction = divmod(self._next*self.source_rate, self.target_rate)
            if index >= self._total or (fraction and index+1 >= self._total and not final):
                break
            first = self._samples[index-self._base]
            second = self._samples[min(index+1, self._total-1)-self._base]
            value = round(first+(second-first)*fraction/self.target_rate)
            output.append(max(-32768, min(32767, value)))
            self._next += 1
        keep_from = min(self._total-1, self._next*self.source_rate//self.target_rate)
        drop = max(0, keep_from-self._base)
        if drop:
            del self._samples[:drop]
            self._base += drop
        self._closed = final
        return _encode_samples(output)


class SoundDeviceRecorder:
    """Capture bounded WAV. Native callbacks only copy into a finite queue.

    A synchronous compatibility path serves injected legacy test adapters
    without start(). Real sounddevice streams always use the callback path.
    """
    supports_echo_cancellation = False
    supports_barge_in = False

    def __init__(self, backend=None, **options):
        self._backend = backend
        self._options_lock = threading.Lock()
        self._options = dict(target_rate=None, input_gain=1., on_level=None,
                             vad_engine='energy', pre_roll_ms=200, queue_blocks=25)
        self.configure(**options)

    def configure(self, **options):
        with self._options_lock:
            if set(options)-set(self._options):
                raise ValueError('Unknown capture option')
            candidate = {**self._options, **options}
            if candidate['target_rate'] is not None:
                AudioFormat(candidate['target_rate'])
            gain = candidate['input_gain']
            if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain) or not .1 <= gain <= 4:
                raise VoiceAudioError('Mikrofon kazancı 0.1 ile 4 arasında olmalıdır.')
            blocks = candidate['queue_blocks']
            if isinstance(blocks, bool) or not isinstance(blocks, int) or not 2 <= blocks <= 100:
                raise VoiceAudioError('Mikrofon kuyruğu sınırı geçersiz.')
            VADConfig(pre_roll_ms=candidate['pre_roll_ms'], engine=candidate['vad_engine'])
            if candidate['on_level'] is not None and not callable(candidate['on_level']):
                raise ValueError('on_level must be callable')
            self._options = candidate

    def _load_backend(self):
        if self._backend is not None:
            return self._backend
        from firik_agent.voice import audio
        try:
            return audio.importlib.import_module('sounddevice')
        except (ImportError, OSError) as error:
            raise VoiceAudioError("Mikrofon desteği yüklenemedi. Ses desteğini 'pip install firik-agent[voice]' ile kurun; Linux'ta PortAudio paketinin de kurulu olduğunu kontrol edin.") from error

    @staticmethod
    def _validate_duration(max_seconds):
        if (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
                or not math.isfinite(max_seconds) or not 0 < max_seconds <= 120):
            raise VoiceAudioError('Kayıt süresi 0 ile 120 saniye arasında olmalıdır.')

    def _blocks(self, backend, device, rate, block, budget, stop, cancel, queue_blocks):
        pending = queue.Queue(maxsize=queue_blocks)
        overflow = threading.Event()
        finished = threading.Event()

        def callback(indata, frames, timing, status):
            if cancel.is_set() or stop.is_set():
                return
            if status or frames <= 0 or frames > block or len(indata) != frames*2:
                overflow.set()
                return
            try:
                pending.put_nowait(bytes(indata))
            except queue.Full:
                overflow.set()

        stream = backend.RawInputStream(device=device, samplerate=rate, channels=1, dtype='int16',
                                        blocksize=block, callback=callback, finished_callback=finished.set)
        if not hasattr(stream, 'start'):
            with stream:
                while budget and not stop.is_set() and not cancel.is_set():
                    count = min(block, budget)
                    data, dropped = stream.read(count)
                    if cancel.is_set():
                        return
                    if dropped:
                        raise VoiceOverflowError('Mikrofon verisi kesildi. Diğer ses uygulamalarını kapatıp yeniden deneyin.')
                    if len(data) != count*2:
                        raise VoiceFormatError('Mikrofondan eksik ses verisi alındı. Yeniden deneyin.')
                    budget -= count
                    yield bytes(data)
            return
        try:
            stream.start()
            last_block = time.monotonic()
            while budget and not stop.is_set() and not cancel.is_set():
                if overflow.is_set():
                    raise VoiceOverflowError('Mikrofon verisi kesildi. Diğer ses uygulamalarını kapatıp yeniden deneyin.')
                try:
                    data = pending.get(timeout=.05)
                except queue.Empty:
                    if finished.is_set() or not stream.active or time.monotonic()-last_block > 2:
                        raise VoiceDeviceError('Mikrofon bağlantısı kesildi. Aygıtı yeniden bağlayıp tekrar deneyin.')
                    continue
                last_block = time.monotonic()
                data = data[:budget*2]
                budget -= len(data)//2
                yield data
        finally:
            try:
                stream.abort()
            finally:
                stream.close()

    def _capture(self, stop, cancel, max_seconds, device, vad_config=None, on_speech_start=None):
        self._validate_duration(max_seconds)
        if cancel.is_set() or (stop.is_set() and vad_config):
            return b''
        with self._options_lock:
            options = self._options.copy()
        backend = self._load_backend()
        try:
            if device is not None and hasattr(backend, 'query_hostapis'):
                device = AudioDeviceCatalog(backend).resolve(device, 'input')
            info = backend.query_devices(device, 'input')
            sample_rate = float(info['default_samplerate'])
            if not math.isfinite(sample_rate) or not 8000 <= sample_rate <= 192000 or int(info['max_input_channels']) < 1:
                raise VoiceAudioError('Seçilen mikrofonun kayıt biçimi desteklenmiyor.')
            source_rate = int(round(sample_rate))
            rate = options['target_rate'] or source_rate
            if vad_config and vad_config.engine == 'webrtc' and rate not in (8000, 16000, 32000, 48000):
                rate = 16000
            budget = int(source_rate*max_seconds)
            if int(rate*max_seconds)*2+44 > MAX_AUDIO_BYTES:
                limit = int((MAX_AUDIO_BYTES-44)/(rate*2))
                raise VoiceAudioError(f'Bu mikrofonun örnekleme hızında kayıt sınırı {limit} saniyedir. Ses ayarlarından kayıt süresini kısaltın veya daha düşük örnekleme hızlı bir mikrofon seçin.')
            backend.check_input_settings(device=device, channels=1, dtype='int16', samplerate=source_rate)
            block = max(1, source_rate//(50 if vad_config else 20))
            converter = PCMResampler(source_rate, rate)
            segmenter = UtteranceSegmenter(rate, int(rate*max_seconds), vad_config) if vad_config else None
            pcm = bytearray()
            vad_pending = bytearray()
            announced = False
            last_level = -math.inf
            blocks = self._blocks(backend, device, source_rate, block, budget, stop, cancel, options['queue_blocks'])

            def consume(data):
                nonlocal announced, last_level
                if options['on_level'] is not None and time.monotonic()-last_level >= .1:
                    last_level = time.monotonic()
                    options['on_level'](min(1., pcm_rms(data)/32768))
                if segmenter is None:
                    pcm.extend(data)
                    return False
                vad_pending.extend(data)
                frame_bytes = rate//50*2
                while len(vad_pending) >= frame_bytes:
                    frame = bytes(vad_pending[:frame_bytes])
                    del vad_pending[:frame_bytes]
                    ended = segmenter.feed(frame)
                    if segmenter.started and not announced:
                        announced = True
                        if on_speech_start:
                            on_speech_start()
                    if ended:
                        return True
                return False

            try:
                for data in blocks:
                    if sys.byteorder != 'little':
                        values = array('h')
                        values.frombytes(data)
                        values.byteswap()
                        data = values.tobytes()
                    if options['input_gain'] != 1:
                        data = _encode_samples(max(-32768, min(32767, round(v*options['input_gain']))) for v in pcm_samples(data))
                    if consume(converter.feed(data)):
                        break
                if not cancel.is_set():
                    consume(converter.feed(b'', final=True))
                    if segmenter and vad_pending and not segmenter.finished:
                        segmenter.feed(bytes(vad_pending))
            finally:
                blocks.close()
            if cancel.is_set() or (stop.is_set() and segmenter):
                return b''
            if segmenter:
                pcm = segmenter.pcm
                if not pcm:
                    return b''
            else:
                if len(pcm) < rate*2*.15:
                    raise VoiceAudioError('Kayıt çok kısa. Mikrofonu açıp konuşun, ardından kaydı bitirin.')
                if pcm_rms(pcm) < 40:
                    raise VoiceAudioError('Konuşma duyulamadı. Mikrofon ses düzeyini kontrol edip yeniden deneyin.')
            return b'' if cancel.is_set() else PCMBuffer(bytes(pcm), AudioFormat(rate)).to_wav()
        except VoiceAudioError:
            if cancel.is_set():
                return b''
            raise
        except PermissionError as error:
            if cancel.is_set():
                return b''
            raise VoicePermissionError('Mikrofon izni gerekli. Sistem gizlilik ayarlarından Firik Agent için mikrofon erişimini açın.') from error
        except Exception as error:
            if cancel.is_set():
                return b''
            detail = str(error).casefold()
            if any(marker in detail for marker in ('permission denied', 'access denied', 'not permitted', 'permission required')):
                raise VoicePermissionError('Mikrofon izni gerekli. Sistem gizlilik ayarlarından Firik Agent için mikrofon erişimini açın.') from error
            raise VoiceAudioError('Mikrofon açılamadı veya kayıt kesildi. Sistem mikrofon iznini, seçili giriş aygıtını ve başka uygulamaların kullanımını kontrol edin.') from error

    def record(self, stop, cancel, max_seconds, device=None):
        return self._capture(stop, cancel, max_seconds, device)

    def record_utterance(self, stop, cancel, max_seconds, device=None, *, threshold=350,
                         silence_ms=700, min_speech_ms=200, on_speech_start=None):
        with self._options_lock:
            options = self._options.copy()
        config = VADConfig(threshold, silence_ms, min_speech_ms, options['pre_roll_ms'], options['vad_engine'])
        return self._capture(stop, cancel, max_seconds, device, config, on_speech_start)
