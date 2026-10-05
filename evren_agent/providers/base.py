from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional
from evren_agent.core.types import Message, ModelInfo, StreamChunk


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
        from evren_agent.providers.reasoning import effort_payload
        return effort_payload(self.model_metadata.get(self.current_model), self.reasoning_effort)

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
