"""Voice uses the existing FIRIK contract, credentials and cancellable runtime."""
import asyncio
import io
import wave
from dataclasses import replace
from types import SimpleNamespace
import weakref

import httpx
import pytest

from firik_agent.api.client import FirikAPI
from firik_agent.ui.service import FirikService
from firik_agent.voice.config import VoiceSettings


def microphone_wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x01\x00" * 3200)
    return output.getvalue()


@pytest.mark.asyncio
async def test_microphone_multipart_uses_documented_endpoint_and_auth():
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url == "https://voice.example/v1/audio/transcriptions"
        assert request.headers["X-API-Key"] == "test-key"
        assert b'filename="microphone.wav"' in request.content
        assert b"Content-Type: audio/wav" in request.content
        assert microphone_wav() in request.content
        assert b"qwen3-asr-1.7b" in request.content
        assert b"\r\ntr\r\n" in request.content
        return httpx.Response(200, json={"text": "Merhaba dünya"})
    async with FirikAPI("test-key", "https://voice.example/v1", transport=httpx.MockTransport(respond)) as client:
        result = await client.transcribe_bytes(microphone_wav(), "qwen3-asr-1.7b", language="tr", response_format="json")
        assert result["text"] == "Merhaba dünya"
        with pytest.raises(ValueError):
            await client.transcribe_bytes(b"", "model")
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{"text": "  İstanbul hello  "}, "  İstanbul hello  "])
async def test_service_reuses_endpoint_and_credentials_without_files(monkeypatch, result):
    calls = []
    class Client:
        def __init__(self, key, base_url, timeout):
            calls.append((key, base_url, timeout))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def transcribe_bytes_normalized(self, audio, model, **options):
            from firik_agent.api.audio import normalize_transcription
            calls.append((audio, model, options))
            return normalize_transcription(result)
    monkeypatch.setattr("firik_agent.ui.service.FirikAPI", Client)
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda *args, **kwargs: "existing-secret")
    service = object.__new__(FirikService)
    service.get_key = lambda: "existing-secret"
    service.config = {}
    service.base_url, service.timeout = "https://existing.example/v1", 180
    audio = microphone_wav()
    assert await service._transcribe_microphone(audio, "qwen3-asr-1.7b", "auto") == "İstanbul hello"
    assert calls == [("existing-secret", service.base_url, 120),
                     (audio, "qwen3-asr-1.7b", {"response_format": "json"})]


@pytest.mark.asyncio
async def test_service_transcription_cancel_closes_http_client(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.set()
        async def transcribe_bytes_normalized(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()
    monkeypatch.setattr("firik_agent.ui.service.FirikAPI", Client)
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda *args, **kwargs: "secret")
    service = object.__new__(FirikService)
    service.get_key = lambda: "secret"
    service.config = {}
    service.base_url, service.timeout = "https://existing.example/v1", 20
    task = asyncio.create_task(service._transcribe_microphone(microphone_wav(), "qwen3-asr-1.7b", "en"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


def test_voice_settings_additive_persistence_and_disable_cancels(tmp_path):
    import yaml
    service = object.__new__(FirikService)
    service.config = {"providers": {"firik": {"default_model": "preserve"}}, "voice": {"enabled": "invalid"}}
    service.config_path = tmp_path / "config.yaml"
    cancelled = []
    class Controller:
        def cancel(self):
            cancelled.append(True)
    controller = Controller()
    service._voice_controllers = weakref.WeakSet([controller])
    assert service.voice_settings == VoiceSettings()
    service.update_voice_settings(replace(VoiceSettings(), language="tr"))
    saved = yaml.safe_load(service.config_path.read_text())
    assert saved["providers"] == service.config["providers"]
    assert saved["voice"]["language"] == "tr"
    assert controller.settings == service.voice_settings
    assert cancelled == [True]


def test_endpoint_change_invalidates_voice_before_mutating_provider(tmp_path):
    service = object.__new__(FirikService)
    service.config_path = tmp_path / "config.yaml"
    service.config = {"providers": {"firik": {}}}
    service.provider, service.base_url = "firik", "https://old.example/v1"
    cancelled = []
    class Controller:
        def cancel(self):
            cancelled.append(service.base_url)
    controller = Controller()
    service._voice_controllers = weakref.WeakSet([controller])
    service.update_settings("https://new.example/v1", "model", 30)
    assert cancelled == ["https://old.example/v1"]
    assert service.base_url == "https://new.example/v1"


def test_service_controllers_share_one_application_capture_owner():
    from firik_agent.voice.controller import CaptureCoordinator
    service = object.__new__(FirikService)
    service.runtime = SimpleNamespace()
    service._dispatch = lambda *args: None
    service.config = {"voice": {"enabled": True}}
    service._voice_controllers = weakref.WeakSet()
    first = service.create_voice_controller(on_state=lambda _: None, on_transcript=lambda _: None,
                                            on_error=lambda _: None, session_id="first")
    second = service.create_voice_controller(on_state=lambda _: None, on_transcript=lambda _: None,
                                             on_error=lambda _: None, session_id="second")
    try:
        assert isinstance(first.coordinator, CaptureCoordinator)
        assert first.coordinator is second.coordinator is service._voice_capture
        assert first.session_id == "first" and second.session_id == "second"
        assert first.coordinator.claim(first)
        assert not second.coordinator.claim(second)
        first.stop_listening()
        assert second.coordinator.claim(second)
    finally:
        first.close()
        second.close()


def test_frozen_smoke_uses_disposable_profile_and_disables_default_mcps(monkeypatch):
    import sys
    import yaml
    from firik_agent.ui import app as module
    profiles = []
    class App:
        def __init__(self, *, config_path):
            profiles.append(config_path)
            config = yaml.safe_load(config_path.read_text())
            assert config["disabled_default_mcps"] == ["computer-use", "web"]
            assert config["providers"]["firik"]["base_url"] == "http://127.0.0.1:9/v1"
            assert config["providers"]["firik"]["api_key_env"] != "FIRIK_API_KEY"
        def after(self, delay, callback):
            assert delay == 250
        def destroy(self):
            pass
        def mainloop(self):
            pass
    monkeypatch.setattr(module, "FirikApp", App)
    monkeypatch.setattr(sys, "argv", ["firik-gui", "--smoke-test"])
    assert module.main() == 0
    assert profiles and not profiles[0].exists()
