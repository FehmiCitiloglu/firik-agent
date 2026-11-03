"""CPU-only local speech segmentation, independent of capture and inference."""
from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass
import importlib
import math
import sys

from firik_agent.voice.errors import VoiceAudioError, VoiceFormatError
from firik_agent.voice.models import AudioFormat, MAX_AUDIO_BYTES


def pcm_samples(data):
    if len(data) % 2:
        raise VoiceFormatError("PCM ses verisi eksik.")
    values = array("h")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def pcm_rms(data):
    values = pcm_samples(data)
    if not values:
        return 0.
    mean = sum(values) / len(values)
    return math.sqrt(max(0., sum(v * v for v in values) / len(values) - mean * mean))


@dataclass(frozen=True)
class VADConfig:
    threshold: int = 350
    silence_ms: int = 700
    min_speech_ms: int = 200
    pre_roll_ms: int = 200
    engine: str = "energy"
    aggressiveness: int = 2

    def __post_init__(self):
        for value, low, high in ((self.threshold, 40, 10000), (self.silence_ms, 250, 3000),
                                 (self.min_speech_ms, 100, 1000), (self.pre_roll_ms, 0, 1000),
                                 (self.aggressiveness, 0, 3)):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise VoiceAudioError("Geçersiz ses etkinliği algılama ayarı.")
        if self.engine not in ("energy", "webrtc"):
            raise VoiceAudioError("Desteklenmeyen ses etkinliği algılama motoru.")


class EnergyDetector:
    """DC-rejecting RMS with conservative idle noise-floor adaptation.

    Sustained onset rejects isolated keyboard impulses. This is a fallback,
    not acoustic echo cancellation or a language-dependent speech recognizer.
    """
    def __init__(self, threshold=350):
        self.threshold = threshold
        self.noise_floor = 0.

    def is_speech(self, pcm, sample_rate):
        rms = pcm_rms(pcm)
        threshold = max(self.threshold, self.noise_floor * 2.5)
        voiced = rms >= threshold
        if not voiced and rms < self.threshold:
            self.noise_floor = .98 * self.noise_floor + .02 * rms
        return voiced


class WebRTCDetector:
    """Optional native WebRTC VAD, without Torch/ONNX/model downloads."""
    def __init__(self, aggressiveness=2):
        try:
            self._vad = importlib.import_module("webrtcvad").Vad(aggressiveness)
        except (ImportError, OSError) as error:
            raise VoiceAudioError("WebRTC VAD için firik-agent[voice-vad] ek paketini kurun veya enerji algılayıcıyı seçin.") from error

    def is_speech(self, pcm, sample_rate):
        if sample_rate not in (8000, 16000, 32000, 48000) or len(pcm) not in (
                sample_rate * 2 // 100, sample_rate * 2 // 50, sample_rate * 6 // 100):
            raise VoiceFormatError("WebRTC VAD 10, 20 veya 30 ms mono PCM çerçevesi gerektirir.")
        return self._vad.is_speech(pcm, sample_rate)


class UtteranceSegmenter:
    """Consume 20ms mono int16 frames and retain one bounded utterance."""
    def __init__(self, sample_rate, max_frames, config=None, detector=None):
        AudioFormat(sample_rate)
        if (isinstance(max_frames, bool) or not isinstance(max_frames, int) or max_frames <= 0
                or max_frames > min(sample_rate * 120, MAX_AUDIO_BYTES // 2)):
            raise VoiceAudioError("Ses etkinliği pencere sınırı geçersiz.")
        self.config = config or VADConfig()
        self.sample_rate, self.max_frames = sample_rate, max_frames
        self.detector = detector or (WebRTCDetector(self.config.aggressiveness)
                                    if self.config.engine == "webrtc" else EnergyDetector(self.config.threshold))
        self.started = False
        self.finished = False
        self._onset = self._silence = self._frames = 0
        self._pre = deque()
        self._pre_frames = 0
        self._pcm = bytearray()

    def feed(self, pcm):
        if self.finished:
            return True
        count = min(len(pcm) // 2, self.max_frames - self._frames)
        pcm = pcm[:count * 2]
        if not count:
            self.finished = True
            return True
        self._frames += count
        # A final partial frame is retained but cannot be classified by WebRTC.
        if self.config.engine == "webrtc" and count != self.sample_rate // 50:
            voiced = False
        else:
            voiced = self.detector.is_speech(pcm, self.sample_rate)
        if not self.started:
            self._pre.append(pcm)
            self._pre_frames += count
            limit = int(self.sample_rate * (self.config.pre_roll_ms + self.config.min_speech_ms) / 1000)
            while len(self._pre) > 1 and self._pre_frames - len(self._pre[0]) // 2 >= limit:
                self._pre_frames -= len(self._pre.popleft()) // 2
            self._onset = self._onset + count if voiced else 0
            if self._onset >= self.sample_rate * self.config.min_speech_ms / 1000:
                self.started = True
                self._pcm.extend(b"".join(self._pre))
                self._pre.clear()
                self._pre_frames = 0
        else:
            self._pcm.extend(pcm)
            self._silence = 0 if voiced else self._silence + count
            if self._silence >= self.sample_rate * self.config.silence_ms / 1000:
                self.finished = True
        if self._frames >= self.max_frames:
            self.finished = True
        return self.finished

    @property
    def pcm(self):
        return bytes(self._pcm) if self.started else b""
