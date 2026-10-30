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

DEFAULT_FIRIK_DIRECT_URL = "https://evren-llmapi.ssyz.org.tr/v1"
DEFAULT_LLMTR_GATEWAY_URL = "https://llmtr.com/v1"

# Deployment limits read from the direct endpoint's /models on 2026-10-30.
# These are exact endpoint/model fallbacks, not capabilities inferred from names.
FIRIK_CONTEXT_WINDOWS = {
    "glm-5.3": 524288,
    "mimo-v2.6-pro": 1048576,
    "deepseek-v4.1-flash": 1048576,
    "qwen3.8-flash-next": 262144,
    "gemma-4-31b": 262144,
    "qwen3-vl-30b": 262144,
    "auto": 32768,
}

FIRIK_KNOWN_MODELS = [
    # Direct Firik models
    ModelInfo(
        id="glm-5.3",
        name="GLM 5.3 (Direct Firik)",
        context_window=524288,
        supports_tools=True,
        description="GLM-5.3 general reasoning and tool-calling model hosted by FIRIK",
    ),
    ModelInfo(
        id="mimo-v2.6-pro",
        name="MiMo V2.6 Pro (Direct Firik)",
        context_window=1048576,
        supports_tools=True,
    ),
    ModelInfo(
        id="deepseek-v4.1-flash",
        name="DeepSeek V4.1 Flash (Direct Firik)",
        context_window=1048576,
        supports_tools=True,
    ),
    ModelInfo(
        id="deepseek-v4-flash",
        name="DeepSeek V4 Flash (Direct Firik)",
        context_window=1000000,
        supports_tools=True,
        description="Fast reasoning model with 1M context on FIRIK H200 cluster",
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
        name="Firik Auto Router",
        context_window=32768,
        supports_tools=True,
        description="Smart routing across FIRIK model fleet",
    ),
    # LLMTR Gateway routed models
    ModelInfo(
        id="evren/glm-5.3-fp8",
        name="Firik GLM 5.3 FP8 (LLMTR Gateway)",
        context_window=512000,
        supports_tools=True,
        description="Zero-cost pass-through to FIRIK via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/deepseek-v4-flash-tr",
        name="Firik DeepSeek V4 Flash TR (LLMTR Gateway)",
        context_window=1000000,
        supports_tools=True,
        description="Zero-cost pass-through to FIRIK DeepSeek via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/gemma-4-31b",
        name="Firik Gemma 4 31B (LLMTR Gateway)",
        context_window=256000,
        supports_tools=True,
        description="Zero-cost pass-through to FIRIK Gemma via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/qwen3.8-flash-next",
        name="Firik Qwen 3.8 Flash Next (LLMTR Gateway)",
        context_window=256000,
        supports_tools=True,
        description="Zero-cost pass-through to FIRIK Qwen via LLMTR gateway",
    ),
    ModelInfo(
        id="evren/qwen3-vl-30b",
        name="Firik Qwen 3 VL 30B (LLMTR Gateway)",
        context_window=256000,
        supports_tools=True,
        supports_vision=True,
        description="Zero-cost pass-through to FIRIK Vision via LLMTR gateway",
    ),
]


class FirikProvider(BaseProvider):
    """
    FIRIK Provider supporting:
    - Direct FIRIK endpoint (https://evren-llmapi.ssyz.org.tr/v1)
    - LLMTR Gateway endpoint (https://llmtr.com/v1)
    - Reasoning extraction (delta.reasoning / delta.reasoning_content)
    - OpenAI-compatible tool/function calling
    - Explicit terms status, text, and acceptance
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_FIRIK_DIRECT_URL,
        default_model: Optional[str] = None,
        name: str = "firik",
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
        self.model_metadata = self.context_metadata_defaults()

    def context_metadata_defaults(self) -> Dict[str, Dict[str, Any]]:
        """Offline capacity evidence for this deployment only; live rows win."""
        if self.base_url != DEFAULT_FIRIK_DIRECT_URL or self.is_gateway:
            return {}
        return {model: {"id": model, "context_window": window, "source": "verified_config",
                        "evidence_url": DEFAULT_FIRIK_DIRECT_URL + "/models"}
                for model, window in FIRIK_CONTEXT_WINDOWS.items()}

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "FirikAgent/0.2.4",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            # Direct FIRIK also accepts X-API-Key
            if not self.is_gateway:
                headers["X-API-Key"] = self.api_key
        return headers

    async def check_terms_status(self) -> Dict[str, Any]:
        """Check FIRIK terms acceptance status (direct endpoint)."""
        if self.is_gateway:
            return {"accepted": True, "note": "Gateway does not require direct terms endpoint check."}

        url = f"{self.base_url}/terms/status"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self._get_headers())
            resp.raise_for_status()
            return resp.json()

    async def get_terms_text(self) -> Dict[str, Any]:
        if self.is_gateway:
            raise ValueError("Terms endpoints are available only on direct FIRIK.")
        from firik_agent.api import FirikAPI
        async with FirikAPI(self.api_key or "", self.base_url) as api:
            return await api.terms_text()

    async def accept_terms(self, version: int) -> Dict[str, Any]:
        """Accept FIRIK terms by submitting current_version."""
        if self.is_gateway:
            raise ValueError("Terms acceptance is available only on direct FIRIK.")

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
            raise RuntimeError("Read `firik terms text`, then explicitly accept with `firik terms accept <version>`.")

    def _prepare_payload(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        supports_vision = False
        for m_info in FIRIK_KNOWN_MODELS:
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

    def _handle_error_response(self, response: httpx.Response) -> None:
        from firik_agent.core.failures import provider_failure
        raise provider_failure(response, provider=self.name) from None

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
                        if not isinstance(item, dict) or not item.get("id"):
                            continue
                        m_id = item.get("id", "")
                        metadata = {**item, "source": "provider"}
                        self.model_metadata[m_id] = metadata
                        models.append(self.model_info(metadata))
                    if models:
                        return models
        except Exception as e:
            logger.debug("Could not fetch remote models: %s", type(e).__name__)

        # Fallback to local catalog
        if self.is_gateway:
            return [m for m in FIRIK_KNOWN_MODELS if m.id.startswith("evren/")]
        return [m for m in FIRIK_KNOWN_MODELS if not m.id.startswith("evren/")]
