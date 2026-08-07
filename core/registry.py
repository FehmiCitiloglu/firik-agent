"""Model Registry - Tracks model metadata, load status, and usage statistics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ModelStatus(Enum):
    """Model lifecycle status."""

    LOADED = "loaded"
    EJECTED = "ejected"
    LOADING = "loading"
    EJECTING = " ejecting"


@dataclass
class ModelUsageStats:
    """Tracks usage statistics for a loaded model."""

    total_generations: int = 0
    total_tokens_generated: int = 0
    total_inference_time_seconds: float = 0.0
    first_load_timestamp: Optional[float] = None

    def update(self, tokens: int = 0, inference_time: float = 0.0) -> None:
        """Update usage statistics."""
        self.total_generations += 1
        self.total_tokens_generated += tokens
        self.total_inference_time_seconds += inference_time

    @property
    def avg_inference_time(self) -> float:
        """Average inference time per generation."""
        if self.total_generations == 0:
            return 0.0
        return self.total_inference_time_seconds / self.total_generations


@dataclass
class ModelMetadata:
    """Comprehensive metadata for a model."""

    # Core identifiers
    model_id: str
    pipeline_type: Optional[str] = None  # "text-generation", "sentiment-analysis", etc.
    library_name: Optional[str] = None  # "transformers", "peft", etc.
    tags: list[str] = field(default_factory=list)

    # Configuration
    max_model_length: int = 4096
    temperature: float = 1.0
    top_p: float = 1.0
    max_new_tokens: int = 512

    # State tracking
    status: ModelStatus = ModelStatus.EJECTED
    loaded_at: Optional[float] = None

    # Performance metrics
    usage_stats: ModelUsageStats = field(default_factory=ModelUsageStats)

    # Additional info
    description: Optional[str] = None
    author: Optional[str] = None

    def is_loaded(self) -> bool:
        """Check if model is currently loaded."""
        return self.status == ModelStatus.LOADED

    def load_time(self) -> Optional[float]:
        """Time since model was loaded."""
        if self.loaded_at is None:
            return None
        return time.time() - self.loaded_at

    def to_dict(self) -> dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "model_id": self.model_id,
            "pipeline_type": self.pipeline_type,
            "library_name": self.library_name,
            "tags": self.tags,
            "max_model_length": self.max_model_length,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_new_tokens": self.max_new_tokens,
            "status": self.status.value,
            "loaded_at": self.loaded_at,
            "description": self.description,
            "author": self.author,
        }


class ModelRegistry:
    """Central registry for all models and their metadata."""

    def __init__(self) -> None:
        self._models: dict[str, ModelMetadata] = {}

    def register(
        self,
        model_id: str,
        pipeline_type: Optional[str] = None,
        library_name: Optional[str] = None,
        max_model_length: int = 4096,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_new_tokens: int = 512,
    ) -> ModelMetadata:
        """Register a new model in the registry."""
        metadata = ModelMetadata(
            model_id=model_id,
            pipeline_type=pipeline_type or "text-generation",
            library_name=library_name or "transformers",
            max_model_length=max_model_length,
            temperature=temperature,
            top_p=top_p,
            max_new_tokens=max_new_tokens,
        )
        self._models[model_id] = metadata
        return metadata

    def unregister(self, model_id: str) -> Optional[ModelMetadata]:
        """Remove a model from the registry."""
        return self._models.pop(model_id, None)

    def get(self, model_id: str) -> Optional[ModelMetadata]:
        """Get model metadata by ID."""
        return self._models.get(model_id)

    def get_loaded_models(self) -> list[ModelMetadata]:
        """Get all currently loaded models."""
        return [m for m in self._models.values() if m.is_loaded()]

    def get_all_models(self) -> list[ModelMetadata]:
        """Get all registered models."""
        return list(self._models.values())

    def clear(self) -> None:
        """Clear all models from registry."""
        self._models.clear()

    def __contains__(self, model_id: str) -> bool:
        """Check if model is registered."""
        return model_id in self._models

    def __len__(self) -> int:
        """Number of registered models."""
        return len(self._models)

    def __iter__(self):
        """Iterate over registered models."""
        return iter(self._models.values())
