"""Offline voice setup information and an explicit, bounded local input test.

Viewing diagnostics never imports native audio backends, queries devices,
requests permission, or makes a network request. The input test only acquires
the microphone after the caller explicitly starts it; no PCM leaves its worker.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import threading

from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.errors import VoiceAudioError
from firik_agent.voice.events import LatestEvent

INPUT_TEST_SECONDS = 3.0


def _package(module, distribution):
    try:
        present = importlib.util.find_spec(module) is not None
    except (ImportError, OSError, ValueError):
        present = False
    version = None
    if present:
        try:
            version = importlib.metadata.version(distribution)
        except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
            pass
    return {"installed": present, "version": version}


def voice_diagnostics(settings: VoiceSettings | None = None):
    """Return safe local setup facts, never credentials or provider responses."""
    settings = settings or VoiceSettings()
    system = platform.system()
    guidance = {
        "Darwin": "Sistem Ayarları > Gizlilik ve Güvenlik > Mikrofon: Firik Agent veya kaynak çalıştırıcınıza izin verin.",
        "Windows": "Ayarlar > Gizlilik ve güvenlik > Mikrofon: mikrofon ve masaüstü uygulama erişimini açın.",
        "Linux": "PortAudio paketini ve ses sunucusu/mikrofon erişimini kontrol edin; dağıtımınıza göre libportaudio2 gerekebilir.",
    }.get(system, "İşletim sisteminin mikrofon iznini ve PortAudio kurulumunu kontrol edin.")
    packages = {
        "sounddevice": _package("sounddevice", "sounddevice"),
        "webrtcvad": _package("webrtcvad", "webrtcvad-wheels"),
        "soundfile": _package("soundfile", "soundfile"),
    }
    return {
        "platform": system,
        "python": platform.python_version(),
        "packages": packages,
        "backend": {"name": "PortAudio", "status": "not_opened",
                    "package_present": packages["sounddevice"]["installed"],
                    "device_access": "not_tested"},
        "microphone_permission": {"status": "not_checked", "guidance": guidance},
        "settings": {"enabled": settings.enabled, "interaction_mode": settings.interaction_mode,
                     "asr_provider": settings.asr_provider, "tts_provider": settings.tts_provider,
                     "asr_model": settings.model if settings.asr_provider == "evren" else "configured_local_model",
                     "tts_model": settings.tts_model, "tts_voice": settings.tts_voice,
                     "tts_format": settings.tts_format, "language": settings.language,
                     "input_selection": "system_default" if settings.input_device is None else "selected_device",
                     "output_selection": "system_default" if settings.output_device is None else "selected_device",
                     "sample_rate": settings.sample_rate, "vad_engine": settings.vad_engine,
                     "vad_enabled": settings.vad_enabled, "read_replies": settings.read_replies,
                     "stream_replies": settings.stream_replies, "completion_chime": settings.completion_chime},
        "capabilities": {"partial_transcripts": False, "acoustic_echo_cancellation": False,
                         "simultaneous_barge_in": False, "sentence_playback": True,
                         "true_streaming_tts": False},
        "client_limits": {"input_test_seconds": INPUT_TEST_SECONDS,
                          "max_utterance_seconds": settings.max_record_seconds,
                          "asr_request_seconds": settings.asr_request_seconds,
                          "asr_total_seconds": 120, "asr_segments": 8,
                          "audio_bytes": 25 * 1024 * 1024},
        "privacy": {"input_test_remote_requests": False, "retained_input_audio": False,
                    "device_enumeration": "explicit_refresh_only"},
        "notes": ["Bu görünüm mikrofonu açmaz ve izin durumunu doğrulamaz.",
                  "Yerel girdi testi yalnızca ses düzeyini gösterir; ses çözümlemeye gönderilmez.",
                  "Paketin kurulu olması yerel aygıt veya izin erişiminin çalıştığını kanıtlamaz.",
                  "Metin sohbeti ses paketleri olmadan kullanılabilir."],
    }


class InputLevelTest:
    """One explicit local microphone test, sharing application capture ownership.

    cancel() is nonblocking. shutdown() is for the service cleanup worker;
    native ownership remains held until the recorder actually closes.
    """
    def __init__(self, *, coordinator, dispatch, settings, on_level, on_done, on_error,
                 recorder_factory=None, seconds=INPUT_TEST_SECONDS):
        if not isinstance(seconds, (float, int)) or isinstance(seconds, bool) or not 0 < seconds <= 5:
            raise ValueError("Mikrofon testi en fazla beş saniye olabilir.")
        self.coordinator, self.settings = coordinator, settings
        self._dispatch, self._on_done, self._on_error = dispatch, on_done, on_error
        self._recorder_factory = recorder_factory
        self._seconds = float(seconds)
        self._cancel = threading.Event()
        self._user_cancel = threading.Event()
        self._stop = threading.Event()
        self.done = threading.Event()
        self._levels = LatestEvent(dispatch, on_level)
        self._worker = None
        self._timer = None
        self._start_lock = threading.Lock()
        self._started = False

    def _post_error(self, message):
        def deliver():
            if not self._user_cancel.is_set():
                self._on_error(message)
        self._dispatch(deliver)

    def reject(self, message):
        with self._start_lock:
            if self._started:
                return self
            self._started = True
        self._levels.close()
        self.done.set()
        self._post_error(message)
        self._dispatch(self._on_done)
        return self

    def start(self):
        with self._start_lock:
            if self._started:
                return self
            self._started = True
        if not self.coordinator.claim(self):
            self._levels.close()
            self.done.set()
            self._post_error("Mikrofon başka bir ses işlemi tarafından kullanılıyor. Önce dinlemeyi durdurun.")
            self._dispatch(self._on_done)
            return self
        self._worker = threading.Thread(target=self._run, name="FirikVoiceInputTest", daemon=True)
        try:
            self._worker.start()
        except RuntimeError:
            self.coordinator.release(self)
            self.done.set()
            self._levels.close()
            self._post_error("Mikrofon testi başlatılamadı; metinle devam edip yeniden deneyin.")
            self._dispatch(self._on_done)
        return self

    def _run(self):
        acquired = native = False
        self._timer = threading.Timer(self._seconds, self._cancel.set)
        self._timer.daemon = True
        try:
            self._timer.start()
            while not self._cancel.is_set():
                if self.coordinator.capture_lock.acquire(timeout=.025):
                    acquired = True
                    break
            if not acquired:
                if not self._user_cancel.is_set():
                    self._post_error("Ses aygıtı henüz kapanmadı. Kısa süre sonra yeniden deneyin.")
                return
            if not self.coordinator.begin_capture(self._cancel, duplex=False):
                if not self._user_cancel.is_set():
                    self._post_error("Yanıt oynatılırken mikrofon testi başlatılamadı. Konuşmayı durdurup yeniden deneyin.")
                return
            native = True
            if self._cancel.is_set():
                return
            if self._recorder_factory is None:
                from firik_agent.voice.audio import SoundDeviceRecorder
                recorder = SoundDeviceRecorder()
            else:
                recorder = self._recorder_factory()
            recorder.configure(target_rate=self.settings.sample_rate, input_gain=self.settings.input_gain,
                               vad_engine="energy", pre_roll_ms=0,
                               on_level=lambda level: self._levels.publish(level) if not self._cancel.is_set() else None)
            # The segmenter treats silence as a normal empty result. Only level
            # telemetry is delivered; no returned PCM/WAV is retained or sent.
            recorder.record_utterance(self._stop, self._cancel, self._seconds,
                device=self.settings.input_device, threshold=self.settings.vad_threshold,
                silence_ms=3000, min_speech_ms=1000)
        except VoiceAudioError as error:
            if not self._cancel.is_set():
                self._post_error(str(error))
        except Exception:
            if not self._cancel.is_set():
                self._post_error("Mikrofon testi yapılamadı. Mikrofon iznini, aygıtı ve ses paketlerini kontrol edin; metinle devam edebilirsiniz.")
        finally:
            self._timer.cancel()
            if self._timer.ident is not None:
                self._timer.join(timeout=.1)
            self._levels.close()
            if native:
                self.coordinator.end_capture()
            if acquired:
                self.coordinator.capture_lock.release()
            self.coordinator.release(self)
            self.done.set()
            self._dispatch(self._on_done)

    def cancel(self):
        self._user_cancel.set()
        self._cancel.set()
        self._stop.set()
        self._levels.close()

    def shutdown(self, timeout=3.0):
        self.cancel()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread() and worker.ident is not None:
            worker.join(max(0, timeout))
        return worker is None or not worker.is_alive()
