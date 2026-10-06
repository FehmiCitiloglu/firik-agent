"""Contract and failure-path tests. No credentials, quota or live network needed."""
from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest

from firik_agent.api import FirikAPI, FirikAPIError
from firik_agent.api.cli import build_parser, payload_for, run
from firik_agent.api.client import api_schema
from firik_agent.providers.firik import FirikProvider

ID = "11111111-1111-4111-8111-111111111111"
KEY = "test-secret-key"

# Every public operation must have a first-class terminal command. Adding an
# endpoint to the checked-in official contract without a command fails this test.
CASES = [
    (["health"], "GET", "/healthz", None),
    (["health", "--ready"], "GET", "/readyz", None),
    (["models"], "GET", "/v1/models", None),
    (["quota"], "GET", "/v1/quota", None),
    (["terms", "status"], "GET", "/v1/terms/status", None),
    (["terms", "text"], "GET", "/v1/terms/text", None),
    (["terms", "accept", "3"], "POST", "/v1/terms/accept", {"version": 3}),
    (["chat", "--prompt", "Merhaba", "--firik-tools"], "POST", "/v1/chat/completions", {"model": "test-model", "messages": [{"role": "user", "content": "Merhaba"}], "evren_tools": True}),
    (["completions", "--prompt", "Bir", "--max-tokens", "12"], "POST", "/v1/completions", {"model": "test-model", "prompt": "Bir", "max_tokens": 12}),
    (["responses", "--prompt", "Merhaba", "--max-tokens", "12"], "POST", "/v1/responses", {"model": "test-model", "input": "Merhaba", "max_output_tokens": 12}),
    (["embeddings", "--model", "embed", "--input", "a", "--input", "b"], "POST", "/v1/embeddings", {"model": "embed", "input": ["a", "b"]}),
    (["rerank", "--model", "rank", "--query", "soru", "--document", "belge"], "POST", "/v1/rerank", {"model": "rank", "query": "soru", "documents": ["belge"]}),
    (["ocr", "--image", "data:image/png;base64,YQ=="], "POST", "/v1/ocr", {"model": "dots-ocr", "image": "data:image/png;base64,YQ=="}),
    (["media", "initiate", "--content-type", "video/mp4", "--size-bytes", "12"], "POST", "/v1/media", {"content_type": "video/mp4", "size_bytes": 12}),
    (["media", "status", ID], "GET", f"/v1/media/{ID}", None),
    (["media", "complete", ID], "POST", f"/v1/media/{ID}/complete", {}),
    (["media", "finalize", ID], "POST", f"/v1/media/{ID}/finalize", None),
    (["media", "abort", ID], "POST", f"/v1/media/{ID}/abort", None),
    (["request-status", ID], "GET", f"/v1/requests/{ID}", None),
]


def test_all_public_operations_have_commands():
    covered = {(method.lower(), path.replace(ID, "{request_id}" if "/requests/" in path else "{media_id}")) for _, method, path, _ in CASES}
    covered.add(("post", "/v1/audio/transcriptions"))
    assert covered == {(method, path) for path, methods in api_schema()["paths"].items() for method in methods}


@pytest.mark.asyncio
@pytest.mark.parametrize("argv,method,path,body", CASES)
async def test_cli_contract(argv, method, path, body, capsys):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == method
        assert request.url.path == path
        assert request.headers.get("x-api-key") == (KEY if path.startswith("/v1/") else None)
        if body is not None:
            assert json.loads(request.content) == body
        else:
            assert not request.content
        if path.endswith("/abort"):
            return httpx.Response(204)
        return httpx.Response(200, json={"ok": True, "firik": {"extension": 123}})
    code = await run(argv, api_key=KEY, default_model="test-model", transport=httpx.MockTransport(handler))
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert len(calls) == 1
    assert json.loads(captured.out) == (None if path.endswith("/abort") else {"ok": True, "firik": {"extension": 123}})


@pytest.mark.asyncio
@pytest.mark.parametrize("response_format", ["json", "text", "srt", "verbose_json", "diarized_json"])
async def test_audio_multipart_and_formats(tmp_path, capsys, response_format):
    audio = tmp_path / "örnek.wav"
    audio.write_bytes(b"RIFF-test-audio")
    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        assert b"RIFF-test-audio" in request.content
        assert b'name="model"' in request.content
        assert b"qwen3-asr-1.7b" in request.content
        assert response_format.encode() in request.content
        return httpx.Response(200, text="Merhaba" if response_format != "srt" else "1\n00:00:00,000 --> 00:00:01,000\nMerhaba")
    code = await run(["transcribe", str(audio), "--response-format", response_format, "--language", "tr"], api_key=KEY, transport=httpx.MockTransport(handler))
    assert code == 0
    assert "Merhaba" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_vision_and_ocr_local_images(tmp_path, capsys):
    path = tmp_path / "scan.png"
    path.write_bytes(b"png")
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(200, json={"text": "ok"})
    transport = httpx.MockTransport(handler)
    assert await run(["chat", "--image", str(path), "--prompt", "oku"], api_key=KEY, transport=transport) == 0
    assert calls[0]["messages"][0]["content"] == [{"type": "text", "text": "oku"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}}]
    assert await run(["ocr", "--file", str(path)], api_key=KEY, transport=transport) == 0
    assert calls[1]["image"] == "data:image/png;base64,cG5n"


