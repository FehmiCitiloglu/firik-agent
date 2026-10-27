from __future__ import annotations
import logging
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx

from firik_agent.core.types import (
    Message,
    ModelInfo,
    StreamChunk,
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
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Message:
        from firik_agent.providers._chat import chat, prepare_payload
        return await chat(self, prepare_payload(self, messages, tools, temperature, max_tokens, stream=False))

    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        from firik_agent.providers._chat import chat_stream, prepare_payload
        async for chunk in chat_stream(self, prepare_payload(self, messages, tools, temperature, max_tokens, stream=True)):
            yield chunk

    async def list_models(self) -> List[ModelInfo]:
        try:
            url = f"{self.base_url}/models"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 200:
                    data = resp.json()
                    rows = data.get("data", []) if isinstance(data, dict) else []
                    self.model_metadata = {str(row["id"]): {**row, "source": "provider"}
                                           for row in rows if isinstance(row, dict) and row.get("id")}
                    return [self.model_info(row) for row in self.model_metadata.values()]
        except Exception as e:
            logger.debug("Failed listing models: %s", type(e).__name__)

        # Configured model remains selectable during an outage; it is not a
        # successful discovery or evidence of capabilities.
        return [self.model_info({"id": self.current_model})] if self.current_model else []
