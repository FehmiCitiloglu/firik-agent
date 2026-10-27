"""Real adapter boundaries with deterministic HTTP/SSE, never live credentials."""
import asyncio
from datetime import datetime, timezone
import json

import httpx
import pytest

from firik_agent.core.failures import FailureKind, RequestPhase, RuntimeFailure, normalize_failure, parse_retry_after
from firik_agent.core.types import Message
from firik_agent.providers.firik import FirikProvider
from firik_agent.providers.openai_provider import OpenAIProvider


PROVIDERS = [OpenAIProvider, FirikProvider]


def mock_client(monkeypatch, respond):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status,code,expected", [
    (429, "rate_limit_exceeded", FailureKind.RATE_LIMITED),
    (503, "server_overloaded", FailureKind.PROVIDER_OVERLOADED),
    (401, "invalid_api_key", FailureKind.AUTHENTICATION_FAILED),
    (403, "access_denied", FailureKind.PERMISSION_DENIED),
    (400, "content_policy_violation", FailureKind.POLICY_REFUSAL),
    (403, "policy_refusal", FailureKind.POLICY_REFUSAL),
    (400, "context_length_exceeded", FailureKind.CONTEXT_OVERFLOW),
    (400, "invalid_tool_schema", FailureKind.UNSUPPORTED_CAPABILITY),
    (429, "insufficient_quota", FailureKind.BUDGET_EXCEEDED),
    (500, "unclassified", FailureKind.UNKNOWN),
])
async def test_adapter_classifies_without_retaining_body(monkeypatch, caplog, provider_type, stream, status, code, expected):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"code": code, "message": "password=TOP-SECRET\nprivate request payload"}},
                              headers={"Retry-After": "2.5", "X-Request-ID": "req-fixture-001"})
    mock_client(monkeypatch, respond)
    provider = provider_type(api_key="fixture-key", base_url="https://offline.invalid/v1")
    with pytest.raises(RuntimeFailure) as raised:
        if stream:
            _ = [chunk async for chunk in provider.chat_stream([Message(role="user", content="test")])]
        else:
            await provider.chat([Message(role="user", content="test")])
    error = raised.value
    assert error.kind == expected
    assert error.phase == RequestPhase.SUBMITTED_NO_OUTPUT
    assert error.metadata["http_status"] == status
    assert error.metadata["provider_request_id"] == "req-fixture-001"
    assert error.retry_after == 2.5
    assert len(requests) == 1  # Adapters never hide a retry or provider switch.
    assert "TOP-SECRET" not in str(error) + repr(error.metadata) + caplog.text
    assert "private request payload" not in str(error) + repr(error.metadata) + caplog.text


@pytest.mark.parametrize("value,expected", [("0", 0), ("2.5", 2.5), ("-1", None), ("nan", None),
                                            ("inf", None), ("garbage", None), (None, None)])
def test_retry_after_seconds(value, expected):
    assert parse_retry_after(value) == expected


def test_retry_after_http_date_is_relative_and_rejects_past():
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    assert parse_retry_after("Tue, 27 Oct 2026 12:00:03 GMT", now=now) == 3
    assert parse_retry_after("Tue, 27 Oct 2026 11:59:59 GMT", now=now) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("stream", [False, True])
async def test_connection_failure_is_proven_not_submitted(monkeypatch, provider_type, stream):
    def respond(request):
        raise httpx.ConnectError("secret URL query=TOP-SECRET", request=request)
    mock_client(monkeypatch, respond)
    provider = provider_type(api_key="unused")
    with pytest.raises(RuntimeFailure) as raised:
        if stream:
            _ = [chunk async for chunk in provider.chat_stream([])]
        else:
            await provider.chat([])
    assert raised.value.kind == FailureKind.TRANSPORT_ERROR
    assert raised.value.phase == RequestPhase.NOT_SUBMITTED
    assert "TOP-SECRET" not in str(raised.value) + repr(raised.value.metadata)


class BrokenStream(httpx.AsyncByteStream):
    def __init__(self, partial):
        self.partial = partial

    async def __aiter__(self):
        if self.partial == "text":
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        elif self.partial == "tool":
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"pay","arguments":"{"}}]}}]}\n\n'
        raise httpx.ReadError("raw body TOP-SECRET")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("partial", [False, "text", "tool"])
