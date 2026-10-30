"""Bounded recovery policy shared by streaming and ordinary agent requests.

This module decides; permission gates and tool owners still authorize execution.
No provider switch, prompt bypass or automatic mutation replay exists here.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import math
import random
import time
from typing import Any, Awaitable, Callable

from firik_agent.core.failures import FailureKind, RequestPhase, RuntimeFailure


class SideEffectRisk(str, Enum):
    PURE_READ = "pure_read"
    STATE_INSPECTION = "idempotent_state_inspection"
    CONDITIONAL_WRITE = "conditional_write"
    MUTATION = "non_idempotent_mutation"
    EXTERNAL = "external_side_effect"
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    RETRY = "retry"
    BACKOFF = "backoff"
    COMPACT = "compact_context"
    REDUCE_OUTPUT = "reduce_tool_output"
    REFRESH_CAPABILITIES = "refresh_capabilities"
    RECONCILE = "reconcile"
    INTERVENE = "request_user_intervention"
    FAIL = "fail"
    STOP = "stop"


@dataclass
class RecoveryLimits:
    request_retries: int = 2
    run_retries: int = 6
    max_requests: int = 100
    max_elapsed: float = 900
    base_delay: float = .25
    overload_base_delay: float = 2
    max_delay: float = 30
    max_tokens: int | None = None
    max_cost: float | None = None
    repeated_tool_failures: int = 3

    def __post_init__(self):
        for key in ("request_retries", "run_retries", "max_requests", "repeated_tool_failures"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if key in {"max_requests", "repeated_tool_failures"} else 0):
                raise ValueError(f"Invalid recovery limit: {key}")
        for key in ("max_elapsed", "base_delay", "overload_base_delay", "max_delay", "max_tokens", "max_cost"):
            value = getattr(self, key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid recovery limit: {key}")


@dataclass
class RecoveryBudget:
    limits: RecoveryLimits = field(default_factory=RecoveryLimits)
    started: float = field(default_factory=time.monotonic)
    requests: int = 0
    retries: int = 0
    tokens: int = 0
    cost: float = 0
    unknown_usage_requests: int = 0
    metadata_requests: int = 0
    context_capacity_lookups: set[tuple[int, str, str]] = field(default_factory=set, repr=False)

    def snapshot(self) -> dict[str, Any]:
        return {"requests_used": self.requests, "requests_remaining": max(0, self.limits.max_requests - self.requests),
                "retries_used": self.retries, "retries_remaining": max(0, self.limits.run_retries - self.retries),
                "elapsed_seconds": max(0, time.monotonic() - self.started), "max_elapsed": self.limits.max_elapsed,
                "reported_tokens": self.tokens, "reported_cost": self.cost,
                "max_tokens": self.limits.max_tokens, "max_cost": self.limits.max_cost,
                "unknown_usage_requests": self.unknown_usage_requests, "metadata_requests_used": self.metadata_requests}

    def exhausted(self) -> bool:
        return (self.requests >= self.limits.max_requests or time.monotonic() - self.started >= self.limits.max_elapsed
                or (self.limits.max_tokens is not None and self.tokens >= self.limits.max_tokens)
                or (self.limits.max_cost is not None and self.cost >= self.limits.max_cost)
                or (self.unknown_usage_requests > 0 and (self.limits.max_tokens is not None or self.limits.max_cost is not None)))

    def begin_request(self) -> None:
        if self.exhausted():
            raise RuntimeFailure(FailureKind.BUDGET_EXCEEDED, phase=RequestPhase.NOT_SUBMITTED)
        self.requests += 1

    def output_reservation(self, messages, schemas, max_tokens, capabilities) -> int:
        """Conservative local reservation, also used by summary requests.

        Reported usage remains authoritative; estimates cannot impose a hard
        provider billing cap and missing usage prevents further capped work.
        """
        from firik_agent.core.compaction import estimate_tokens
        if time.monotonic() - self.started >= self.limits.max_elapsed or (
                self.unknown_usage_requests and (self.limits.max_tokens is not None or self.limits.max_cost is not None)):
            raise RuntimeFailure(FailureKind.BUDGET_EXCEEDED, phase=RequestPhase.NOT_SUBMITTED)
        output = max_tokens or 2048
        input_tokens = estimate_tokens(messages, schemas)
        if self.limits.max_tokens is not None:
            remaining = self.limits.max_tokens - self.tokens - input_tokens
            if remaining < 1:
                raise RuntimeFailure(FailureKind.BUDGET_EXCEEDED, phase=RequestPhase.NOT_SUBMITTED)
            output = min(output, remaining)
        if self.limits.max_cost is not None:
            price_in, price_out = capabilities.get("input_cost_per_million"), capabilities.get("output_cost_per_million")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                   for value in (price_in, price_out)):
                raise RuntimeFailure(FailureKind.BUDGET_EXCEEDED, phase=RequestPhase.NOT_SUBMITTED)
            remaining = self.limits.max_cost - self.cost - input_tokens * price_in / 1_000_000
            affordable = int(remaining * 1_000_000 / price_out) if price_out else output
            if remaining < 0 or affordable < 1:
                raise RuntimeFailure(FailureKind.BUDGET_EXCEEDED, phase=RequestPhase.NOT_SUBMITTED)
            output = min(output, affordable)
        return int(output)

    def record_usage(self, usage: dict[str, Any]) -> None:
        from firik_agent.projects.model_capabilities import usage_tokens
        counts = usage_tokens(usage)
        total = counts.get("total_tokens")
        cost = usage.get("cost_usd") if usage.get("cost_usd") is not None else (
            usage.get("cost") if str(usage.get("currency", "USD")).upper() == "USD" else None)
        valid = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0
        if valid(total):
            self.tokens += total
        if valid(cost):
            self.cost += cost
        if not valid(total) or (self.limits.max_cost is not None and not valid(cost)):
            self.unknown_usage_requests += 1


@dataclass
class RecoveryContext:
    run_id: str
    attempt_id: str
    failure_kind: FailureKind
    request_phase: RequestPhase
    side_effect_risk: SideEffectRisk
    provider_capabilities: dict[str, Any]
    remaining_budget: RecoveryBudget
    cancellation_status: bool = False
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    attempts_used: int = 0
    retry_after: float | None = None
    operation: str = "model"
    can_compact: bool = False
    can_reduce_output: bool = False


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    delay: float
    retry_limit: int
    reason: str
    requires_approval: bool = False
    safe_to_repeat: bool = False

    def metadata(self) -> dict[str, Any]:
        return {**asdict(self), "action": self.action.value, "recovery_action": self.action.value,
                "retry_eligible": self.action in {RecoveryAction.RETRY, RecoveryAction.BACKOFF, RecoveryAction.COMPACT,
                                                 RecoveryAction.REDUCE_OUTPUT},
                "reconciliation_status": "required" if self.action == RecoveryAction.RECONCILE else "not_required"}


class AdaptiveRecoveryEngine:
    def __init__(self, limits: RecoveryLimits | None = None, *, jitter: Callable[[], float] = random.random):
        self.limits = limits or RecoveryLimits()
        self.jitter = jitter

    def decide(self, context: RecoveryContext) -> RecoveryDecision:
        c, limit = context, self.limits.request_retries
        def result(action, reason, *, safe=False, approval=False, delay=0):
            return RecoveryDecision(action, delay, limit, reason, approval, safe)
        if c.cancellation_status or c.failure_kind == FailureKind.CANCELLED:
            return result(RecoveryAction.STOP, "Cancellation is authoritative.")
        if c.failure_kind in {FailureKind.PERMISSION_DENIED, FailureKind.POLICY_REFUSAL}:
            return result(RecoveryAction.STOP, "Security and provider policy denials cannot be bypassed.")
        if c.failure_kind == FailureKind.AUTHENTICATION_FAILED:
            return result(RecoveryAction.INTERVENE, "Resolve the configured credentials before continuing.", approval=True)
        if c.request_phase == RequestPhase.PARTIAL_RESPONSE:
            return result(RecoveryAction.RECONCILE, "Partial model output must be reviewed before a new request.", approval=True)
        if c.request_phase == RequestPhase.TOOL_DISPATCHED and c.side_effect_risk not in {
                SideEffectRisk.PURE_READ, SideEffectRisk.STATE_INSPECTION}:
            return result(RecoveryAction.RECONCILE, "A dispatched mutation has no confirmed result.", approval=True)
        if c.remaining_budget.exhausted() or c.failure_kind == FailureKind.BUDGET_EXCEEDED:
            return result(RecoveryAction.FAIL, "Resource budget exhausted or usage cannot establish the configured cap.")
        if c.attempts_used >= limit or c.remaining_budget.retries >= self.limits.run_retries:
            return result(RecoveryAction.FAIL, "Bounded request or run retry limit reached.")
        if c.failure_kind == FailureKind.WORKSPACE_CONFLICT:
            return result(RecoveryAction.RECONCILE, "Inspect the current workspace revision before another write.")
        if c.failure_kind == FailureKind.UNSUPPORTED_CAPABILITY:
            return result(RecoveryAction.REFRESH_CAPABILITIES, "Refresh capability metadata; schema changes require a new reviewed request.")
        if c.failure_kind == FailureKind.CONTEXT_OVERFLOW:
            if c.can_reduce_output:
                return result(RecoveryAction.REDUCE_OUTPUT, "Reduce oversized output in the request view; preserve stored evidence.", safe=True)
            if c.can_compact:
                return result(RecoveryAction.COMPACT, "Compact complete transactions through the existing compactor.", safe=True)
            return result(RecoveryAction.INTERVENE, "Use a larger context window or smaller input.", approval=True)
        safe = (c.operation == "model" and (c.request_phase == RequestPhase.NOT_SUBMITTED
                or c.failure_kind in {FailureKind.RATE_LIMITED, FailureKind.PROVIDER_OVERLOADED})) or (
                c.operation == "tool" and (c.request_phase == RequestPhase.TOOL_NOT_DISPATCHED
                or c.side_effect_risk in {SideEffectRisk.PURE_READ, SideEffectRisk.STATE_INSPECTION}))
        eligible = c.failure_kind in {FailureKind.TRANSPORT_ERROR, FailureKind.RATE_LIMITED,
                                     FailureKind.PROVIDER_OVERLOADED, FailureKind.STREAM_INTERRUPTED, FailureKind.TOOL_TIMEOUT}
        if safe and eligible:
            # A busy model server needs a longer recovery interval than a
            # failed connection. Keep both waits within the same run/retry caps.
            base_delay = (max(self.limits.base_delay, self.limits.overload_base_delay)
                          if c.failure_kind == FailureKind.PROVIDER_OVERLOADED else self.limits.base_delay)
            delay = min(self.limits.max_delay, base_delay * (2 ** c.attempts_used)) * (.5 + .5 * self.jitter())
            if c.retry_after is not None:
                # Never shorten a valid provider Retry-After. Fail if it exceeds
                # our bounded run wait instead of hammering the endpoint early.
                delay = max(delay, c.retry_after)
            remaining_time = self.limits.max_elapsed - (time.monotonic() - c.remaining_budget.started)
            if delay > min(self.limits.max_delay, remaining_time):
                return result(RecoveryAction.INTERVENE, "Provider retry delay exceeds the available wait budget.")
            return result(RecoveryAction.BACKOFF if delay else RecoveryAction.RETRY,
                          "Transient failure at a proven safe retry boundary.", safe=True, delay=delay)
        if c.failure_kind == FailureKind.TOOL_OUTCOME_UNKNOWN:
            return result(RecoveryAction.RECONCILE, "Inspect the operation outcome before repeating it.", approval=True)
        return result(RecoveryAction.FAIL, "No proven safe automatic recovery is available.")


async def interruptible_backoff(delay: float, checkpoint: Callable[[], Awaitable[None]]) -> None:
    deadline = time.monotonic() + delay
    while True:
        await checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(.05, remaining))


def tool_semantics(name: str, definition: Any = None) -> SideEffectRisk:
    # Third-party names and MCP annotations alone never establish safety.
    source = getattr(definition, "source", "")
    if source.startswith(("mcp", "plugin", "skill")):
        return SideEffectRisk.UNKNOWN
    if name in {"read_file", "list_files", "search_project", "search_symbols", "get_environment_info", "calculate"}:
        return SideEffectRisk.PURE_READ
    if name in {"git_status", "git_diff", "git_inspect", "discover_quality_gates"}:
        return SideEffectRisk.STATE_INSPECTION
    if name in {"edit_file", "apply_patch", "write_file", "delete_file", "undo_change"}:
        return SideEffectRisk.CONDITIONAL_WRITE
    if name in {"run_command", "run_tests", "run_quality_gate", "run_local_command"}:
        return SideEffectRisk.EXTERNAL
    if name in {"update_plan", "record_verification"}:
        return SideEffectRisk.MUTATION
    return SideEffectRisk.UNKNOWN


def operation_fingerprint(name: str, arguments: str) -> str:
    try:
        arguments = json.loads(arguments or "{}")
    except (ValueError, TypeError):
        pass
    return hashlib.sha256(json.dumps([name, arguments], sort_keys=True).encode()).hexdigest()


class BudgetedCompactionProvider:
    """Keep the existing compactor, while applying the same resource reserve."""
    def __init__(self, provider, budget: RecoveryBudget, capabilities: dict[str, Any], run_control=None):
        self.provider, self.budget, self.capabilities = provider, budget, capabilities
        self.run_control = run_control
        self.name, self.current_model = getattr(provider, "name", ""), getattr(provider, "current_model", "")

    async def chat(self, **kwargs):
        kwargs["max_tokens"] = self.budget.output_reservation(kwargs["messages"], kwargs.get("tools") or [],
                                                            kwargs.get("max_tokens"), self.capabilities)
        control = self.run_control
        intent = control.durable_begin("model:compaction", side_effect="external_side_effect",
            input_data={"messages": [m.to_dict() for m in kwargs["messages"]],
                        "max_tokens": kwargs["max_tokens"]},
            metadata={"kind": "model", "paid_request": True, "compaction": True}) if control else None
        if intent:
            control.durable_transition(intent["attempt_id"], "dispatching")
            control.durable_transition(intent["attempt_id"], "running")
        try:
            result = await self.provider.chat(**kwargs)
        except BaseException:
            if intent:
                control.durable_finish(intent["attempt_id"], "outcome_unknown")
            raise
        if intent:
            control.durable_boundary("after_model_output", attempt_id=intent["attempt_id"])
            control.durable_finish(intent["attempt_id"], result={"response": result.to_dict()})
        return result
