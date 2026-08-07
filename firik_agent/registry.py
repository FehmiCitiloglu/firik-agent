"""Firik Agent model metadata and lifecycle registry."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ModelStatus(StrEnum):
    LOADED = "loaded"
    EJECTED = "ejected"
    LOADING = "loading"
    EJECTING = "ejecting"


@dataclass(slots=True)
class ModelUsageStats:
    total_generations: int = 0
    total_tokens_generated: int = 0
    total_inference_time_seconds: float = 0.0
    first_load_timestamp: float | None = None

    def update(self, tokens: int = 0, inference_time: float = 0.0) -> None:
        self.total_generations += 1
        self.total_tokens_generated += tokens
        self.total_inference_time_seconds += inference_time

    @property
    def average_inference_time(self) -> float:
        if not self.total_generations:
            return 0.0
        return self.total_inference_time_seconds / self.total_generations


@dataclass(slots=True)
class ModelMetadata:
    model_id: str
    pipeline_type: str = "text-generation"
    library_name: str = "transformers"
    tags: list[str] = field(default_factory=list)
    max_model_length: int = 4096
    temperature: float = 1.0
    top_p: float = 1.0
    max_new_tokens: int = 512
    status: ModelStatus = ModelStatus.EJECTED
    loaded_at: float | None = None
    usage_stats: ModelUsageStats = field(default_factory=ModelUsageStats)
    description: str | None = None
    author: str | None = None

    def is_loaded(self) -> bool:
        return self.status is ModelStatus.LOADED

    def load_time(self) -> float | None:
        return time.time() - self.loaded_at if self.loaded_at is not None else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class ModelRegistry:
    """In-memory registry for model metadata."""

    def __init__(self) -> None:
        self._models: dict[str, ModelMetadata] = {}

    def register(self, model_id: str, **configuration: Any) -> ModelMetadata:
        metadata = ModelMetadata(model_id=model_id, **configuration)
        self._models[model_id] = metadata
        return metadata

    def unregister(self, model_id: str) -> ModelMetadata | None:
        return self._models.pop(model_id, None)

    def get(self, model_id: str) -> ModelMetadata | None:
        return self._models.get(model_id)

    def get_loaded_models(self) -> list[ModelMetadata]:
        return [metadata for metadata in self._models.values() if metadata.is_loaded()]

    def get_all_models(self) -> list[ModelMetadata]:
        return list(self._models.values())

    def clear(self) -> None:
        self._models.clear()

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __len__(self) -> int:
        return len(self._models)

    def __iter__(self) -> Iterator[ModelMetadata]:
        return iter(self._models.values())
