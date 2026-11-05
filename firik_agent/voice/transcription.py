"""Validated, bounded, sequential remote inference, independent of HTTP or Tk.

The FIRIK OpenAPI does not advertise a duration maximum. Thirty seconds is a
conservative client request budget, not a claimed provider maximum. A deployment
with a verified lower limit must lower the policy. No overlap is used because
the endpoint returns plain text without reliable timestamp alignment.
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import io
import math
from sys import byteorder
import wave

MAX_AUDIO_BYTES = 25 * 1024 * 1024


class FinalTranscript(str):
    """String-compatible finalized text with actual provider language evidence."""
    def __new__(cls, text, recognized_language=None):
        value = super().__new__(cls, text)
        value.recognized_language = recognized_language
        return value

    def strip(self, chars=None):
        return FinalTranscript(super().strip(chars), self.recognized_language)


@dataclass(frozen=True)
class TranscriptionPolicy:
    max_request_seconds: float = 30
    max_total_seconds: float = 120
    max_segments: int = 8

    def __post_init__(self):
        for value, high in ((self.max_request_seconds, 30), (self.max_total_seconds, 120)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= high):
                raise ValueError("Geçersiz ses çözümleme süresi.")
        if (isinstance(self.max_segments, bool) or not isinstance(self.max_segments, int)
                or not 1 <= self.max_segments <= 120):
            raise ValueError("Geçersiz ses parçası sınırı.")


def wav_segments(audio: bytes, policy: TranscriptionPolicy):
    """Validate the entire container before yielding any network-bound segment."""
    if not isinstance(audio, bytes) or not 44 < len(audio) <= MAX_AUDIO_BYTES:
        raise ValueError("Ses boş veya boyut sınırını aşıyor.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            rate, frames = source.getframerate(), source.getnframes()
            if (source.getnchannels() != 1 or source.getsampwidth() != 2
                    or source.getcomptype() != "NONE" or rate not in (8000, 16000, 32000, 48000)
                    or frames <= 0 or frames > int(rate * policy.max_total_seconds)):
                raise ValueError("Ses mono PCM16 WAV olmalı ve kayıt sınırını aşmamalıdır.")
            pcm = source.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("Eksik WAV ses verisi.")
    except (wave.Error, EOFError, OSError) as error:
        raise ValueError("Geçersiz WAV ses kaydı.") from error
    size = max(1, int(rate * policy.max_request_seconds))
    if math.ceil(frames / size) > policy.max_segments:
        raise ValueError("Ses parçası sınırı aşıldı; daha kısa kayıt yapın.")
    # Prefer a low-energy 100 ms boundary near the request cap, without overlap.
    # Every frame is sent once, in order; the request cap is never exceeded.
    boundaries = []
    offset = 0
    while offset < frames:
        end = min(frames, offset + size)
        if end < frames and size >= rate:
            width = max(1, rate // 10)
            search = max(offset + size // 2, end - rate)
            candidates = []
            for point in range(search, end - width + 1, width):
                values = array("h", pcm[point * 2:(point + width) * 2])
                if byteorder != "little":
                    values.byteswap()
                candidates.append((sum(v * v for v in values), point + width))
            if candidates:
                end = min(candidates)[1]
        boundaries.append((offset, end))
        if len(boundaries) > policy.max_segments:
            raise ValueError("Ses parçası sınırı aşıldı; daha kısa kayıt yapın.")
        offset = end
    # Plan and check every boundary before upload. Build only the one WAV
    # awaiting inference instead of retaining all encoded segments at once.
    for offset, end in boundaries:
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(pcm[offset * 2:end * 2])
        yield output.getvalue()


class TranscriptionAdapter:
    def __init__(self, request, policy: TranscriptionPolicy | None = None):
        self.request = request
        self.policy = policy or TranscriptionPolicy()

    async def transcribe(self, audio: bytes, model: str, language: str) -> FinalTranscript:
        results = []
        languages = []
        count = 0
        for segment in wav_segments(audio, self.policy):
            result = await self.request(segment, model, language)
            if not isinstance(result, str) or not result.strip():
                raise ValueError("Konuşma algılanamadı. Lütfen yeniden deneyin.")
            count += len(result.strip()) + bool(results)
            if count > 32000:
                raise ValueError("Transkript çok uzun.")
            results.append(result.strip())
            languages.append(getattr(result, "recognized_language", None))
        recognized = languages[0] if languages and languages[0] and all(value == languages[0] for value in languages) else None
        return FinalTranscript(" ".join(results), recognized)
