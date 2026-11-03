"""Speech contracts independent of chat models, with explicit evidence levels.

The assigned defaults come from the user's inventory. A model name is never
evidence for an endpoint, voice, language quality, or runtime availability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import asyncio
import math
import threading
import time
from typing import Any, Callable, Protocol, runtime_checkable

import httpx

from firik_agent.api.client import AudioRequestError, FirikAPIError, api_schema
from firik_agent.api.audio import SpeechResult, TranscriptionResult
from firik_agent.voice.errors import VoiceAudioError, VoiceSynthesisError


def conversational_models(models: list[str], metadata: dict[str, dict]) -> list[str]:
    """Filter the shared catalog without changing a selected session model."""
    speech_or_specialized = {"clef", "laya", "deepseek-ocr-2", "dots-ocr", "qwen3-asr-1.7b",
        "qwen3-embedding-8b", "qwen3-guard-4b", "qwen3-reranker-8b", "voxcpm2"}
    return [model for model in models if model not in speech_or_specialized
            and metadata.get(model, {}).get("task") in (None, "chat", "vision_chat")]


@dataclass(frozen=True)
class SpeechCapabilities:
    provider: str
    operation: str
    models: tuple[str, ...]
    endpoint: str | None
    contract_verified: bool
    authenticated: bool = False
    available_models: tuple[str, ...] | None = None
    language_options: tuple[str, ...] = ("auto", "tr", "en")
    languages_verified: bool = False
    voices: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    speed_range: tuple[float, float] | None = None
    streaming: bool = False
    voice_cloning: bool = False

    def require_model(self, model: str) -> None:
        if not self.contract_verified:
            raise VoiceAudioError("Ses hizmetinin API sözleşmesi doğrulanmadı. Metinle devam edebilirsiniz.")
        if model not in self.models:
            raise VoiceAudioError("Seçilen model bu ses işlemi için doğrulanmadı. Uygun bir ses modeli seçin.")
        if self.available_models is not None and model not in self.available_models:
            raise VoiceAudioError("Seçilen ses modeli bu API anahtarıyla kullanılamıyor. Metinle devam edebilirsiniz.")


def evren_capabilities(operation: str, metadata: Any = None, *, authenticated=False) -> SpeechCapabilities:
    """Only the documented `task` field grants a discovered speech capability."""
    if operation not in ("asr", "tts"):
        raise ValueError("Bilinmeyen ses işlemi.")
    path = "/v1/audio/transcriptions" if operation == "asr" else "/v1/audio/speech"
    default = "qwen3-asr-1.7b" if operation == "asr" else "voxcpm2"
    task = "audio_transcription" if operation == "asr" else "tts"
    rows = metadata.get("data") if isinstance(metadata, dict) else metadata
    models = [default]
    available = None
    if authenticated and isinstance(rows, list):
        available = []
        evidence: dict[str, set[str | None]] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            model = row["id"]
            value = row.get("task")
            evidence.setdefault(model, set()).add(value if isinstance(value, str) else None)
        for model, tasks in evidence.items():
            # Conflicting duplicate declarations cannot grant a capability.
            if tasks == {task}:
                if model not in models:
                    models.append(model)
                available.append(model)
            elif model == default and tasks == {None}:
                # Inventory assignment remains a preference; availability alone
                # does not verify a capability that metadata does not describe.
                available.append(model)
            elif model == default:
                if default in models:
                    models.remove(default)
    return SpeechCapabilities("evren", operation, tuple(models), path,
        "post" in api_schema().get("paths", {}).get(path, {}),
        authenticated, tuple(available) if available is not None else None,
        voices=("default",) if operation == "tts" else (),
        formats=("wav", "mp3", "flac", "opus", "pcm") if operation == "tts" else ("json", "text", "srt", "verbose_json", "diarized_json"),
        speed_range=(.25, 4.0) if operation == "tts" else None)


@runtime_checkable
class SpeechToTextProvider(Protocol):
    def capabilities(self) -> SpeechCapabilities: ...
    async def transcribe(self, audio: bytes, *, model: str, language: str = "auto") -> TranscriptionResult: ...


@runtime_checkable
class TextToSpeechProvider(Protocol):
    def capabilities(self) -> SpeechCapabilities: ...
    async def synthesize(self, text: str, *, model: str, language: str = "auto",
                         voice: str = "default", response_format: str = "wav", speed: float = 1.0) -> SpeechResult: ...


def voice_error_message(error: Exception) -> str:
    """Never display raw upstream bodies, headers, URLs, or credentials."""
    if isinstance(error, VoiceAudioError):
        return str(error)
    if isinstance(error, AudioRequestError):
        return str(error)
    if isinstance(error, FirikAPIError):
        status = error.status_code
        if status == 401:
            return "Ses hizmeti kimlik doğrulaması başarısız. Ayarlar bölümündeki Evren API anahtarını kontrol edin."
        if status == 402:
            return "Evren ses hizmeti için kredi yetersiz. Bakiyenizi kontrol edin veya metinle devam edin."
        if status == 403:
            return "Evren ses hizmetine erişim izni yok. Anahtar yetkilerini ve kullanım şartlarını kontrol edin."
        if status == 429:
            return "Evren ses hizmetinin hız sınırı aşıldı. Bekleme süresinden sonra elle yeniden deneyin."
        if status >= 500:
            return "Evren ses hizmeti şu anda yanıt veremiyor. Metinle devam edebilir, daha sonra yeniden deneyebilirsiniz."
        return "Evren ses isteği reddedildi. Ses biçimini ve model ayarlarını kontrol edin."
    if isinstance(error, httpx.TimeoutException):
        return "Ses isteği zaman aşımına uğradı; ücret oluşmuş olabilir. Otomatik tekrar yapılmadı. Metinle devam edebilirsiniz."
    if isinstance(error, httpx.HTTPError):
        return "Evren ses hizmetine bağlanılamadı. Bağlantınızı kontrol edin veya metinle devam edin."
    if isinstance(error, ValueError):
        # Validation errors here originate in our own client, never the server.
        return "Ses isteği veya yanıtı geçersiz. Ses biçimini ve ayarlarını kontrol edin."
    return "Ses işlemi tamamlanamadı. Metinle devam edebilirsiniz."


class VoiceRequestGate:
    """Shared nonblocking budget: no automatic retries of billable requests.

    Retry-After establishes a deadline for later explicit requests. A missing
    header on 429 uses a conservative 30-second cooldown. At most one ASR and
    one TTS request can be in flight per application service.
    """
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._until: dict[str, float] = {}
        self._active: set[tuple[str, str]] = set()

    async def run(self, endpoint: str, operation: str, request):
        ticket = (endpoint, operation)
        with self._lock:
            remaining = self._until.get(endpoint, 0) - self._clock()
            if remaining > 0:
                raise VoiceAudioError(f"Ses hizmeti bekleme süresinde. {math.ceil(remaining)} saniye sonra yeniden deneyin; metinle devam edebilirsiniz.")
            if ticket in self._active:
                raise VoiceAudioError("Bir ses isteği zaten sürüyor. Tamamlanmasını bekleyin veya metinle devam edin.")
            self._active.add(ticket)
        try:
            return await request()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if isinstance(error, FirikAPIError):
                value = error.metadata.get("retry-after")
                delay = 30.0 if error.status_code == 429 else 0.0
                if value:
                    try:
                        seconds = float(value)
                        if math.isfinite(seconds) and seconds >= 0:
                            delay = seconds
                    except (TypeError, ValueError):
                        try:
                            date = parsedate_to_datetime(value)
                            if date.tzinfo is None:
                                date = date.replace(tzinfo=timezone.utc)
                            delay = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            pass
                if delay > 0:
                    with self._lock:
                        self._until[endpoint] = max(self._until.get(endpoint, 0), self._clock() + delay)
            raise VoiceAudioError(voice_error_message(error)) from None
        finally:
            with self._lock:
                self._active.discard(ticket)


class EvrenSpeechToTextProvider:
    def __init__(self, client_factory, *, endpoint: str, gate=None, capabilities=None):
        self._client_factory = client_factory
        self.endpoint = endpoint
        self.gate = gate or VoiceRequestGate()
        self._capabilities = capabilities or evren_capabilities("asr")

    def capabilities(self):
        return self._capabilities

    async def transcribe(self, audio, *, model="qwen3-asr-1.7b", language="auto"):
        self.capabilities().require_model(model)
        if language not in self.capabilities().language_options:
            raise VoiceAudioError("Ses dili auto, tr veya en olmalıdır.")
        async def request():
            async with self._client_factory() as client:
                return await client.transcribe_bytes_normalized(audio, model, response_format="json",
                    **({"language": language} if language != "auto" else {}))
        return await self.gate.run(self.endpoint, "asr", request)


class EvrenTextToSpeechProvider:
    def __init__(self, client_factory, *, endpoint: str, gate=None, capabilities=None):
        self._client_factory = client_factory
        self.endpoint = endpoint
        self.gate = gate or VoiceRequestGate()
        self._capabilities = capabilities or evren_capabilities("tts")

    def capabilities(self):
        return self._capabilities

    async def synthesize(self, text, *, model="voxcpm2", language="auto", voice="default", response_format="wav", speed=1.0):
        capabilities = self.capabilities()
        capabilities.require_model(model)
        if language not in capabilities.language_options or voice not in capabilities.voices or response_format not in capabilities.formats:
            raise VoiceSynthesisError("Desteklenen ses dili, ses ve çıktı biçimini seçin.")
        if (isinstance(speed, bool) or not isinstance(speed, (float, int)) or not math.isfinite(speed)
                or not .25 <= speed <= 4):
            raise VoiceSynthesisError("Konuşma hızı 0,25 ile 4 arasında olmalıdır.")
        if not isinstance(text, str) or not text.strip() or len(text) > 1600:
            raise VoiceSynthesisError("Seslendirilecek cümle boş veya çok uzun.")
        async def request():
            async with self._client_factory() as client:
                # The verified endpoint has no language parameter. Preserve the
                # original UTF-8 text; do not fabricate a language/style prompt.
                return await client.synthesize(text, model, voice=voice, response_format=response_format, speed=speed)
        return await self.gate.run(self.endpoint, "tts", request)


class SystemTextToSpeechProvider:
    """Explicit local choice, using installed voices; never a remote fallback."""
    def __init__(self, synthesizer=None):
        self._synthesizer = synthesizer

    def capabilities(self):
        return SpeechCapabilities("system", "tts", ("system",), None, True,
            voices=(), formats=("wav",), speed_range=(.25, 4.0))

    def synthesize_blocking(self, text, cancel, language="auto", rate=175, *, speed=1.0):
        from firik_agent.voice.synthesis import NativeSynthesizer
        synthesizer = self._synthesizer or NativeSynthesizer()
        return synthesizer.synthesize(text, cancel, language, max(80, min(350, round(rate * speed))))

    async def synthesize(self, text, *, model="system", language="auto", voice="default", response_format="wav", speed=1.0):
        if model != "system" or voice != "default" or response_format != "wav" or language not in ("auto", "tr", "en"):
            raise VoiceSynthesisError("Sistem seslendirmesi için kurulu bir ses ve WAV biçimi gerekir.")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not .25 <= speed <= 4:
            raise VoiceSynthesisError("Geçersiz konuşma hızı.")
        cancel = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(self.synthesize_blocking, text, cancel, language, speed=speed))
        try:
            audio = await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel.set()
            # Do not abandon native subprocess cleanup on cancellation.
            await asyncio.shield(task)
            raise
        if audio is None:
            raise VoiceSynthesisError("Sistem seslendirmesi durduruldu.")
        return SpeechResult(audio.data, "audio/wav", "wav", {})


class SystemProviderSynthesizer:
    """Keep native cleanup on the owning speech worker before releasing it."""
    supports_streaming = False

    def __init__(self, provider: SystemTextToSpeechProvider, *, speed=1.0):
        self.provider, self.speed = provider, speed

    def synthesize(self, text, cancel, language="auto", rate=175):
        return self.provider.synthesize_blocking(text, cancel, language, rate, speed=self.speed)


class ProviderSynthesizer:
    """Cancellable worker-to-async bridge for the existing bounded speech queue."""
    supports_streaming = False

    def __init__(self, runtime, provider: TextToSpeechProvider, *, model, voice="default", response_format="wav", speed=1.0):
        self.runtime, self.provider = runtime, provider
        self.model, self.voice, self.response_format, self.speed = model, voice, response_format, speed

    def synthesize(self, text, cancel, language="auto", rate=175):
        from concurrent.futures import CancelledError, TimeoutError
        from firik_agent.voice.playback import EncodedAudio
        from firik_agent.voice.models import AudioFormat
        if cancel.is_set():
            return None
        coroutine = self.provider.synthesize(text, model=self.model, language=language,
            voice=self.voice, response_format=self.response_format, speed=self.speed)
        try:
            future = self.runtime.run_coroutine(coroutine)
        except Exception:
            coroutine.close()
            raise VoiceSynthesisError("Ses hizmeti başlatılamadı; metinle devam edebilirsiniz.") from None
        try:
            while not cancel.is_set():
                try:
                    result = future.result(timeout=.05)
                    raw = AudioFormat(result.sample_rate, result.channels, 2) if result.response_format == "pcm" else None
                    return EncodedAudio(result.audio, result.content_type, raw)
                except TimeoutError:
                    if future.done():
                        raise
            future.cancel()
            return None
        except CancelledError:
            return None
        except Exception as error:
            raise VoiceSynthesisError(voice_error_message(error)) from None
