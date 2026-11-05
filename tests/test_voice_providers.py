"""Capability, cost, cancellation and real speech pipeline regressions."""
import asyncio
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import logging
from types import SimpleNamespace
import threading
import time

import httpx
import pytest

from firik_agent.api.audio import SpeechResult, TranscriptionResult
from firik_agent.api.client import FirikAPI, FirikAPIError
from firik_agent.ui.service import FirikService
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.controller import VoiceController
from firik_agent.voice.errors import VoiceAudioError, VoiceSynthesisError
from firik_agent.voice.providers import (SpeechToTextProvider, TextToSpeechProvider,
    EvrenSpeechToTextProvider, EvrenTextToSpeechProvider, SystemTextToSpeechProvider,
    ProviderSynthesizer, VoiceRequestGate, evren_capabilities, conversational_models)
from firik_agent.voice.transcription import TranscriptionPolicy
from test_evren_audio_api import wav_audio


def test_catalogue_requires_explicit_task_and_never_infers_audio_direction():
    rows = {"data": [{"id": "qwen3-asr-1.7b", "task": "audio_transcription"},
        {"id": "voxcpm2", "task": "tts"}, {"id": "auto", "modalities": ["audio"]},
        {"id": "dots-ocr", "task": "ocr"}, {"id": "clef", "task": "classification"},
        {"id": "new-asr", "task": "audio_transcription"}]}
    asr = evren_capabilities("asr", rows, authenticated=True)
    tts = evren_capabilities("tts", rows, authenticated=True)
    assert asr.models == ("qwen3-asr-1.7b", "new-asr")
    assert asr.available_models == asr.models
    assert tts.models == ("voxcpm2",) and tts.contract_verified
    assert not tts.languages_verified and not tts.voice_cloning and not tts.streaming
    for model in ("auto", "voxcpm2", "dots-ocr", "clef", "qwen3-embedding-8b"):
        with pytest.raises(VoiceAudioError):
            asr.require_model(model)
    with pytest.raises(VoiceAudioError):
        evren_capabilities("tts", {"data": []}, authenticated=True).require_model("voxcpm2")
    # A name alone is a configured preference, never language/runtime evidence.
    unknown = evren_capabilities("tts")
    assert unknown.available_models is None and not unknown.authenticated


@pytest.mark.parametrize("tasks", [("embedding", "embedding"), ("embedding", "audio_transcription"), ("audio_transcription", "embedding")])
def test_catalogue_handles_duplicate_conflicting_rows_conservatively(tasks):
    caps = evren_capabilities("asr", {"data": [
        {"id": "qwen3-asr-1.7b", "task": task} for task in tasks]}, authenticated=True)
    assert not caps.models


def test_conversational_catalogue_filters_voice_and_specialized_models():
    models = ["glm-5.3", "qwen3.8-flash-next", "qwen3-asr-1.7b", "voxcpm2", "auto", "clef", "other-voice"]
    assert conversational_models(models, {"other-voice": {"task": "tts"}}) == ["glm-5.3", "qwen3.8-flash-next", "auto"]


