"""Voice preferences migrate without coupling audio choices to chat models."""
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from firik_agent.voice.config import VoiceSettings


def test_fresh_voice_defaults_are_disabled_independent_evren_models():
    settings = VoiceSettings.from_mapping(None)
    assert not settings.enabled and not settings.speak_responses
    assert not settings.auto_send and not settings.vad_enabled
    assert not settings.wake_word_enabled and not settings.retain_audio
    assert settings.asr_provider == settings.tts_provider == "evren"
    assert settings.asr_model == "qwen3-asr-1.7b"
    assert settings.tts_model == "voxcpm2"
    assert settings.tts_voice == "default" and settings.tts_format == "wav"
    assert settings.language == "auto" and settings.playback_speed == 1


def test_existing_voice_section_keeps_explicit_system_speech_on_migration():
    settings = VoiceSettings.from_mapping({"enabled": True, "read_replies": True,
        "model": "qwen3-asr-1.7b", "max_record_seconds": 30, "vad_silence_ms": 900})
    assert settings.tts_provider == "system" and settings.speak_responses
    assert settings.max_utterance_duration == 30 and settings.silence_timeout == .9
    assert VoiceSettings.from_mapping(settings.to_dict()) == settings


def test_canonical_settings_and_legacy_replace_share_single_values():
    settings = VoiceSettings.from_mapping({"asr_provider": "evren", "asr_model": "qwen3-asr-1.7b",
        "tts_provider": "evren", "tts_model": "voxcpm2", "speak_responses": True,
        "push_to_talk_shortcut": "<Alt-space>", "max_utterance_duration": 20,
        "silence_timeout": 1.25, "interaction_mode": "conversation", "vad_enabled": True})
    assert settings.read_replies and settings.shortcut == "<Alt-space>"
    assert settings.max_record_seconds == 20 and settings.vad_silence_ms == 1250
    updated = replace(settings, read_replies=False, max_record_seconds=10, vad_silence_ms=500)
    assert not updated.speak_responses
    assert updated.max_utterance_duration == 10 and updated.silence_timeout == .5
    assert VoiceSettings.from_mapping(updated.to_dict()) == updated


@pytest.mark.parametrize("mapping", [
    {"asr_model": "auto"}, {"asr_model": "deepseek-ocr-2"}, {"asr_model": "voxcpm2"},
    {"asr_model": "qwen3-embedding-8b"}, {"asr_model": "clef"},
    {"tts_model": "qwen3-asr-1.7b"}, {"tts_model": "glm-5.3"},
    {"asr_provider": "unrequested-remote"}, {"tts_provider": "unrequested-remote"},
    {"tts_voice": "cloned-voice"}, {"tts_format": "unknown"},
    {"speak_responses": "false"}, {"vad_enabled": "false"}, {"retain_audio": True},
    {"retain_audio": "false"}, {"interaction_mode": "always_listening"},
    {"max_utterance_duration": True}, {"max_utterance_duration": 121},
    {"silence_timeout": True}, {"silence_timeout": 0}, {"silence_timeout": float("nan")},
    {"playback_speed": 0}, {"playback_speed": 4.1}, {"playback_speed": float("inf")},
    {"playback_speed": True}, {"push_to_talk_shortcut": "<Button-1>"},
    {"asr_model": "qwen3-asr-1.7b", "model": "auto"},
    {"silence_timeout": .7, "vad_silence_ms": 800},
    {"speak_responses": True, "read_replies": False},
])
def test_invalid_or_conflicting_voice_configuration_is_rejected(mapping):
    with pytest.raises(ValueError):
        VoiceSettings.from_mapping(mapping)


def test_shipped_configuration_loads_all_canonical_preferences():
    config = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text(encoding="utf-8"))
    settings = VoiceSettings.from_mapping(config["voice"])
    assert settings == VoiceSettings()
    assert config["providers"]["firik"]["default_model"] != settings.asr_model
    assert config["providers"]["firik"]["default_model"] != settings.tts_model


@pytest.mark.parametrize("response_format", ["mp3", "wav", "flac", "opus", "pcm"])
def test_only_documented_speech_formats_are_configurable(response_format):
    assert VoiceSettings.from_mapping({"tts_format": response_format}).tts_format == response_format
