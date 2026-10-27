"""Passive, bounded accounting for native runtime events.

Usage frames are cumulative snapshots of one request, not token increments.
Only complete, successfully finalized requests can form a complete run total.
Legacy events remain readable; their reported values are partial evidence.
No prompts, tool arguments, credentials or exception messages are retained here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping


def _number(value: Any) -> float | int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 and (isinstance(value, int) or math.isfinite(value)):
        return value
    return None


def _usage(values: Mapping[str, Any]) -> dict[str, float | int]:
    result = {}
    for key, aliases in {
        "prompt_tokens": ("prompt_tokens", "input_tokens"),
        "completion_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
        "cached_tokens": ("cached_tokens", "cache_read_input_tokens"),
        "cost": ("cost_usd", "cost") if str(values.get("currency", "USD")).upper() == "USD" else ("cost_usd",),
    }.items():
        for alias in aliases:
            value = _number(values.get(alias))
            if value is not None and (key == "cost" or isinstance(value, int)):
                result[key] = value
                break
    if "total_tokens" not in result and all(key in result for key in ("prompt_tokens", "completion_tokens")):
        result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
    for key in ("prompt_tokens_details", "input_tokens_details"):
        details = values.get(key)
        if isinstance(details, Mapping) and isinstance((cached := _number(details.get("cached_tokens"))), int):
            result["cached_tokens"] = cached
            break
    return result


@dataclass
class RunMetrics:
    """Observe live AgentEvents or passive SQLite/dictionary events, once in order.

    Request updates are idempotent. Tool counters count emitted attempt events;
    replay should feed each journal event once. Overflow is explicit and makes
    complete totals unknown, rather than silently dropping old billed requests.
    """

    max_requests: int = 10000
    _requests: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict, repr=False)
    _iterations: dict[str, int] = field(default_factory=dict, repr=False)
    _limited: bool = False
    _tool_calls: int = 0
    _tool_errors: int = 0
    _tool_duration_ms: float = 0.0
    _errors: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_requests, bool) or not isinstance(self.max_requests, int) or not 1 <= self.max_requests <= 100000:
            raise ValueError("max_requests must be between 1 and 100000")

    def observe(self, event: Any) -> None:
        if hasattr(event, "data"):
            data = dict(event.data)
            data.setdefault("type", event.event_type)
        elif isinstance(event, Mapping):
            data = dict(event)
        else:
            data = {key: getattr(event, key, None) for key in ("type", "metadata", "duration_ms")}
        kind = str(getattr(data.get("type"), "value", data.get("type", data.get("event_type", ""))))
        metadata = data.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        if kind == "subagent_event":
            child_kind = metadata.get("child_event_type")
            if child_kind:
                detail = metadata.get("child_metadata")
                detail = dict(detail) if isinstance(detail, Mapping) else {}
                self.observe({"type": child_kind,
                    "duration_ms": metadata.get("child_duration_ms", data.get("duration_ms")),
                    "metadata": {**detail, "agent_id": metadata.get("agent_id", "unknown-child"),
                                 "iteration": detail.get("iteration", metadata.get("request_iteration", 1))}})
            return
        if kind == "tool_call_started":
            self._tool_calls += 1
        elif kind in {"tool_call_result", "tool_call_error"}:
            self._tool_errors += int(kind == "tool_call_error")
            duration = _number(data.get("duration_ms"))
            if duration is not None:
                self._tool_duration_ms += duration
        elif kind == "error":
            self._errors += 1
        if kind not in {"model_request_started", "model_usage", "model_request_finished"}:
            return
        owner = str(metadata.get("agent_id") or "orchestrator")
        if len(owner) > 256:
            self._limited = True
            return
        if owner not in self._iterations:
            if len(self._iterations) >= self.max_requests:
                self._limited = True
                return
            self._iterations[owner] = 0
        iteration = metadata.get("iteration")
        if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 1:
            iteration = self._iterations[owner] + int(kind == "model_request_started") or 1
        self._iterations[owner] = iteration
        identity = str(metadata.get("request_id") or f"legacy:{iteration}")
        if len(identity) > 256:
            self._limited = True
            return
        key = owner, identity
        if key not in self._requests:
            if len(self._requests) >= self.max_requests:
                self._limited = True
                return
            self._requests[key] = {"usage": {}, "metrics": {}, "status": None,
                                   "kind": str(metadata.get("request_kind") or "model")[:32]}
        request = self._requests[key]
        usage = metadata.get("usage")
        if isinstance(usage, Mapping):
            # Merge snapshots; sparse final metadata must not erase a prior
            # cache detail or a provider's already observed token count.
            normalized = _usage(usage)
            previous = request["usage"].copy()
            families = {"prompt_tokens": ("prompt_tokens", "input_tokens"),
                        "completion_tokens": ("completion_tokens", "output_tokens"),
                        "total_tokens": ("total_tokens",), "cost": ("cost", "cost_usd"),
                        "cached_tokens": ("cached_tokens", "cache_read_input_tokens")}
            for canonical, aliases in families.items():
                if any(alias in usage for alias in aliases):
                    # Explicit null/invalid values invalidate older evidence.
                    request["usage"].pop(canonical, None)
            if "currency" in usage and str(usage["currency"]).upper() != "USD" and "cost_usd" not in normalized:
                request["usage"].pop("cost", None)
            for details_key in ("prompt_tokens_details", "input_tokens_details"):
                details = usage.get(details_key)
                if isinstance(details, Mapping) and "cached_tokens" in details:
                    request["usage"].pop("cached_tokens", None)
            request["usage"].update(normalized)
            touched_components = any(alias in usage for key in ("prompt_tokens", "completion_tokens") for alias in families[key])
            if "total_tokens" not in normalized and touched_components:
                if all(key in request["usage"] for key in ("prompt_tokens", "completion_tokens")):
                    request["usage"]["total_tokens"] = request["usage"]["prompt_tokens"] + request["usage"]["completion_tokens"]
                else:
                    # A prior total describes the prior snapshot, not a newer
                    # output count whose matching input count is unknown.
                    request["usage"].pop("total_tokens", None)
            changed_tokens = any(previous.get(key) != request["usage"].get(key)
                                 for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens"))
            if changed_tokens and "cost" not in normalized:
                request["usage"].pop("cost", None)
        metrics = metadata.get("metrics")
        if isinstance(metrics, Mapping):
            for name in ("latency_ms", "ttft_ms", "retry_count"):
                value = _number(metrics.get(name))
                if value is not None:
                    request["metrics"][name] = value
        if kind == "model_request_finished":
            status = metadata.get("status")
            request["status"] = status if status in {"completed", "failed", "cancelled"} else "unknown"

    def snapshot(self) -> dict[str, Any]:
        requests = list(self._requests.values())
        finished = sum(request["status"] is not None for request in requests)
        finalized = bool(requests) and not self._limited and all(request["status"] == "completed" for request in requests)
        reported = {}
        complete = {}
        coverage = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "cost"):
            values = [request["usage"][key] for request in requests if key in request["usage"]]
            coverage[key] = len(values)
            reported[key] = sum(values) if values else None
            complete[key] = reported[key] if finalized and len(values) == len(requests) else None
        return {"schema_version": 1, "currency": "USD", "request_count": len(requests), "finished_requests": finished,
            "reported_token_lower_bound": sum(max(request["usage"].get("total_tokens", 0),
                request["usage"].get("prompt_tokens", 0) + request["usage"].get("completion_tokens", 0)) for request in requests),
            "failed_requests": sum(request["status"] == "failed" for request in requests),
            "cancelled_requests": sum(request["status"] == "cancelled" for request in requests),
            "compaction_requests": sum(request["kind"] == "compaction" for request in requests),
            "usage": complete, "reported_usage": reported, "coverage": coverage,
            "usage_complete": complete["total_tokens"] is not None,
            "cost_complete": complete["cost"] is not None,
            "accounting_limited": self._limited, "tool_calls": self._tool_calls,
            "tool_errors": self._tool_errors, "tool_duration_ms": self._tool_duration_ms,
            "runtime_errors": self._errors,
            "recovery_attempts": sum(1 for request in requests if request["metrics"].get("retry_count", 0) > 0),
            "latency_ms": [request["metrics"]["latency_ms"] for request in requests if "latency_ms" in request["metrics"]],
            "ttft_ms": [request["metrics"]["ttft_ms"] for request in requests if "ttft_ms" in request["metrics"]]}
