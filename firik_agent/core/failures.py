"""Typed failures with deliberately bounded, non-secret diagnostic metadata.

Provider bodies are inspected for classification only. They are never retained
in exceptions, logs, events or model-visible context.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import math
import re
from typing import Any, Optional

import httpx


class FailureKind(str, Enum):
    TRANSPORT_ERROR = "transport_error"
    RATE_LIMITED = "rate_limited"
    PROVIDER_OVERLOADED = "provider_overloaded"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    CONTEXT_OVERFLOW = "context_overflow"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    STREAM_INTERRUPTED = "stream_interrupted"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_OUTCOME_UNKNOWN = "tool_outcome_unknown"
    WORKSPACE_CONFLICT = "workspace_conflict"
    BUDGET_EXCEEDED = "budget_exceeded"
    POLICY_REFUSAL = "policy_refusal"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class RequestPhase(str, Enum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED_NO_OUTPUT = "submitted_no_output"
    PARTIAL_RESPONSE = "partial_response"
    RESPONSE_COMPLETE = "response_complete"
    TOOL_NOT_DISPATCHED = "tool_not_dispatched"
    TOOL_DISPATCHED = "tool_dispatched"
    TOOL_COMPLETED = "tool_completed"


_MESSAGES = {
    FailureKind.TRANSPORT_ERROR: "Provider transport failed; request submission state is recorded.",
    FailureKind.RATE_LIMITED: "Provider rate limit reached.",
    FailureKind.PROVIDER_OVERLOADED: "Provider is temporarily overloaded.",
    FailureKind.AUTHENTICATION_FAILED: "Provider authentication failed; resolve the configured credentials.",
    FailureKind.PERMISSION_DENIED: "Operation denied by the active permission policy.",
    FailureKind.CONTEXT_OVERFLOW: "Provider context limit exceeded.",
    FailureKind.UNSUPPORTED_CAPABILITY: "Provider does not support the requested capability or schema.",
    FailureKind.INVALID_TOOL_ARGUMENTS: "Tool arguments are invalid.",
    FailureKind.STREAM_INTERRUPTED: "Incomplete streamed tool call or truncated SSE response; output was not replayed.",
    FailureKind.TOOL_TIMEOUT: "Tool timed out before confirmed execution.",
    FailureKind.TOOL_OUTCOME_UNKNOWN: "Tool outcome is unknown; reconciliation is required before repetition.",
    FailureKind.WORKSPACE_CONFLICT: "Workspace changed; inspect the current revision before another write.",
    FailureKind.BUDGET_EXCEEDED: "Run resource budget exhausted.",
    FailureKind.POLICY_REFUSAL: "Provider policy refusal; automatic bypass is forbidden.",
    FailureKind.CANCELLED: "Operation cancelled.",
    FailureKind.UNKNOWN: "Operation failed with an unclassified error; inspect the recorded error type.",
}


def _identifier(value: Any) -> Optional[str]:
    """Identifiers only: never store URLs, arbitrary messages, headers or tokens."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value):
        return None
    if re.search(r"(?i)(?:sk[-_]|firik_llm_|bearer|api.?key|password|secret|token[=:])", value):
        return None
    return value


class RuntimeFailure(RuntimeError):
    def __init__(self, kind: FailureKind | str, *, phase: RequestPhase | str = RequestPhase.SUBMITTED_NO_OUTPUT,
                 provider: Optional[str] = None, http_status: Optional[int] = None,
                 provider_code: Optional[str] = None, request_id: Optional[str] = None,
                 retry_after: Optional[float] = None, source_type: Optional[str] = None):
        self.kind = FailureKind(kind)
        self.phase = RequestPhase(phase)
        self.retry_after = (float(retry_after) if isinstance(retry_after, (int, float))
                            and not isinstance(retry_after, bool) and math.isfinite(retry_after)
                            and retry_after >= 0 else None)
        self.metadata = {"failure_kind": self.kind.value, "request_phase": self.phase.value}
        for key, value in (("provider", provider), ("provider_code", provider_code),
                           ("provider_request_id", request_id), ("error_type", source_type)):
            safe = _identifier(value)
            if key == "provider_code" and isinstance(value, str) and value in {"invalid_api_key", "missing_api_key", "api_key_expired", "invalid_token", "token_expired"}:
                safe = value  # Known diagnostic codes describe credentials, not their values.
            if safe:
                self.metadata[key] = safe
        if isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599:
            self.metadata["http_status"] = http_status
        if self.retry_after is not None:
            self.metadata["retry_after"] = self.retry_after
        super().__init__(_MESSAGES[self.kind])


