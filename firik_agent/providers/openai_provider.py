from __future__ import annotations
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx

from firik_agent.core.types import (
    Message,
    ModelInfo,
    StreamChunk,
    ToolCall,
    ToolCallFunction,
)
from firik_agent.providers.base import BaseProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseProvider):
    """Generic OpenAI-compatible provider (OpenAI, OpenRouter, Groq, DeepSeek direct, etc.)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o",
        name: str = "openai",
    ):
        super().__init__(
            name=name,
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
        )

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _is_vision_model(self) -> bool:
        mid = (self.current_model or "").lower()
        return any(v in mid for v in ("4o", "vision", "vl", "claude-3", "gemini-1.5", "gemini-2", "gpt-4-turbo"))

    def _prepare_payload(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        supports_vision = self._is_vision_model()
        formatted_messages = []
        for m in messages:
            msg_dict = m.to_dict()
            if supports_vision and m.parts:
                parts_content: List[Dict[str, Any]] = []
                if m.content:
                    parts_content.append({"type": "text", "text": m.content})
                for p in m.parts:
                    if p.type == "text" and p.text:
                        parts_content.append({"type": "text", "text": p.text})
                    elif p.type == "image" and p.data:
                        mime = p.mime_type or "image/png"
                        parts_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{p.data}"},
                        })
                msg_dict["content"] = parts_content
            else:
                if msg_dict.get("content") is None and not msg_dict.get("tool_calls"):
                    msg_dict["content"] = ""
            formatted_messages.append(msg_dict)


        payload: Dict[str, Any] = {
            "model": self.current_model,
            "messages": formatted_messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(self.reasoning_options())
        return payload

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Message:
        payload = self._prepare_payload(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )

        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload, headers=self._get_headers())
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI error ({resp.status_code}): {resp.text}")
            data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            return Message(role="assistant", content="")

        msg_data = choices[0].get("message", {})
        content = msg_data.get("content")
        tool_calls = None
        if "tool_calls" in msg_data and msg_data["tool_calls"]:
            tool_calls = [
                ToolCall(
                    id=tc.get("id", ""),
                    type="function",
                    function=ToolCallFunction(
                        name=tc.get("function", {}).get("name", ""),
                        arguments=tc.get("function", {}).get("arguments", ""),
                    ),
                )
                for tc in msg_data["tool_calls"]
            ]

        return Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._prepare_payload(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        url = f"{self.base_url}/chat/completions"
        tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", url, json=payload, headers=self._get_headers()) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise RuntimeError(f"OpenAI stream error ({response.status_code}): {body.decode('utf-8', errors='replace')}")

                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    lines = buffer.split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue

                        data_str = line[len("data:") :].strip()
                        if data_str == "[DONE]":
                            yield StreamChunk(is_done=True)
                            continue

                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = data.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        content_delta = delta.get("content")

                        raw_tool_calls = delta.get("tool_calls")
                        if raw_tool_calls:
                            for tc_delta in raw_tool_calls:
                                idx = tc_delta.get("index", 0)
                                if idx not in tool_calls_accumulator:
                                    tool_calls_accumulator[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "name": "",
                                        "arguments": "",
                                    }
                                if tc_delta.get("id"):
                                    tool_calls_accumulator[idx]["id"] = tc_delta["id"]
                                fn = tc_delta.get("function", {})
                                if fn.get("name"):
                                    tool_calls_accumulator[idx]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    tool_calls_accumulator[idx]["arguments"] += fn["arguments"]

                        finish_reason = choices[0].get("finish_reason")
                        if finish_reason == "tool_calls" and tool_calls_accumulator:
                            assembled = [
                                ToolCall(
                                    id=v["id"] or f"call_{i}",
                                    type="function",
                                    function=ToolCallFunction(
                                        name=v["name"],
                                        arguments=v["arguments"],
                                    ),
                                )
                                for i, v in tool_calls_accumulator.items()
                            ]
                            yield StreamChunk(tool_calls=assembled, is_done=True)
                        else:
                            if content_delta:
                                yield StreamChunk(delta_content=content_delta)

    async def list_models(self) -> List[ModelInfo]:
        try:
            url = f"{self.base_url}/models"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    return [
                        ModelInfo(
                            id=m.get("id", ""),
                            name=m.get("id", ""),
                            context_window=128000,
                            supports_tools=True,
                        )
                        for m in data.get("data", [])
                    ]
        except Exception as e:
            logger.debug("Failed listing models from %s: %s", self.base_url, e)

        return [
            ModelInfo(id="gpt-4o", name="GPT-4o", context_window=128000, supports_tools=True),
            ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", context_window=128000, supports_tools=True),
        ]