class FragmentedStream(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
    async def __aiter__(self):
        for index in range(0, len(self.data), 3):
            yield self.data[index:index + 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("command,sse", [
    ("chat", ': ping\r\ndata: {"choices":[{"delta":{"content":"Türkçe"}}]}\r\n\r\ndata: {"choices":[],"usage":{"total_tokens":7}}\n\ndata: [DONE]\n\n'),
    ("completions", 'data: {"choices":[{"text":"Türkçe"}]}\n\ndata: [DONE]\n\n'),
    ("responses", 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta",\ndata: "delta":"Türkçe"}\n\nevent: response.completed\ndata: {"type":"response.completed"}'),
])
async def test_streaming_protocols(command, sse, capsys):
    def handler(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream", "X-Request-Id": ID}, stream=FragmentedStream(sse.encode()))
    transport = httpx.MockTransport(handler)
    argv = [command, "--prompt", "hi", "--stream"]
    assert await run(argv, api_key=KEY, transport=transport) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(events) >= 2
    if command == "chat":
        assert events[1]["data"]["usage"]["total_tokens"] == 7
    if command == "responses":
        assert events[-1]["event"] == "response.completed"
    assert await run(argv + ["--text"], api_key=KEY, transport=transport) == 0
    assert capsys.readouterr().out == "Türkçe\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("sse", [
    'event: error\ndata: {"message":"failed"}\n\n',
    'data: {"error":{"message":"failed"}}\n\n',
    'event: response.failed\ndata: {"type":"response.failed"}\n\n',
    'data: this-is-not-json\n\n',
])
async def test_stream_errors_fail_and_preserve_output(sse, tmp_path, capsys):
    output = tmp_path / "result.txt"
    output.write_text("old-result")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse))
    assert await run(["chat", "--prompt", "hi", "--stream", "--output", str(output)], api_key=KEY, transport=transport) == 1
    assert output.read_text() == "old-result"
    assert "Hata:" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 422, 429, 503])
async def test_errors_metadata_redaction_no_retry(status, capsys):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": KEY, "firik": {"remaining": 0}}}, headers={"X-Request-Id": ID, "Retry-After": "60", "X-Evren-Credits-Remaining": "0"})
    assert await run(["chat", "--prompt", "hi", "--metadata"], api_key=KEY, transport=httpx.MockTransport(handler)) == 1
    output = capsys.readouterr()
    assert not output.out
    assert KEY not in output.err
    assert str(status) in output.err and ID in output.err and "retry-after" in output.err
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_json_stdin_extensions_and_atomic_output(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"model":"custom","input":[{"role":"user","content":"hi"}],"tools":[{"type":"function","name":"lookup","parameters":{}}]}'))
    output = tmp_path / "response.json"
    def handler(request):
        body = json.loads(request.content)
        assert body["tools"][0]["name"] == "lookup"
        assert body["reasoning"] == {"effort": "high"}
        assert body["model"] == "custom"
        return httpx.Response(200, json={"output": [], "usage": {"total_tokens": 12}})
    assert await run(["responses", "--body", "-", "--set", 'reasoning={"effort":"high"}', "--output", str(output)], api_key=KEY, transport=httpx.MockTransport(handler)) == 0
    assert json.loads(output.read_text())["usage"]["total_tokens"] == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("multipart", [False, True])
async def test_media_upload_sequence_and_storage_auth(tmp_path, multipart):
    path = tmp_path / "sample.mp4"
    path.write_bytes(b"abcdefghij")
    calls, uploaded = [], []
    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "PUT":
            assert "x-api-key" not in request.headers and "authorization" not in request.headers
            assert request.headers["content-type"] == "video/mp4"
            assert int(request.headers["content-length"]) == len(request.content)
            uploaded.append(request.content)
            return httpx.Response(200, headers={"ETag": '"etag-value"'})
        assert request.headers["x-api-key"] == KEY
        if request.url.path == "/v1/media":
            assert json.loads(request.content) == {"content_type": "video/mp4", "size_bytes": 10}
            plan = {"part_urls": ["https://storage.test/1", "https://storage.test/2", "https://storage.test/3"], "part_size_bytes": 4} if multipart else {"upload_url": "https://storage.test/one"}
            return httpx.Response(200, json={"media_id": ID, **plan})
        if request.url.path.endswith("/complete"):
            expected = {"parts": [{"part_number": n, "etag": '"etag-value"'} for n in (1, 2, 3)]} if multipart else {}
            assert json.loads(request.content) == expected
        return httpx.Response(200, json={"media_id": ID, "state": "READY"})
    async with FirikAPI(KEY, transport=httpx.MockTransport(handler)) as api:
        result = await api.upload_media(path, finalize=True)
    assert result["state"] == "READY"
    assert b"".join(uploaded) == b"abcdefghij"
    assert calls[-2:] == [("POST", f"/v1/media/{ID}/complete"), ("POST", f"/v1/media/{ID}/finalize")]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["storage", "etag", "plan", "cancel"])
