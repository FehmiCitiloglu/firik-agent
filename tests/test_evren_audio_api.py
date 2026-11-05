"""Public-contract audio tests; never perform billable inference or use secrets."""
import asyncio
import io
import json
import wave

import httpx
import pytest

from firik_agent.api.audio import (MAX_TRANSCRIPT_BYTES, normalize_transcription,
                                   speech_parameters, transcription_parameters)
from firik_agent.api.client import FirikAPI, FirikAPIError, api_schema
from firik_agent.api.cli import run


def wav_audio(seconds=0.1):
    target = io.BytesIO()
    with wave.open(target, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"\x01\x00" * int(16000 * seconds))
    return target.getvalue()


def test_public_speech_contract_is_allowlisted_without_internal_operations():
    schema = api_schema()
    assert "/v1/audio/speech" in schema["paths"]
    assert not any(path.startswith("/internal/") for path in schema["paths"])
    body = schema["components"]["schemas"]["SpeechBody"]["properties"]
    assert body["response_format"]["enum"] == ["mp3", "wav", "flac", "opus", "pcm"]
    assert body["speed"]["minimum"] == 0.25
    assert body["speed"]["maximum"] == 4
    assert "language" not in body and "reference_audio" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["auto", "tr", "en"])
async def test_normalized_asr_preserves_utf8_and_only_provider_metadata(language):
    def handle(request):
        assert request.method == "POST"
        assert request.headers["x-api-key"] == "secret"
        assert wav_audio() in request.content
        assert b'name="language"' in request.content if language != "auto" else b'name="language"' not in request.content
        return httpx.Response(200, json={"text": "  Iğdır, İstanbul, hello.  "}, headers={"x-request-id": "id"})
    async with FirikAPI("secret", transport=httpx.MockTransport(handle)) as api:
        result = await api.transcribe_bytes_normalized(wav_audio(), language=language)
    assert result.text == "Iğdır, İstanbul, hello."
    assert result.language is result.duration is None
    assert result.metadata == {"headers": {"x-request-id": "id"}, "provider": {}}


def test_normalization_keeps_confidence_and_timing_out_of_text():
    result = normalize_transcription({"text": "Türkçe", "language": "tr", "duration": 1.5,
                                      "confidence": .72, "segments": [{"start": 0, "text": "Türkçe"}]})
    assert (result.text, result.language, result.duration) == ("Türkçe", "tr", 1.5)
    assert result.metadata["provider"]["confidence"] == .72
    assert normalize_transcription("1\n00:00:00,000 --> 00:00:01,000\nMerhaba\n\n2\n00:00:01,000 --> 00:00:02,000\nDünya", "srt").text == "Merhaba Dünya"
    assert normalize_transcription({"segments": [{"text": "Merhaba"}, {"text": "Dünya"}]}, "diarized_json").text == "Merhaba Dünya"
    with pytest.raises(ValueError):
        normalize_transcription({"confidence": .9})


@pytest.mark.asyncio
@pytest.mark.parametrize("audio,options", [
    (b"malformed", {}), (wav_audio(), {"temperature": float("nan")}),
    (wav_audio(), {"language": "Turkish"}), (wav_audio(), {"response_format": "vtt"}),
    (wav_audio(), {"unknown": 1}), (wav_audio(), {"prompt": "a" * 2001}),
])
async def test_asr_invalid_input_never_transmits(audio, options):
    async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: pytest.fail("unexpected HTTP"))) as api:
        with pytest.raises(ValueError):
            await api.transcribe_bytes(audio, "qwen3-asr-1.7b", **options)


@pytest.mark.asyncio
async def test_asr_bounds_response_and_rejects_html():
    for response in [httpx.Response(200, content=b"a" * (MAX_TRANSCRIPT_BYTES + 1), headers={"content-type": "text/plain"}),
                     httpx.Response(200, text="<html>Login</html>", headers={"content-type": "text/html"})]:
        async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: response)) as api:
            with pytest.raises(ValueError):
                await api.transcribe_bytes(wav_audio(), "qwen3-asr-1.7b")


@pytest.mark.asyncio
@pytest.mark.parametrize("recognized", ["123", "true", "null", '{"text":"spoken literally"}'])
async def test_text_transcription_is_never_reinterpreted_as_json(recognized):
    async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: httpx.Response(200, text=recognized))) as api:
        result = await api.transcribe_bytes_normalized(wav_audio(), response_format="text")
    assert result.text == recognized


@pytest.mark.asyncio
async def test_transcription_rejects_invalid_utf8_or_json():
    for response, response_format in [(httpx.Response(200, content=b"\xff", headers={"content-type": "text/plain"}), "text"),
                                      (httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"}), "json")]:
        async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: response)) as api:
            with pytest.raises(ValueError):
                await api.transcribe_bytes_normalized(wav_audio(), response_format=response_format)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Merhaba, Iğdır ve İstanbul!", "Hello, how are you?"])
