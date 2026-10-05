from __future__ import annotations
import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx

from evren_agent.core.types import (
    Message,
    ModelInfo,
    StreamChunk,
    ToolCall,
    ToolCallFunction,
)
from evren_agent.providers.base import BaseProvider

logger = logging.getLogger(__name__)

DEFAULT_EVREN_DIRECT_URL = "https://evren-llmapi.ssyz.org.tr/v1"
DEFAULT_LLMTR_GATEWAY_URL = "https://llmtr.com/v1"

EVREN_KNOWN_MODELS = [
    # Direct Evren models
    ModelInfo(
        id="glm-5.3",
        name="GLM 5.3 (Direct Evren)",
        context_window=512000,
        supports_tools=True,
        description="GLM-5.3 general reasoning and tool-calling model hosted by EVREN",
    ),
    ModelInfo(
        id="deepseek-v4-flash",
        name="DeepSeek V4 Flash (Direct Evren)",
        context_window=1000000,
        supports_tools=True,
        description="Fast reasoning model with 1M context on EVREN H200 cluster",
    ),
    ModelInfo(
        id="qwen3.8-flash-next",
        name="Qwen 3.8 Flash Next",
        context_window=256000,
        supports_tools=True,
        description="High-speed model for multi-turn conversations",
    ),
    ModelInfo(
        id="gemma-4-31b",
        name="Gemma 4 31B",
        context_window=256000,
        supports_tools=True,
        description="Turkish and multilingual high-accuracy instruction model",
    ),
    ModelInfo(
        id="qwen3-vl-30b",
        name="Qwen 3 VL 30B (Vision)",
        context_window=256000,
        supports_tools=True,
        supports_vision=True,
        description="Vision-language model for image understanding and document analysis",
    ),
    ModelInfo(
        id="auto",
        name="Evren Auto Router",
        context_window=512000,
        supports_tools=True,
        description="Smart routing across EVREN model fleet",
    ),
    # LLMTR Gateway routed models
    ModelInfo(
        id="evren/glm-5.3-fp8",
        name="Evren GLM 5.3 FP8 (LLMTR Gateway)",
        context_window=512000,
        supports_tools=True,
        description="Zero-cost pass-through to EVREN via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/deepseek-v4-flash-tr",
        name="Evren DeepSeek V4 Flash TR (LLMTR Gateway)",
        context_window=1000000,
        supports_tools=True,
        description="Zero-cost pass-through to EVREN DeepSeek via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/gemma-4-31b",
        name="Evren Gemma 4 31B (LLMTR Gateway)",
        context_window=256000,
        supports_tools=True,
        description="Zero-cost pass-through to EVREN Gemma via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/qwen3.8-flash-next",
        name="Evren Qwen 3.8 Flash Next (LLMTR Gateway)",
        context_window=256000,
        supports_tools=True,
        description="Zero-cost pass-through to EVREN Qwen via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/qwen3-vl-30b",
        name="Evren Qwen 3 VL 30B (LLMTR Gateway)",
        context_window=256000,
        supports_tools=True,
        supports_vision=True,
        description="Zero-cost pass-through to EVREN Vision via LLMTR gateway",
    ),
]