async def test_media_failure_aborts(tmp_path, failure):
    path = tmp_path / "test.mp4"
    path.write_bytes(b"abcd")
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/media":
            return httpx.Response(200, json={"media_id": ID, "part_urls": ["https://storage.test/part?secret=private"], "part_size_bytes": 0 if failure == "plan" else 4})
        if request.method == "PUT":
            if failure == "cancel":
                raise asyncio.CancelledError()
            return httpx.Response(503 if failure == "storage" else 200)
        return httpx.Response(204)
    async with FirikAPI(KEY, transport=httpx.MockTransport(handler)) as api:
        with pytest.raises((RuntimeError, ValueError, asyncio.CancelledError)) as error:
            await api.upload_media(path)
    assert "secret=private" not in str(error.value)
    assert calls[-1] == f"/v1/media/{ID}/abort"
    assert f"/v1/media/{ID}/complete" not in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["https://evil.test/v1/models", "//evil.test/v1/models", "/internal/chat/completions", "/v1/models?secret=1", "/v1/media/../../models"])
async def test_raw_request_rejects_untrusted_and_internal_paths(path):
    async with FirikAPI(KEY, transport=httpx.MockTransport(lambda _: pytest.fail("network must not be called"))) as api:
        with pytest.raises(ValueError):
            await api.request("GET", path)


@pytest.mark.asyncio
async def test_missing_key_and_offline_docs(capsys):
    transport = httpx.MockTransport(lambda _: pytest.fail("network must not be called"))
    assert await run(["models"], api_key="", transport=transport) == 1
    assert "FIRIK_API_KEY" in capsys.readouterr().err
    assert await run(["api-docs"], api_key="", transport=transport) == 0
    assert len(json.loads(capsys.readouterr().out)["operations"]) == 20


def test_global_options_preserved():
    args = build_parser().parse_args(["--output", "out.json", "--timeout", "12", "terms", "text", "--text"])
    assert args.output == "out.json" and args.timeout == 12 and args.text


@pytest.mark.parametrize("argv", [
    ["embeddings", "--input", "hello"],
    ["responses"],
    ["rerank", "-m", "rank", "--query", "q", "--documents", '{}'],
    ["chat", "--prompt", "hi", "--body", '{"messages":[]}'],
    ["chat", "--prompt", "hi", "--max-tokens", "0"],
    ["chat", "--prompt", "hi", "--set", "oops"],
])
def test_invalid_generation_inputs(argv):
    with pytest.raises(ValueError):
        payload_for(build_parser().parse_args(argv), "test-model")


@pytest.mark.asyncio
async def test_terms_never_accepted_implicitly(monkeypatch):
    provider = FirikProvider(api_key=KEY)
    async def status():
        return {"accepted": False, "current_version": 2}
    async def accept(_):
        pytest.fail("No automatic acceptance")
    monkeypatch.setattr(provider, "check_terms_status", status)
    monkeypatch.setattr(provider, "accept_terms", accept)
    with pytest.raises(RuntimeError, match="explicitly accept"):
        await provider.ensure_terms_accepted()


@pytest.mark.asyncio
async def test_finalize_failure_reports_recoverable_id(tmp_path):
    path = tmp_path / "test.mp4"
    path.write_bytes(b"abcd")
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/media":
            return httpx.Response(200, json={"media_id": ID, "upload_url": "https://storage.test/file"})
        if request.url.path.endswith("/finalize"):
            return httpx.Response(409, json={"error": {"message": "not ready"}})
        return httpx.Response(200, json={"media_id": ID, "state": "UPLOADED"})
    async with FirikAPI(KEY, transport=httpx.MockTransport(handler)) as api:
        with pytest.raises(RuntimeError, match=ID):
            await api.upload_media(path, finalize=True)
    assert not any(path.endswith("/abort") for path in calls)


@pytest.mark.asyncio
async def test_missing_config_fails_before_network(tmp_path, capsys):
    transport = httpx.MockTransport(lambda _: pytest.fail("No network with missing explicit config"))
    assert await run(["models", "--config", str(tmp_path / "missing.yaml")], api_key=KEY, transport=transport) == 1
    assert "Config dosyası bulunamadı" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_api_help_does_not_exit_repl(capsys):
    from firik_agent.cli import handle_slash_command
    from firik_agent.core.agent import Agent
    agent = Agent(config={"providers": {}, "agent": {"default_provider": "firik"}})
    assert await handle_slash_command(agent, "/api --help") is True
    assert "transcribe" in capsys.readouterr().out
    assert not agent._is_initialized