async def test_synthesis_binary_contract_and_utf8(text):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/audio/speech"
        assert request.headers["x-api-key"] == "secret"
        assert json.loads(request.content) == {"input": text, "model": "voxcpm2", "voice": "default",
                                                "response_format": "wav", "speed": 1.25, "stream": False}
        assert text.encode() in request.content
        return httpx.Response(200, content=wav_audio(), headers={"content-type": "audio/wav; charset=binary", "x-request-id": "id"})
    async with FirikAPI("secret", transport=httpx.MockTransport(handle)) as api:
        result = await api.synthesize(text, speed=1.25)
    assert result.audio == wav_audio()
    assert result.content_type == "audio/wav" and result.response_format == "wav"
    assert result.metadata == {"x-request-id": "id"}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response_format,audio,media_type", [
    ("mp3", b"ID3mock", "audio/mpeg"), ("flac", b"fLaCmock", "audio/flac"),
    ("opus", b"OggSheaderOpusHeadmock", "audio/ogg"), ("pcm", b"\x00\x00" * 10, "application/octet-stream"),
])
async def test_documented_speech_formats(response_format, audio, media_type):
    async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=audio, headers={"content-type": media_type}))) as api:
        result = await api.synthesize("Türkçe", response_format=response_format)
    assert result.audio == audio
    if response_format == "pcm":
        assert (result.sample_rate, result.channels) == (48000, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"voice": "nova"}, {"response_format": "aac"}, {"speed": 0},
                                     {"speed": float("nan")}, {"instructions": "x" * 1001}])
async def test_unverified_or_invalid_speech_options_do_not_transmit(options):
    async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: pytest.fail("unexpected HTTP"))) as api:
        with pytest.raises(ValueError):
            await api.synthesize("hello", **options)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(200, json={"text": "not audio"}),
                                      httpx.Response(200, content=b"not wav", headers={"content-type": "audio/wav"}),
                                      httpx.Response(200, content=wav_audio()[:-1], headers={"content-type": "audio/wav"}),
                                      httpx.Response(200, content=b"", headers={"content-type": "audio/wav"})])
async def test_invalid_binary_or_media_type_rejected(response):
    async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: response)) as api:
        with pytest.raises(ValueError):
            await api.synthesize("hello")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429, 500, 503])
async def test_speech_http_errors_redact_and_never_retry(status):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "private-key"}, headers={"retry-after": "2"})
    async with FirikAPI("private-key", transport=httpx.MockTransport(handle)) as api:
        with pytest.raises(FirikAPIError) as error:
            await api.synthesize("hello")
    assert "private-key" not in str(error.value)
    assert error.value.metadata["retry-after"] == "2"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_speech_timeout_is_safe_and_once():
    calls = []
    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private-key")
    async with FirikAPI("private-key", transport=httpx.MockTransport(handle)) as api:
        with pytest.raises(RuntimeError, match="otomatik tekrar") as error:
            await api.synthesize("hello")
    assert "private-key" not in str(error.value) and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["asr", "tts"])
async def test_audio_http_failure_preserves_cooldown_without_reading_private_body(operation):
    class UnreadableErrorBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            pytest.fail("Audio HTTP failures must not read upstream error bodies")
            yield b"private prompt"

        async def aclose(self):
            self.closed = True

    stream = UnreadableErrorBody()
    def handle(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(429, headers={"retry-after": "5", "x-request-id": "private prompt"}, stream=stream)

    async with FirikAPI("secret", transport=httpx.MockTransport(handle)) as api:
        with pytest.raises(FirikAPIError) as error:
            if operation == "asr":
                await api.transcribe_bytes_normalized(wav_audio())
            else:
                await api.synthesize("private prompt")
        assert error.value.status_code == 429
        assert error.value.metadata == api.last_metadata == {"retry-after": "5"}
        assert "private prompt" not in str(error.value)
        assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["asr", "tts"])
async def test_cancelling_audio_body_read_closes_stream(operation):
    started, closed = asyncio.Event(), asyncio.Event()

    class BlockingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self):
            closed.set()

    def handle(request):
        return httpx.Response(200, stream=BlockingBody())

    async with FirikAPI("secret", transport=httpx.MockTransport(handle)) as api:
        call = api.transcribe_bytes_normalized(wav_audio()) if operation == "asr" else api.synthesize("hello")
        pending = asyncio.create_task(call)
        await asyncio.wait_for(started.wait(), 1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert closed.is_set()


@pytest.mark.asyncio
async def test_audio_http_compression_is_rejected_before_decoding():
    class UnexpectedEncoding(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Compressed HTTP content must not reach the decoder")
            yield b""

    response = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=UnexpectedEncoding())
    async with FirikAPI("secret", transport=httpx.MockTransport(lambda _: response)) as api:
        with pytest.raises(ValueError, match="sıkıştırılmamış"):
            await api.synthesize("hello")


@pytest.mark.asyncio
async def test_cli_synthesis_binary_output_and_failure_atomicity(tmp_path, capsys):
    target = tmp_path / "speech.wav"
    good = httpx.MockTransport(lambda _: httpx.Response(200, content=wav_audio(), headers={"content-type": "audio/wav"}))
    assert await run(["synthesize", "--input", "İstanbul", "--output", str(target)], api_key="secret", transport=good) == 0
    assert target.read_bytes() == wav_audio() and not capsys.readouterr().out
    bad = httpx.MockTransport(lambda _: httpx.Response(402, json={"error": "credits"}))
    assert await run(["synthesize", "--input", "hello", "--output", str(target)], api_key="secret", transport=bad) == 1
    assert target.read_bytes() == wav_audio()
    assert list(tmp_path.iterdir()) == [target]
    assert await run(["synthesize", "--input", "hello"], api_key="secret", transport=httpx.MockTransport(lambda _: pytest.fail("no output must not request"))) == 1