@pytest.mark.asyncio
async def test_provider_contracts_and_independent_request_parameters():
    calls = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def transcribe_bytes_normalized(self, audio, model, **options):
            calls.append(("asr", audio, model, options))
            return TranscriptionResult("İstanbul", "tr", .5)
        async def synthesize(self, text, model, **options):
            calls.append(("tts", text, model, options))
            return SpeechResult(b"audio", "audio/wav", "wav")
    asr = EvrenSpeechToTextProvider(Client, endpoint="https://evren.example/v1")
    tts = EvrenTextToSpeechProvider(Client, endpoint="https://evren.example/v1")
    assert isinstance(asr, SpeechToTextProvider) and isinstance(tts, TextToSpeechProvider)
    assert isinstance(SystemTextToSpeechProvider(), TextToSpeechProvider)
    result = await asr.transcribe(b"validated-by-client", language="auto")
    assert result.language == "tr" and result.duration == .5
    await tts.synthesize("Iğdır, çığ, özgürlük. Hello!", language="tr", speed=1.2)
    assert calls == [("asr", b"validated-by-client", "qwen3-asr-1.7b", {"response_format": "json"}),
        ("tts", "Iğdır, çığ, özgürlük. Hello!", "voxcpm2", {"voice": "default", "response_format": "wav", "speed": 1.2})]
    with pytest.raises(VoiceAudioError):
        await asr.transcribe(b"audio", model="auto")
    with pytest.raises(VoiceSynthesisError):
        await tts.synthesize("text", voice="clone")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_segmented_utterance_reuses_client_and_cancel_leaves_tts_running(caplog):
    caplog.set_level(logging.INFO, logger="firik_agent.voice.telemetry")
    segment_started, tts_started, release_tts = asyncio.Event(), asyncio.Event(), asyncio.Event()
    clients, requests, closed = [], [], []
    endpoint = "https://private-endpoint.example/v1"
    gate = VoiceRequestGate()

    class Transport(httpx.MockTransport):
        async def aclose(self):
            closed.append(self)

    async def respond(request):
        path = request.url.path
        requests.append(path)
        if path == "/v1/audio/speech":
            tts_started.set()
            await release_tts.wait()
            return httpx.Response(200, content=wav_audio(), headers={"content-type": "audio/wav"})
        if requests.count(path) == 2:
            segment_started.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"text": "private transcript", "language": "en"})

    def client_factory():
        transport = Transport(respond)
        clients.append(transport)
        return FirikAPI("private-key", endpoint, transport=transport)

    asr = EvrenSpeechToTextProvider(client_factory, endpoint=endpoint, gate=gate)
    tts = EvrenTextToSpeechProvider(client_factory, endpoint=endpoint, gate=gate)
    asr_task = asyncio.create_task(asr.transcribe_utterance(wav_audio(.3), model="qwen3-asr-1.7b",
        language="en", policy=TranscriptionPolicy(max_request_seconds=.1)))
    tts_task = asyncio.create_task(tts.synthesize("private prompt"))
    try:
        await asyncio.wait_for(segment_started.wait(), 1)
        await asyncio.wait_for(tts_started.wait(), 1)
        assert len(clients) == 2
        asr_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asr_task
        assert closed == clients[:1]
        assert requests.count("/v1/audio/transcriptions") == 2
        assert not tts_task.done()
        release_tts.set()
        assert (await tts_task).audio == wav_audio()
        assert closed == clients
    finally:
        for task in (asr_task, tts_task):
            task.cancel()
        await asyncio.gather(asr_task, tts_task, return_exceptions=True)

    assert "stage=asr_request status=ok" in caplog.text
    assert "stage=asr_request status=cancelled" in caplog.text
    assert "stage=tts_request status=ok" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429, 500, 503])
async def test_failure_message_never_displays_secret_or_retries(status):
    calls = []
    gate = VoiceRequestGate()
    async def request():
        calls.append(1)
        raise FirikAPIError(status, {"message": "SECRET-KEY"}, {"retry-after": "2"})
    with pytest.raises(VoiceAudioError) as error:
        await gate.run("endpoint", "tts", request)
    assert "SECRET-KEY" not in str(error.value) and len(calls) == 1
    with pytest.raises(VoiceAudioError, match="bekleme"):
        await gate.run("endpoint", "asr", request)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_after_deadline_and_no_automatic_retry():
    now = [100.0]
    gate = VoiceRequestGate(clock=lambda: now[0])
    calls = []
    async def rejected():
        calls.append(1)
        raise FirikAPIError(429, {}, {"retry-after": "5"})
    with pytest.raises(VoiceAudioError):
        await gate.run("origin", "asr", rejected)
    now[0] = 104.9
    with pytest.raises(VoiceAudioError, match="1 saniye"):
        await gate.run("origin", "tts", rejected)
    now[0] = 105
    assert await gate.run("origin", "tts", lambda: asyncio.sleep(0, result="ok")) == "ok"
    assert calls == [1]


@pytest.mark.asyncio
async def test_retry_after_http_date_and_concurrent_budget():
    gate = VoiceRequestGate()
    started, release = asyncio.Event(), asyncio.Event()
    async def waiting():
        started.set()
        await release.wait()
    pending = asyncio.create_task(gate.run("origin", "asr", waiting))
    await started.wait()
    with pytest.raises(VoiceAudioError, match="zaten sürüyor"):
        await gate.run("origin", "asr", waiting)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError): await pending
    assert not gate._active
    async def busy():
        raise FirikAPIError(503, {}, {"retry-after": format_datetime(datetime.now(timezone.utc) + timedelta(minutes=1))})
    with pytest.raises(VoiceAudioError): await gate.run("origin", "tts", busy)
    with pytest.raises(VoiceAudioError, match="bekleme"):
        await gate.run("origin", "tts", waiting)


def test_provider_synthesis_bridge_cancels_pending_request_and_describes_raw_pcm():
    future = Future()
    submitted = threading.Event()
    class Runtime:
        def run_coroutine(self, coroutine):
            coroutine.close()
            submitted.set()
            return future
    class Provider:
        async def synthesize(self, *args, **kwargs): pass
    cancel = threading.Event()
    bridge = ProviderSynthesizer(Runtime(), Provider(), model="voxcpm2")
    output = []
    worker = threading.Thread(target=lambda: output.append(bridge.synthesize("Merhaba", cancel)))
    worker.start()
    assert submitted.wait(1)
    cancel.set()
    worker.join(1)
    assert not worker.is_alive() and future.cancelled() and output == [None]
    future = Future()
    future.set_result(SpeechResult(b"\0\0" * 20, "audio/pcm", "pcm", {}, 48000, 1))
    audio = bridge.synthesize("Merhaba", threading.Event())
    assert audio.raw_format.sample_rate == 48000 and audio.raw_format.channels == 1