class EvrenProvider(BaseProvider):
    """
    EVREN Provider supporting:
    - Direct EVREN endpoint (https://evren-llmapi.ssyz.org.tr/v1)
    - LLMTR Gateway endpoint (https://llmtr.com/v1)
    - Reasoning extraction (delta.reasoning / delta.reasoning_content)
    - OpenAI-compatible tool/function calling
    - Explicit terms status, text, and acceptance
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_EVREN_DIRECT_URL,
        default_model: Optional[str] = None,
        name: str = "evren",
        is_gateway: bool = False,
    ):
        # Infer default model based on base_url / gateway mode
        if default_model is None:
            default_model = "evren/glm-5.3-fp8" if ("llmtr.com" in base_url or is_gateway) else "glm-5.3"

        super().__init__(
            name=name,
            base_url=base_url,
            api_key=api_key,
            default_model=default_model,
        )
        self.is_gateway = is_gateway or ("llmtr.com" in base_url)

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "EvrenAgent/0.2.4",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            # Direct EVREN also accepts X-API-Key
            if not self.is_gateway:
                headers["X-API-Key"] = self.api_key
        return headers

    async def check_terms_status(self) -> Dict[str, Any]:
        """Check EVREN terms acceptance status (direct endpoint)."""
        if self.is_gateway:
            return {"accepted": True, "note": "Gateway does not require direct terms endpoint check."}

        url = f"{self.base_url}/terms/status"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self._get_headers())
            resp.raise_for_status()
            return resp.json()

    async def get_terms_text(self) -> Dict[str, Any]:
        if self.is_gateway:
            raise ValueError("Terms endpoints are available only on direct EVREN.")
        from evren_agent.api import EvrenAPI
        async with EvrenAPI(self.api_key or "", self.base_url) as api:
            return await api.terms_text()

    async def accept_terms(self, version: int) -> Dict[str, Any]:
        """Accept EVREN terms by submitting current_version."""
        if self.is_gateway:
            raise ValueError("Terms acceptance is available only on direct EVREN.")

        url = f"{self.base_url}/terms/accept"
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("Terms version must be an integer.")
        payload = {"version": version}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=self._get_headers())
            resp.raise_for_status()
            return resp.json()

    async def ensure_terms_accepted(self) -> None:
        """Verify terms without silently accepting a legal agreement."""
        if self.is_gateway or not self.api_key:
            return
        status = await self.check_terms_status()
        if not status.get("accepted", False):
            raise RuntimeError("Read `evren terms text`, then explicitly accept with `evren terms accept <version>`.")

    def _prepare_payload(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        supports_vision = False
        for m_info in EVREN_KNOWN_MODELS:
            if m_info.id == self.current_model and m_info.supports_vision:
                supports_vision = True
                break
        if not supports_vision and any(v in (self.current_model or "").lower() for v in ("vl", "vision")):
            supports_vision = True


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
                # Clean up empty optional fields
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

        if stream:
            payload["stream_options"] = {"include_usage": True}

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
                self._handle_error_response(resp)

            data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            return Message(role="assistant", content="")

        msg_data = choices[0].get("message", {})
        content = msg_data.get("content")
        reasoning = (
            msg_data.get("reasoning")
            or msg_data.get("reasoning_content")
            or None
        )

        tool_calls = None
        if "tool_calls" in msg_data and msg_data["tool_calls"]:
            tool_calls = []
            for tc in msg_data["tool_calls"]:
                tool_calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        type="function",
                        function=ToolCallFunction(
                            name=tc.get("function", {}).get("name", ""),
                            arguments=tc.get("function", {}).get("arguments", ""),
                        ),
                    )
                )

        return Message(
            role="assistant",
            content=content,
            reasoning=reasoning,
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
        headers = self._get_headers()

        tool_calls_accumulator: Dict[int, Dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=180.0) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_msg = f"HTTP {response.status_code}: {body.decode('utf-8', errors='replace')}"
                    raise RuntimeError(f"Evren API Error: {error_msg}")

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

                        usage = data.get("usage")
                        choices = data.get("choices", [])
                        if not choices:
                            if usage:
                                yield StreamChunk(usage=usage)
                            continue

                        delta = choices[0].get("delta", {})

                        # Check for text delta
                        content_delta = delta.get("content")

                        # Check for reasoning/thinking delta (supported by EVREN deepseek/glm)
                        reasoning_delta = (
                            delta.get("reasoning")
                            or delta.get("reasoning_content")
                        )

                        # Check for streaming tool calls
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
                            assembled_tools = [
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
                            yield StreamChunk(
                                tool_calls=assembled_tools,
                                is_done=True,
                                usage=usage,
                            )
                        else:
                            if content_delta or reasoning_delta:
                                yield StreamChunk(
                                    delta_content=content_delta,
                                    delta_reasoning=reasoning_delta,
                                    usage=usage,
                                )

    def _handle_error_response(self, response: httpx.Response) -> None:
        try:
            error_data = response.json()
            err = error_data.get("error", {})
            err_type = err.get("type", "unknown_error")
            err_msg = err.get("message", response.text)
        except Exception:
            err_type = "http_error"
            err_msg = response.text

        msg = f"Evren API error ({response.status_code}, {err_type}): {err_msg}"
        logger.error(msg)
        raise RuntimeError(msg)

    async def list_models(self) -> List[ModelInfo]:
        """Query models from API or return known catalogue."""
        try:
            url = f"{self.base_url}/models"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    models = []
                    for item in data.get("data", []):
                        m_id = item.get("id", "")
                        models.append(
                            ModelInfo(
                                id=m_id,
                                name=f"{m_id} ({item.get('owned_by', 'evren')})",
                                context_window=128000,
                                supports_tools=True,
                            )
                        )
                    if models:
                        return models
        except Exception as e:
            logger.debug("Could not fetch remote models from %s: %s", self.base_url, e)

        # Fallback to local catalog
        if self.is_gateway:
            return [m for m in EVREN_KNOWN_MODELS if m.id.startswith("evren/")]
        return [m for m in EVREN_KNOWN_MODELS if not m.id.startswith("evren/")]