async def test_stream_network_error_preserves_output_phase(monkeypatch, provider_type, partial):
    mock_client(monkeypatch, lambda _: httpx.Response(200, stream=BrokenStream(partial)))
    provider = provider_type(api_key="unused")
    emitted = []
    with pytest.raises(RuntimeFailure) as raised:
        async for chunk in provider.chat_stream([]):
            emitted.append(chunk)
    assert raised.value.phase == (RequestPhase.PARTIAL_RESPONSE if partial else RequestPhase.SUBMITTED_NO_OUTPUT)
    assert raised.value.kind == (FailureKind.STREAM_INTERRUPTED if partial else FailureKind.TRANSPORT_ERROR)
    assert "TOP-SECRET" not in str(raised.value) + repr(raised.value.metadata)
    assert not any(chunk.tool_calls for chunk in emitted)  # Incomplete arguments never dispatch.


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("stream", [False, True])
async def test_success_status_with_policy_refusal_is_terminal(monkeypatch, provider_type, stream):
    if stream:
        response = httpx.Response(200, text='data: {"choices":[{"delta":{"refusal":"private policy detail"},"finish_reason":"content_filter"}]}\n\n')
    else:
        response = httpx.Response(200, json={"choices": [{"message": {"refusal": "private policy detail"}, "finish_reason": "stop"}]})
    mock_client(monkeypatch, lambda _: response)
    provider = provider_type(api_key="unused")
    with pytest.raises(RuntimeFailure) as raised:
        if stream:
            _ = [chunk async for chunk in provider.chat_stream([])]
        else:
            await provider.chat([])
    assert raised.value.kind == FailureKind.POLICY_REFUSAL
    assert "private policy detail" not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_embedded_sse_error_does_not_disappear(monkeypatch, provider_type):
    text = 'data: {"error":{"code":"policy_refusal","message":"TOP-SECRET"}}\n\n'
    mock_client(monkeypatch, lambda _: httpx.Response(200, text=text))
    with pytest.raises(RuntimeFailure) as raised:
        _ = [chunk async for chunk in provider_type(api_key="unused").chat_stream([])]
    assert raised.value.kind == FailureKind.POLICY_REFUSAL
    assert "TOP-SECRET" not in repr(raised.value.metadata)


def test_partial_output_overrides_even_an_implausible_connect_error():
    error = normalize_failure(httpx.ConnectError("raw"), phase=RequestPhase.PARTIAL_RESPONSE)
    assert error.phase == RequestPhase.PARTIAL_RESPONSE
    assert error.kind == FailureKind.STREAM_INTERRUPTED


def test_timeout_does_not_claim_tool_execution_without_phase_evidence():
    assert normalize_failure(TimeoutError()).kind == FailureKind.TRANSPORT_ERROR
    assert normalize_failure(TimeoutError(), phase=RequestPhase.TOOL_NOT_DISPATCHED).kind == FailureKind.TOOL_TIMEOUT
    assert normalize_failure(TimeoutError(), phase=RequestPhase.TOOL_DISPATCHED).kind == FailureKind.TOOL_OUTCOME_UNKNOWN


def test_metadata_rejects_non_identifier_and_credential_shapes():
    error = RuntimeFailure(FailureKind.UNKNOWN, provider="https://secret:pwd@host", provider_code="sk-secret",
                           request_id="password=secret", source_type="ValueError")
    assert error.metadata == {"failure_kind": "unknown", "request_phase": "submitted_no_output", "error_type": "ValueError"}


def test_cancelled_is_classified_without_its_message():
    assert normalize_failure(asyncio.CancelledError("private")).kind == FailureKind.CANCELLED


def test_late_typed_policy_refusal_preserves_classification_after_partial_output():
    original = RuntimeFailure(FailureKind.POLICY_REFUSAL, provider="fixture", provider_code="policy_refusal",
                              request_id="req-late")
    failure = normalize_failure(original, phase=RequestPhase.PARTIAL_RESPONSE)
    assert failure.kind == FailureKind.POLICY_REFUSAL
    assert failure.phase == RequestPhase.PARTIAL_RESPONSE
    assert failure.metadata["provider_code"] == "policy_refusal"
    assert failure.metadata["provider_request_id"] == "req-late"


@pytest.mark.parametrize("phase", [RequestPhase.NOT_SUBMITTED, RequestPhase.SUBMITTED_NO_OUTPUT])
def test_bare_provider_timeout_never_claims_tool_timeout(phase):
    failure = normalize_failure(TimeoutError("private"), phase=phase)
    assert failure.kind == FailureKind.TRANSPORT_ERROR
    assert failure.phase == phase


def test_permission_revocation_after_partial_output_remains_a_denial():
    failure = normalize_failure(PermissionError("private"), phase=RequestPhase.PARTIAL_RESPONSE)
    assert failure.kind == FailureKind.PERMISSION_DENIED
    assert failure.phase == RequestPhase.PARTIAL_RESPONSE


