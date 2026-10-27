"""Deterministic provider faults; no credentials, sockets or external model clients."""
from __future__ import annotations
import asyncio
import hashlib
import json
from firik_agent.benchmarks.manifests import TaskManifest
from firik_agent.core.types import StreamChunk, ToolCall, ToolCallFunction
from firik_agent.projects.model_capabilities import usage_tokens
from firik_agent.providers.base import BaseProvider


class ReplayBudgetExceeded(RuntimeError):
    pass


class ReplayRateLimit(RuntimeError):
    pass


class ReplayContextExhausted(RuntimeError):
    pass


class ReplayInterrupted(RuntimeError):
    pass


class ReplayIncompleteToolStream(RuntimeError):
    pass


class ReplayProvider(BaseProvider):
    """No HTTP client, credentials or provider configuration is consulted."""

    def __init__(self, manifest: TaskManifest):
        super().__init__("replay", "offline://replay", default_model="scripted-v1")
        self.manifest, self.index, self.tokens = manifest, 0, 0
        self.fixture_cancelled = False
        self.unknown_usage_requests = 0

    def _substitute(self, value, messages):
        if isinstance(value, dict):
            return {key: self._substitute(item, messages) for key, item in value.items()}
        if isinstance(value, list):
            return [self._substitute(item, messages) for item in value]
        if not isinstance(value, str):
            return value
        if value.startswith("$initial:"):
            return hashlib.sha256(self.manifest.files[value[9:]].encode()).hexdigest()
        if value.startswith("$hash:"):
            path = value[6:]
            for message in reversed(messages):
                if message.role == "tool" and message.name == "read_file":
                    result = json.loads(message.content)
                    if result.get("path") == path and result.get("hash"):
                        return result["hash"]
            raise ValueError("Replay hash reference has no observed read result.")
        if value == "$evidence":
            for message in reversed(messages):
                if message.role == "tool" and message.name == "run_tests":
                    result = json.loads(message.content)
                    if result.get("passed") and result.get("evidence_id"):
                        return result["evidence_id"]
            raise ValueError("Replay evidence reference has no successful executed evaluator.")
        return value

    async def chat_stream(self, messages, **kwargs):
        if self.index >= len(self.manifest.turns):
            raise ReplayBudgetExceeded("Replay script exhausted; no unscripted provider fallback.")
        turn = self.manifest.turns[self.index]
        self.index += 1
        usage = usage_tokens(turn.usage)
        tokens = usage["total_tokens"]
        lower_bound = tokens if tokens is not None else (usage["input_tokens"] or 0) + (usage["output_tokens"] or 0)
        if self.tokens + lower_bound > self.manifest.token_budget:
            raise ReplayBudgetExceeded("Replay token budget exhausted.")
        self.tokens += lower_bound
        if tokens is None:
            self.unknown_usage_requests += 1
        if turn.fault == "timeout":
            await asyncio.sleep(60)
        if turn.fault == "rate_limit":
            raise ReplayRateLimit("Injected rate limit; no retry is authorized.")
        if turn.fault == "context_exhaustion":
            raise ReplayContextExhausted("Injected provider context exhaustion.")
        if turn.fault == "interrupt":
            raise ReplayInterrupted("Injected interrupted execution.")
        if turn.fault == "transport":
            raise ConnectionError("Injected offline transport failure.")
        if turn.fault == "cancel":
            self.fixture_cancelled = True
            raise asyncio.CancelledError()
        calls = [ToolCall(id=f"replay_{self.index}_{index}", function=ToolCallFunction(
            name=call.name, arguments=call.raw_arguments if call.raw_arguments is not None else json.dumps(self._substitute(call.arguments, messages))))
            for index, call in enumerate(turn.calls)]
        if turn.fault == "incomplete_tool":
            yield StreamChunk(delta_content="Partial tool arguments received.")
            raise ReplayIncompleteToolStream("Tool-call stream ended before arguments were complete.")
        yield StreamChunk(delta_content=turn.text or None, tool_calls=calls or None, usage=turn.usage)
        for _ in range(turn.duplicate_usage_frames):
            yield StreamChunk(usage=turn.usage)
        if turn.fault == "partial":
            raise ConnectionError("Injected offline partial stream failure.")

    async def chat(self, *args, **kwargs):
        raise RuntimeError("Replay does not allow implicit nonstream fallback.")

    async def list_models(self):
        return []

