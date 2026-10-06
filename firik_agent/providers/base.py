from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional
import time
from urllib.parse import urlsplit
from firik_agent.core.types import Message, ModelInfo, StreamChunk


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: Optional[str] = None,
        default_model: str = "default",
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.current_model = default_model
        self.model_metadata: Dict[str, Dict[str, Any]] = {}
        self.reasoning_effort: Optional[str] = None

    def set_model(self, model_name: str) -> None:
        self.current_model = model_name

    def reasoning_options(self) -> Dict[str, Any]:
        from firik_agent.providers.reasoning import effort_payload
        return effort_payload(self.model_metadata.get(self.current_model), self.reasoning_effort)

    @property
    def is_local(self) -> bool:
        """Endpoint locality, independent of model names and provider branding."""
        return (urlsplit(self.base_url).hostname or "").casefold() in {"localhost", "127.0.0.1", "::1"}

    def model_info(self, metadata: Dict[str, Any]) -> ModelInfo:
        """Preserve provider evidence without guessing absent capabilities."""
        from firik_agent.projects.model_capabilities import model_capabilities
        capabilities = model_capabilities(metadata)
        fields = {key: value for key, value in capabilities.items() if key in ModelInfo.model_fields}
        return ModelInfo(id=str(metadata.get("id", "")), name=str(metadata.get("name") or metadata.get("id", "")),
                         provider=self.name, local=self.is_local, description=metadata.get("description"), **fields)

    async def endpoint_health(self) -> Dict[str, Any]:
        """A read-only endpoint probe; fallback catalogues cannot report online status."""
        import httpx
        started = time.monotonic()
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.base_url + "/models", headers=headers)
            data = response.json() if response.is_success else {}
            rows = data.get("data", []) if isinstance(data, dict) else []
            return {"provider": self.name, "local": self.is_local, "status": "online" if response.is_success else "error",
                    "latency_ms": round((time.monotonic() - started) * 1000, 1), "http_status": response.status_code,
                    "model": self.current_model, "available_models": len(rows), "source": "endpoint_probe"}
        except Exception as error:
            return {"provider": self.name, "local": self.is_local, "status": "offline",
                    "latency_ms": round((time.monotonic() - started) * 1000, 1), "model": self.current_model,
                    "error_type": type(error).__name__, "source": "endpoint_probe"}

    @abstractmethod
    async def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Message:
        """Send chat messages and return assistant response message."""
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Send chat messages and yield response chunks (content, reasoning, tool calls)."""
        pass

    @abstractmethod
    async def list_models(self) -> List[ModelInfo]:
        """List models available for this provider."""
        pass
