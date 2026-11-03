"""Small immutable audio values; importing these never loads a native backend."""
from __future__ import annotations

from dataclasses import dataclass
import io
import math
import wave

from firik_agent.voice.errors import VoiceFormatError

MAX_AUDIO_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int
    channels: int = 1
    sample_width: int = 2

    def __post_init__(self):
        for value in (self.sample_rate, self.channels, self.sample_width):
            if isinstance(value, bool) or not isinstance(value, int):
                raise VoiceFormatError("Ses biçimi tam sayısal PCM ayarları gerektirir.")
        if not 8000 <= self.sample_rate <= 192000 or not 1 <= self.channels <= 8 or self.sample_width not in (1, 2, 3, 4):
            raise VoiceFormatError("Ses örnekleme hızı, kanal sayısı veya örnek biçimi desteklenmiyor.")

    @property
    def frame_bytes(self):
        return self.channels * self.sample_width


@dataclass(frozen=True)
class PCMBuffer:
    """Interleaved little-endian integer PCM (8-bit WAV PCM is unsigned)."""
    data: bytes
    format: AudioFormat

    def __post_init__(self):
        if not isinstance(self.format, AudioFormat) or not isinstance(self.data, bytes) or len(self.data) % self.format.frame_bytes:
            raise VoiceFormatError("PCM ses verisi eksik veya geçersiz.")
        if len(self.data) > MAX_AUDIO_BYTES:
            raise VoiceFormatError("Ses verisi izin verilen boyutu aşıyor.")

    @property
    def frames(self):
        return len(self.data) // self.format.frame_bytes

    @property
    def duration(self):
        return self.frames / self.format.sample_rate

    def to_wav(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(self.format.channels)
            wav.setsampwidth(self.format.sample_width)
            wav.setframerate(self.format.sample_rate)
            wav.writeframes(self.data)
        return output.getvalue()

    @classmethod
    def from_wav(cls, data: bytes, *, max_bytes=MAX_AUDIO_BYTES, max_seconds=120):
        if not isinstance(data, bytes) or len(data) > max_bytes or len(data) < 44:
            raise VoiceFormatError("WAV ses verisi boş veya izin verilen boyutu aşıyor.")
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                if wav.getcomptype() != "NONE":
                    raise VoiceFormatError("Sıkıştırılmış WAV ses biçimi desteklenmiyor.")
                fmt = AudioFormat(wav.getframerate(), wav.getnchannels(), wav.getsampwidth())
                frames = wav.getnframes()
                if frames <= 0 or frames * fmt.frame_bytes > max_bytes:
                    raise VoiceFormatError("WAV ses uzunluğu geçersiz.")
                if max_seconds is not None and (not math.isfinite(max_seconds) or frames > fmt.sample_rate * max_seconds):
                    raise VoiceFormatError("Ses kaydı izin verilen süreyi aşıyor.")
                pcm = wav.readframes(frames)
                if len(pcm) != frames * fmt.frame_bytes:
                    raise VoiceFormatError("WAV ses verisi eksik.")
        except (wave.Error, EOFError, OSError, ValueError) as error:
            raise VoiceFormatError("WAV ses kabı okunamadı.") from error
        return cls(pcm, fmt)
