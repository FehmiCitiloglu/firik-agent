"""Shared OpenAI-compatible response boundary; no retries or fallback here."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

from firik_agent.core.failures import FailureKind, RequestPhase, RuntimeFailure, normalize_failure, provider_failure
from firik_agent.core.types import Message, StreamChunk, ToolCall, ToolCallFunction


def _refusal(choice: dict[str, Any], message: dict[str, Any]) -> bool:
    return bool(message.get("refusal")) or choice.get("finish_reason") in {"content_filter", "refusal", "safety"}


def _calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    return [ToolCall(id=call.get("id", ""), function=ToolCallFunction(
        name=call.get("function", {}).get("name", ""),
        arguments=call.get("function", {}).get("arguments", ""))) for call in raw]


def prepare_payload(provider: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return provider._prepare_payload(*args, **kwargs)
    except Exception as error:
        raise normalize_failure(error, phase=RequestPhase.NOT_SUBMITTED, provider=provider.name) from None


async def chat(provider: Any, payload: dict[str, Any]) -> Message:
    phase = RequestPhase.NOT_SUBMITTED
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            phase = RequestPhase.SUBMITTED_NO_OUTPUT
            response = await client.post(f"{provider.base_url}/chat/completions", json=payload,
                                         headers=provider._get_headers())
            if response.status_code != 200:
                raise provider_failure(response, provider=provider.name)
            data = response.json()
            if data.get("error"):
                raise provider_failure(response, provider=provider.name)
        choices = data.get("choices", [])
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        if not choices:
            return Message(role="assistant", content="", usage=usage)
        choice = choices[0]
        message = choice.get("message", {})
        if _refusal(choice, message):
            raise RuntimeFailure(FailureKind.POLICY_REFUSAL, provider=provider.name)
        return Message(role="assistant", content=message.get("content"),
                       reasoning=message.get("reasoning") or message.get("reasoning_content"),
                       tool_calls=_calls(message["tool_calls"]) if message.get("tool_calls") else None,
                       usage=usage)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise normalize_failure(error, phase=phase, provider=provider.name) from None


async def chat_stream(provider: Any, payload: dict[str, Any]) -> AsyncIterator[StreamChunk]:
    phase = RequestPhase.NOT_SUBMITTED
    accumulator: dict[int, dict[str, str]] = {}
    finished = False
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            phase = RequestPhase.SUBMITTED_NO_OUTPUT
            async with client.stream("POST", f"{provider.base_url}/chat/completions", json=payload,
                                     headers=provider._get_headers()) as response:
                if response.status_code != 200:
                    await response.aread()
                    raise provider_failure(response, provider=provider.name)
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    lines = buffer.split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        encoded = line[len("data:"):].strip()
                        if encoded == "[DONE]":
                            if accumulator:
                                raise RuntimeFailure(FailureKind.STREAM_INTERRUPTED, phase=phase, provider=provider.name)
                            finished = True
                            yield StreamChunk(is_done=True)
                            continue
                        try:
                            data = json.loads(encoded)
                        except json.JSONDecodeError:
                            raise RuntimeFailure(FailureKind.STREAM_INTERRUPTED, phase=phase, provider=provider.name) from None
                        if not isinstance(data, dict):
                            raise RuntimeFailure(FailureKind.STREAM_INTERRUPTED, phase=phase, provider=provider.name)
                        if data.get("error"):
                            # Embedded SSE errors use the same classification as HTTP
                            # errors; no provider message is retained.
                            error_response = httpx.Response(200, json=data)
                            raise provider_failure(error_response, provider=provider.name, phase=phase)
                        usage = data.get("usage")
                        if isinstance(usage, dict) and usage:
                            yield StreamChunk(usage=usage)
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        if _refusal(choice, delta):
                            raise RuntimeFailure(FailureKind.POLICY_REFUSAL, phase=phase, provider=provider.name)
                        content = delta.get("content")
                        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                        raw_calls = delta.get("tool_calls")
                        if content or reasoning or raw_calls:
                            phase = RequestPhase.PARTIAL_RESPONSE
                        for raw in raw_calls or []:
                            index = raw.get("index", 0)
                            item = accumulator.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if raw.get("id"):
                                item["id"] = raw["id"]
                            function = raw.get("function", {})
                            item["name"] += function.get("name") or ""
                            item["arguments"] += function.get("arguments") or ""
                        finish = choice.get("finish_reason")
                        if finish is not None:
                            finished = True
                        if finish == "tool_calls" and accumulator:
                            calls = [ToolCall(id=value["id"] or f"call_{index}", function=ToolCallFunction(
                                name=value["name"], arguments=value["arguments"])) for index, value in accumulator.items()]
                            accumulator.clear()
                            yield StreamChunk(tool_calls=calls, is_done=True)
                        elif content or reasoning:
                            yield StreamChunk(delta_content=content, delta_reasoning=reasoning)
                if accumulator or buffer.strip() or not finished:
                    raise RuntimeFailure(FailureKind.STREAM_INTERRUPTED, phase=phase, provider=provider.name)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise normalize_failure(error, phase=phase, provider=provider.name) from None
