"""Conservative client audio policy and normalized Evren response values.

Bounds here are client cost/memory budgets, not advertised server limits.
Only PCM WAV input is admitted until compressed input limits are documented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import math
import re
import wave
from typing import Any

MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_AUDIO_SECONDS = 120
MAX_TRANSCRIPT_BYTES = 256 * 1024
MAX_SYNTHESIS_CHARS = 1600
TRANSCRIPTION_FORMATS = ("json", "text", "srt", "verbose_json", "diarized_json")
SPEECH_FORMATS = ("mp3", "wav", "flac", "opus", "pcm")


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str | None = None
    duration: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    response_format: str = "json"


@dataclass(frozen=True)
class SpeechResult:
    audio: bytes
    content_type: str
    response_format: str
    metadata: dict[str, str] = field(default_factory=dict)
    sample_rate: int | None = None
    channels: int | None = None


def validate_wav(audio: bytes) -> float:
    if not isinstance(audio, bytes) or not 44 < len(audio) <= MAX_AUDIO_BYTES:
        raise ValueError("Ses kaydı boş veya 25 MiB istemci sınırını aşıyor.")
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            channels, width = source.getnchannels(), source.getsampwidth()
            rate, frames = source.getframerate(), source.getnframes()
            if (source.getcomptype() != "NONE" or channels not in (1, 2)
                    or width not in (1, 2, 3, 4) or not 8000 <= rate <= 48000
                    or not 0 < frames <= rate * MAX_AUDIO_SECONDS):
                raise ValueError("Ses PCM WAV olmalı ve 120 saniyelik istemci sınırını aşmamalıdır.")
            if len(source.readframes(frames)) != frames * channels * width:
                raise ValueError("WAV ses verisi eksik.")
            return frames / rate
    except (wave.Error, EOFError, OSError) as error:
        raise ValueError("Geçersiz ses. Bu istemci PCM WAV kaydı gerektirir.") from error


def transcription_parameters(model: str, options: dict[str, Any]) -> dict[str, str]:
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError("Geçerli bir ses çözümleme modeli seçin.")
    if set(options) - {"language", "prompt", "response_format", "temperature"}:
        raise ValueError("Belgelenmemiş ses çözümleme parametresi.")
    language = options.get("language")
    if language not in (None, "auto") and (not isinstance(language, str) or not re.fullmatch(r"[a-z]{2}", language)):
        raise ValueError("Dil auto veya ISO 639-1 kodu olmalıdır (tr, en).")
    response_format = options.get("response_format", "json")
    if response_format not in TRANSCRIPTION_FORMATS:
        raise ValueError("Desteklenmeyen transkript biçimi.")
    prompt = options.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 2000:
        raise ValueError("Ses çözümleme ipucu 2000 karakteri aşmamalıdır.")
    temperature = options.get("temperature", 0.0)
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature) or not 0 <= temperature <= 1):
        raise ValueError("Ses temperature değeri 0 ile 1 arasında olmalıdır.")
    # 'auto' is a client preference; the wire contract uses an absent hint.
    result = {key: str(value) for key, value in options.items() if value is not None and key != "language"}
    if language not in (None, "auto"):
        result["language"] = language
    result["model"] = model.strip()
    return result


def normalize_transcription(value: Any, response_format: str = "json", metadata: dict | None = None) -> TranscriptionResult:
    language, duration = None, None
    extras: dict[str, Any] = {}
    if isinstance(value, dict):
        text = value.get("text")
        if not isinstance(text, str) and response_format == "diarized_json":
            segments = value.get("segments")
            if isinstance(segments, list) and all(isinstance(s, dict) and isinstance(s.get("text"), str) for s in segments):
                text = " ".join(s["text"].strip() for s in segments)
        supplied_language = value.get("language")
        if isinstance(supplied_language, str) and supplied_language.strip():
            language = supplied_language.strip()
        supplied_duration = value.get("duration")
        if (not isinstance(supplied_duration, bool) and isinstance(supplied_duration, (int, float))
                and math.isfinite(supplied_duration) and supplied_duration >= 0):
            duration = float(supplied_duration)
        # Timing/confidence/segments stay metadata; they are never recognized text.
        extras = {key: item for key, item in value.items() if key not in ("text", "language", "duration")}
    elif isinstance(value, str):
        text = value
        if response_format == "srt":
            blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
            lines = []
            for block in blocks:
                chunk = block.splitlines()
                if chunk and chunk[0].strip().isdigit():
                    chunk = chunk[1:]
                if not chunk or not re.fullmatch(r"\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2}:\d{2}:\d{2},\d{3}", chunk[0].strip()):
                    raise ValueError("Geçersiz SRT transkript yanıtı.")
                lines.extend(chunk[1:])
            text = " ".join(line.strip() for line in lines)
    else:
        text = None
    if not isinstance(text, str) or not text.strip() or len(text) > 32000:
        raise ValueError("Geçersiz veya boş transkript yanıtı.")
    return TranscriptionResult(text.strip(), language, duration, {"headers": dict(metadata or {}), "provider": extras}, response_format)


def speech_parameters(text: str, model: str, voice: str, response_format: str, speed: float,
                      instructions: str | None) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_SYNTHESIS_CHARS or len(text.encode("utf-8")) > 8192:
        raise ValueError("Sentez metni boş olmamalı ve 1600 karakterlik istemci sınırını aşmamalıdır.")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError("Geçerli bir konuşma sentezi modeli seçin.")
    # Public schema describes aliases as equivalent to default, not distinct voices.
    if voice != "default":
        raise ValueError("Evren için doğrulanmış tek ses seçimi default değeridir.")
    if response_format not in SPEECH_FORMATS:
        raise ValueError("Desteklenmeyen ses çıktısı biçimi.")
    if (isinstance(speed, bool) or not isinstance(speed, (int, float))
            or not math.isfinite(speed) or not 0.25 <= speed <= 4):
        raise ValueError("Konuşma hızı 0.25 ile 4 arasında olmalıdır.")
    if instructions is not None and (not isinstance(instructions, str) or len(instructions) > 1000):
        raise ValueError("Ses stili yönergesi 1000 karakteri aşmamalıdır.")
    body = {"model": model.strip(), "input": text, "voice": voice, "response_format": response_format,
            "speed": speed, "stream": False}
    if instructions:
        body["instructions"] = instructions
    return body


def validate_speech(audio: bytes, content_type: str, response_format: str) -> None:
    media_types = {
        "wav": ("audio/wav", "audio/wave", "audio/x-wav"),
        "mp3": ("audio/mpeg", "audio/mp3"),
        "flac": ("audio/flac", "audio/x-flac"),
        "opus": ("audio/opus", "audio/ogg", "application/ogg"),
        "pcm": ("audio/pcm", "audio/L16", "application/octet-stream"),
    }
    if not audio or len(audio) > MAX_AUDIO_BYTES or content_type.lower() not in tuple(s.lower() for s in media_types[response_format]):
        raise ValueError("Sentez yanıtı boş, çok büyük veya beklenen ses türünde değil.")
    valid = {"wav": audio[:4] == b"RIFF" and audio[8:12] == b"WAVE",
             "mp3": audio.startswith(b"ID3") or len(audio) >= 2 and audio[0] == 255 and audio[1] & 0xE0 == 0xE0,
             "flac": audio.startswith(b"fLaC"),
             "opus": audio.startswith(b"OggS") and b"OpusHead" in audio[:128],
             "pcm": len(audio) % 2 == 0}
    if not valid[response_format]:
        raise ValueError("Sentez yanıtının ses kapsayıcısı geçersiz.")
    if response_format == "wav":
        validate_wav(audio)
    elif response_format == "pcm" and len(audio) > 48000 * 2 * MAX_AUDIO_SECONDS:
        raise ValueError("Sentez yanıtı 120 saniyelik istemci sınırını aşıyor.")