class CleanupFailure(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    async def aclose(self):
        raise ValueError("private cleanup TOP-SECRET")


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_transport_cleanup_error_is_normalized_without_losing_partial_phase(monkeypatch, provider_type):
    mock_client(monkeypatch, lambda _: httpx.Response(200, stream=CleanupFailure()))
    with pytest.raises(RuntimeFailure) as raised:
        _ = [chunk async for chunk in provider_type(api_key="unused").chat_stream([])]
    assert raised.value.kind == FailureKind.STREAM_INTERRUPTED
    assert raised.value.phase == RequestPhase.PARTIAL_RESPONSE
    assert "TOP-SECRET" not in str(raised.value) + repr(raised.value.metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_capability_refresh_is_one_authenticated_read_and_only_updates_selected_model(monkeypatch, provider_type):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [
            {"id": "selected", "capabilities": {"supports_tools": False, "context_window": 8192},
             "input_cost_per_million": 1.5, "unknown_secret": "DO-NOT-CACHE"},
            {"id": "other", "supports_tools": True}]})
    mock_client(monkeypatch, respond)
    provider = provider_type(api_key="fixture-refresh", base_url="https://offline.invalid/v1", default_model="selected")
    provider.model_metadata = {"selected": {"source": "provider", "supports_tools": True},
                               "other": {"id": "other", "source": "verified_config", "supports_tools": False}}
    before_other = dict(provider.model_metadata["other"])
    async def no_fallback():
        raise AssertionError("refresh must never use list_models fallback")
    monkeypatch.setattr(provider, "list_models", no_fallback)
    result = await provider.refresh_capabilities()
    assert len(requests) == 1
    assert requests[0].method == "GET" and str(requests[0].url) == "https://offline.invalid/v1/models"
    assert requests[0].headers["Authorization"] == "Bearer fixture-refresh"
    assert result["status"] == "refreshed" and result["source"] == "provider"
    assert result["capabilities"]["supports_tools"] is False
    assert result["capabilities"]["context_window"] == 8192
    assert result["capabilities"]["supports_streaming"] is None
    assert provider.current_model == "selected"
    assert provider.model_metadata["other"] == before_other
    assert "DO-NOT-CACHE" not in repr(result) + repr(provider.model_metadata)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("rows", [[], [{"id": "other"}], [{"id": "selected"}, {"id": "selected"}], "invalid"])
async def test_capability_refresh_missing_model_revokes_cached_claims(monkeypatch, provider_type, rows):
    mock_client(monkeypatch, lambda _: httpx.Response(200, json={"data": rows}))
    provider = provider_type(api_key="unused", default_model="selected")
    provider.model_metadata["selected"] = {"supports_tools": True, "source": "provider"}
    result = await provider.refresh_capabilities()
    assert result["status"] == "unavailable" and result["source"] is None
    assert result["capabilities"]["source"] is None
    assert result["capabilities"]["supports_tools"] is None
    assert "selected" not in provider.model_metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
@pytest.mark.parametrize("status,kind", [(401, FailureKind.AUTHENTICATION_FAILED), (403, FailureKind.PERMISSION_DENIED),
                                        (429, FailureKind.RATE_LIMITED), (503, FailureKind.PROVIDER_OVERLOADED)])
async def test_capability_refresh_never_retries_denied_or_transient_response(monkeypatch, provider_type, status, kind):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "private BODY-SECRET"}}, headers={"Retry-After": "0"})
    mock_client(monkeypatch, respond)
    provider = provider_type(api_key="unused")
    with pytest.raises(RuntimeFailure) as raised:
        await provider.refresh_capabilities()
    assert raised.value.kind == kind
    assert len(requests) == 1
    assert "BODY-SECRET" not in str(raised.value) + repr(raised.value.metadata)


@pytest.mark.asyncio
async def test_non_http_scripted_provider_refresh_has_no_implicit_network(monkeypatch):
    from firik_agent.providers.base import BaseProvider
    class Scripted(BaseProvider):
        async def chat(self, **kwargs): pass
        async def chat_stream(self, **kwargs): yield
        async def list_models(self): return []
    def no_client(**kwargs):
        raise AssertionError("scripted provider must not create a network client")
    monkeypatch.setattr(httpx, "AsyncClient", no_client)
    result = await Scripted("scripted", "offline://fixture").refresh_capabilities()
    assert result["status"] == "unavailable"
    assert result["capabilities"]["source"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_capability_refresh_does_not_follow_redirect_with_credentials(monkeypatch, provider_type):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://elsewhere.invalid/models"})
    mock_client(monkeypatch, respond)
    with pytest.raises(RuntimeFailure):
        await provider_type(api_key="fixture-only").refresh_capabilities()
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", PROVIDERS)
async def test_capability_refresh_preserves_cancellation(monkeypatch, provider_type):
    def respond(request):
        raise asyncio.CancelledError()
    mock_client(monkeypatch, respond)
    with pytest.raises(asyncio.CancelledError):
        await provider_type(api_key="unused").refresh_capabilities()
