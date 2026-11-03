"""Validated, additive desktop voice preferences; never stores credentials."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class VoiceSettings:
    enabled: bool = False
    interaction_mode: str = "push_to_talk"
    asr_provider: str = "evren"
    tts_provider: str = "evren"
    tts_model: str = "voxcpm2"
    tts_voice: str = "default"
    tts_format: str = "wav"
    vad_enabled: bool = False
    retain_audio: bool = False
    playback_speed: float = 1.0
    read_replies: bool = False
    stream_replies: bool = False
    completion_chime: bool = False
    model: str = "qwen3-asr-1.7b"
    language: str = "auto"
    max_record_seconds: int = 60
    input_device: str | int | None = None
    output_device: str | int | None = None
    input_gain: float = 1.0
    output_volume: float = 1.0
    sample_rate: int = 16000
    asr_request_seconds: int = 30
    vad_engine: str = "energy"
    vad_pre_roll_ms: int = 200
    speech_rate: int = 175
    auto_send: bool = False
    review_before_send: bool = True
    shortcut: str = "<Control-Shift-v>"
    barge_in: bool = False
    vad_threshold: int = 350
    vad_silence_ms: int = 700
    vad_min_speech_ms: int = 200
    wake_word_enabled: bool = False

    def __post_init__(self) -> None:
        for name in ("enabled", "read_replies", "stream_replies", "completion_chime", "auto_send", "review_before_send", "barge_in", "wake_word_enabled", "vad_enabled", "retain_audio"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"Ses ayarı {name} doğru veya yanlış olmalıdır.")
        if not isinstance(self.model, str) or not self.model.strip() or len(self.model) > 200:
            raise ValueError("Geçerli bir ses çözümleme modeli seçin.")
        if self.interaction_mode not in ("push_to_talk", "conversation"):
            raise ValueError("Ses etkileşim modu push_to_talk veya conversation olmalıdır.")
        if self.asr_provider not in ("evren", "local"):
            raise ValueError("Ses çözümleme sağlayıcısı evren veya local olmalıdır.")
        if self.tts_provider not in ("evren", "system"):
            raise ValueError("Seslendirme sağlayıcısı evren veya system olmalıdır.")
        if self.asr_provider == "evren" and self.model.strip() != "qwen3-asr-1.7b":
            raise ValueError("Evren ses çözümlemesi için qwen3-asr-1.7b seçin; sohbet modelleri ASR modeli değildir.")
        if self.tts_model != "voxcpm2":
            raise ValueError("Evren seslendirmesi için voxcpm2 seçin; sohbet modelleri TTS modeli değildir.")
        if self.tts_voice != "default":
            raise ValueError("Evren için doğrulanmış ses seçeneği default değeridir.")
        if self.tts_format not in ("mp3", "wav", "flac", "opus", "pcm"):
            raise ValueError("Geçersiz Evren ses biçimi.")
        if self.retain_audio:
            raise ValueError("Kalıcı ses saklama desteklenmiyor; retain_audio false olmalıdır.")
        if self.language not in ("auto", "tr", "en"):
            raise ValueError("Ses dili auto, tr veya en olmalıdır.")
        if (isinstance(self.max_record_seconds, bool) or not isinstance(self.max_record_seconds, int)
                or not 5 <= self.max_record_seconds <= 120):
            raise ValueError("Mikrofon kayıt sınırı 5 ile 120 saniye arasında olmalıdır.")
        if (isinstance(self.speech_rate, bool) or not isinstance(self.speech_rate, int)
                or not 80 <= self.speech_rate <= 350):
            raise ValueError("Konuşma hızı 80 ile 350 arasında olmalıdır.")
        if not isinstance(self.shortcut, str) or not self.shortcut or len(self.shortcut) > 80:
            raise ValueError("Geçerli bir mikrofon kısayolu seçin.")
        # A single press/release keyboard event can represent a held control.
        import re
        if not re.fullmatch(r"<(?:Control-|Alt-|Shift-|Command-|Option-)*(?:[A-Za-z0-9]|space|F(?:[1-9]|1[0-9]|2[0-4]))>", self.shortcut):
            raise ValueError("Kısayol tek bir klavye birleşimi olmalıdır; örnek: <Control-Shift-v>.")
        for name, low, high in (("vad_threshold", 40, 10000), ("vad_silence_ms", 250, 3000), ("vad_min_speech_ms", 100, 1000)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"Geçersiz ses etkinliği ayarı: {name}.")
        if self.wake_word_enabled:
            raise ValueError("Yerel uyandırma sözcüğü doğrulanmadı; bu özellik henüz etkinleştirilemez.")
        import math
        for name, low, high in (("input_gain", 0.1, 4.0), ("output_volume", 0.0, 1.0), ("playback_speed", 0.25, 4.0)):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(f"Geçersiz ses düzeyi: {name}.")
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int) or self.sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("Desteklenen örnekleme hızını seçin.")
        for name, low, high in (("asr_request_seconds", 1, 30), ("vad_pre_roll_ms", 0, 1000)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"Geçersiz ses sınırı: {name}.")
        if self.vad_engine not in ("energy", "webrtc"):
            raise ValueError("Ses algılayıcı energy veya webrtc olmalıdır.")
        for device in (self.input_device, self.output_device):
            if (device is not None and (isinstance(device, bool) or not isinstance(device, (str, int))
                                  or isinstance(device, int) and device < 0
                                  or isinstance(device, str) and (not device.strip() or len(device) > 200))):
                raise ValueError("Geçerli bir ses aygıtı seçin.")
        object.__setattr__(self, "model", self.model.strip())

    # Keep one stored value for each legacy preference. Properties prevent
    # dataclasses.replace(settings, model=...) from leaving a second alias stale.
    @property
    def asr_model(self) -> str:
        return self.model

    @property
    def speak_responses(self) -> bool:
        return self.read_replies

    @property
    def push_to_talk_shortcut(self) -> str:
        return self.shortcut

    @property
    def max_utterance_duration(self) -> int:
        return self.max_record_seconds

    @property
    def silence_timeout(self) -> float:
        """Seconds of silence; the legacy capture preference uses milliseconds."""
        return self.vad_silence_ms / 1000

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> VoiceSettings:
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("Ses ayarları bir ayar nesnesi olmalıdır.")
        values = {key: value[key] for key in cls.__dataclass_fields__ if key in value}
        for canonical, legacy in (("asr_model", "model"), ("speak_responses", "read_replies"),
                ("push_to_talk_shortcut", "shortcut"), ("max_utterance_duration", "max_record_seconds")):
            if canonical in value:
                if legacy in value and (type(value[legacy]) is not type(value[canonical])
                        or value[legacy] != value[canonical]):
                    raise ValueError(f"Çelişen ses ayarları: {canonical} / {legacy}.")
                values[legacy] = value[canonical]
        if "silence_timeout" in value:
            import math
            seconds = value["silence_timeout"]
            if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                    or not math.isfinite(seconds) or not 0.25 <= seconds <= 3):
                raise ValueError("Sessizlik süresi 0.25 ile 3 saniye arasında olmalıdır.")
            milliseconds = round(seconds * 1000)
            if "vad_silence_ms" in value and (type(value["vad_silence_ms"]) is not int
                    or value["vad_silence_ms"] != milliseconds):
                raise ValueError("Çelişen sessizlik süresi ayarları.")
            values["vad_silence_ms"] = milliseconds
        # Existing voice sections used OS speech. Preserve that explicit local
        # behavior until the user selects Evren; fresh settings use Evren.
        if "tts_provider" not in value:
            values["tts_provider"] = "system"
        # Ignore future keys without coercing strings such as 'false' to bool.
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "asr_model": self.asr_model,
                "speak_responses": self.speak_responses,
                "push_to_talk_shortcut": self.push_to_talk_shortcut,
                "max_utterance_duration": self.max_utterance_duration,
                "silence_timeout": self.silence_timeout}