def parse_retry_after(value: Optional[str], *, now: Optional[datetime] = None) -> Optional[float]:
    if not value:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                return None
            delay = (when - (now or datetime.now(timezone.utc))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return delay if math.isfinite(delay) and delay >= 0 else None


def provider_failure(response: httpx.Response, *, provider: Optional[str] = None,
                     phase: RequestPhase = RequestPhase.SUBMITTED_NO_OUTPUT) -> RuntimeFailure:
    """Classify structured provider errors without retaining the response body."""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = {}
    error = (payload.get("error") or payload.get("detail") or payload) if isinstance(payload, dict) else {}
    if isinstance(error, str):
        error = {"message": error}
    elif not isinstance(error, dict):
        error = {}
    code = error.get("code") or error.get("type")
    # Error text is used locally to recognize legacy compatible endpoints; it is
    # discarded before constructing the public exception.
    hint = " ".join(str(error.get(key, "")) for key in ("code", "type", "message")).casefold()
    status = response.status_code
    if any(word in hint for word in ("content_filter", "policy_refusal", "safety_policy", "content_policy", "responsibleai", "policy violation")):
        kind = FailureKind.POLICY_REFUSAL
    elif status == 401:
        kind = FailureKind.AUTHENTICATION_FAILED
    elif status == 403:
        kind = FailureKind.PERMISSION_DENIED
    elif any(word in hint for word in ("context_length", "context_window", "context window", "context limit", "maximum context", "too many tokens")):
        kind = FailureKind.CONTEXT_OVERFLOW
    elif status == 402 or any(word in hint for word in ("insufficient_quota", "budget_exceeded", "credit_balance", "billing_hard_limit", "quota_exceeded")):
        kind = FailureKind.BUDGET_EXCEEDED
    elif status == 429 or any(word in hint for word in ("rate_limit", "rate limit")):
        kind = FailureKind.RATE_LIMITED
    elif status in {502, 503, 504, 529} or any(word in hint for word in ("overloaded", "server_overloaded")):
        kind = FailureKind.PROVIDER_OVERLOADED
    elif any(word in hint for word in ("unsupported", "not supported", "invalid_tool_schema", "invalid schema", "invalid_function_parameters")):
        kind = FailureKind.UNSUPPORTED_CAPABILITY
    else:
        kind = FailureKind.UNKNOWN
    return RuntimeFailure(kind, phase=phase, provider=provider, http_status=status,
                          provider_code=code, request_id=response.headers.get("x-request-id") or response.headers.get("x-evren-request-id") or response.headers.get("request-id"),
                          retry_after=parse_retry_after(response.headers.get("retry-after")), source_type="HTTPStatusError")


def normalize_failure(error: BaseException, *, phase: RequestPhase = RequestPhase.SUBMITTED_NO_OUTPUT,
                      provider: Optional[str] = None) -> RuntimeFailure:
    if isinstance(error, RuntimeFailure):
        # Preserve a policy denial even if some response content preceded it.
        if phase == RequestPhase.PARTIAL_RESPONSE and error.phase != phase:
            return RuntimeFailure(error.kind, phase=phase, provider=error.metadata.get("provider", provider),
                                  http_status=error.metadata.get("http_status"), provider_code=error.metadata.get("provider_code"),
                                  request_id=error.metadata.get("provider_request_id"), retry_after=error.retry_after,
                                  source_type=error.metadata.get("error_type"))
        return error
    if isinstance(error, asyncio.CancelledError):
        kind = FailureKind.CANCELLED
    elif isinstance(error, PermissionError):
        kind = FailureKind.PERMISSION_DENIED
    elif isinstance(error, httpx.HTTPStatusError):
        return provider_failure(error.response, provider=provider, phase=phase)
    elif phase == RequestPhase.PARTIAL_RESPONSE:
        kind = FailureKind.STREAM_INTERRUPTED
    elif isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.InvalidURL, httpx.UnsupportedProtocol)):
        kind, phase = FailureKind.TRANSPORT_ERROR, RequestPhase.NOT_SUBMITTED
    elif isinstance(error, (httpx.TransportError, ConnectionError)):
        kind = FailureKind.TRANSPORT_ERROR
    elif isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        kind = (FailureKind.TOOL_OUTCOME_UNKNOWN if phase == RequestPhase.TOOL_DISPATCHED else
                FailureKind.TOOL_TIMEOUT if phase == RequestPhase.TOOL_NOT_DISPATCHED else FailureKind.TRANSPORT_ERROR)
    elif isinstance(error, NotImplementedError):
        kind = FailureKind.UNSUPPORTED_CAPABILITY
    else:
        kind = FailureKind.UNKNOWN
    return RuntimeFailure(kind, phase=phase, provider=provider, source_type=type(error).__name__)