def test_service_speech_factory_preserves_session_model_and_snapshots_endpoint_credentials(monkeypatch):
    calls = []
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda provider, cfg, **kw: calls.append((provider, cfg, kw)) or "old-key")
    service = object.__new__(FirikService)
    service.config = {"providers": {"firik": {"api_key_env": "OLD_ENV"}}}
    service.base_url, service.timeout, service.default_model = "https://old.example/v1", 30, "glm-5.3"
    provider = service._voice_provider("tts")
    service.base_url = "https://new.example/v1"
    service.config["providers"]["firik"]["api_key_env"] = "NEW_ENV"
    client = provider._client_factory()
    assert client.api_key == "old-key" and client.base_url == "https://old.example"
    assert calls == [("firik", {"api_key_env": "OLD_ENV"}, {"base_url": "https://old.example/v1"})]
    asyncio.run(client.close())
    assert service.default_model == "glm-5.3"


def test_evren_endpoint_is_independent_of_active_llm_provider(monkeypatch):
    lookups = []
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda provider, cfg, **kw: lookups.append((provider, kw)) or "evren-key")
    service = object.__new__(FirikService)
    service.config = {"providers": {"firik": {"base_url": "https://evren.example/v1"}}}
    service.base_url, service.provider, service.timeout = "https://other-llm.example/v1", "openai", 20
    client = service._voice_provider("asr")._client_factory()
    assert client.base_url == "https://evren.example"
    assert lookups == [("firik", {"base_url": "https://evren.example/v1"})]
    asyncio.run(client.close())


@pytest.mark.asyncio
async def test_system_async_adapter_waits_for_native_cancel_cleanup():
    started, cleaning, release = threading.Event(), threading.Event(), threading.Event()
    class Native:
        def synthesize(self, text, cancel, language, rate):
            started.set()
            cancel.wait(1)
            cleaning.set()
            release.wait(1)
    task = asyncio.create_task(SystemTextToSpeechProvider(Native()).synthesize("Merhaba"))
    assert await asyncio.to_thread(started.wait, 1)
    task.cancel()
    assert await asyncio.to_thread(cleaning.wait, 1)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task


@pytest.mark.asyncio
async def test_discovery_rejects_result_from_replaced_credentials(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr("firik_agent.ui.service.get_api_key", lambda *args, **kwargs: "old-key")
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def models(self):
            started.set()
            await release.wait()
            return {"data": [{"id": "voxcpm2", "task": "tts"}]}
    monkeypatch.setattr("firik_agent.ui.service.FirikAPI", Client)
    tasks, delivered = [], []
    class Runtime:
        def submit(self, coroutine, on_done, on_error):
            async def execute():
                on_done(await coroutine)
            tasks.append(asyncio.create_task(execute()))
    service = object.__new__(FirikService)
    service.config, service.base_url, service.timeout = {}, "https://old.example/v1", 20
    service._voice_revision = 0
    service._voice_catalogue = service._voice_catalogue_url = None
    service._dispatch = lambda callback, *args: callback(*args)
    service.runtime = Runtime()
    service.discover_voice_capabilities_async(delivered.append, delivered.append)
    await started.wait()
    service._voice_revision += 1
    release.set()
    await asyncio.gather(*tasks)
    assert not delivered and service._voice_catalogue is None


def test_actual_speech_queue_surfaces_credit_errors_and_keeps_text_available():
    from firik_agent.core.runtime import AsyncRuntime
    from firik_agent.voice.synthesis import EngineSpeaker
    runtime = AsyncRuntime()
    errors, states, played = [], [], []
    class Provider:
        async def synthesize(self, *args, **kwargs):
            raise VoiceAudioError("Evren ses hizmeti için kredi yetersiz. Metinle devam edin.")
    class Player:
        def play(self, *args): played.append(True)
        def stop(self): pass
        def shutdown(self): pass
    controller = VoiceController(runtime, lambda callback, *args: callback(*args), lambda *args: None,
        replace(VoiceSettings(), enabled=True), states.append, lambda _: None, errors.append,
        speaker=EngineSpeaker(synthesizer=ProviderSynthesizer(runtime, Provider(), model="voxcpm2"), player=Player()))
    try:
        assert controller.speak("Merhaba dünya.")
        deadline = time.monotonic() + 2
        while not errors and time.monotonic() < deadline:
            time.sleep(.01)
        assert errors and "kredi yetersiz" in errors[0]
        assert controller.state_id.value == "error" and not played
        assert not controller.conversation_active
    finally:
        controller.shutdown()
        runtime.shutdown()
