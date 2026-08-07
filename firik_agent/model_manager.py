"""Backward-compatible local Hugging Face model lifecycle management."""

from __future__ import annotations

import gc
import time
from typing import Any

from .registry import ModelRegistry, ModelStatus


class ModelManager:
    """Load, generate with, and eject Transformers pipelines."""

    def __init__(self, registry: ModelRegistry | None = None, device: str = "auto") -> None:
        self.registry = registry if registry is not None else ModelRegistry()
        self.device = device
        self._loaded_models: dict[str, Any] = {}
        self._tokenizers: dict[str, Any] = {}

    def search_models(
        self,
        query: str = "",
        task: str | None = None,
        limit: int = 10,
        sort: str = "downloads",
    ) -> list[dict[str, Any]]:
        try:
            from huggingface_hub import HfApi
        except ImportError as exc:
            raise ImportError("huggingface-hub is required for model search") from exc
        models = HfApi().list_models(
            search=query,
            task=task or "text-generation",
            sort=sort,
            direction=-1,
            limit=limit,
        )
        return [
            {
                "id": model.id,
                "downloads": model.downloads,
                "likes": getattr(model, "likes", 0),
                "pipeline_tag": getattr(model, "pipeline_tag", None),
                "tags": getattr(model, "tags", []),
            }
            for model in models
        ]

    def load_model(
        self,
        model_id: str,
        pipeline_type: str = "text-generation",
        max_model_length: int = 4096,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_new_tokens: int = 512,
        *,
        trust_remote_code: bool = False,
    ) -> Any:
        if self._is_model_loaded(model_id):
            return self._loaded_models[model_id]
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
        except ImportError as exc:
            raise ImportError("Install the 'local' dependency group") from exc

        metadata = self.registry.register(
            model_id=model_id,
            pipeline_type=pipeline_type,
            max_model_length=max_model_length,
            temperature=temperature,
            top_p=top_p,
            max_new_tokens=max_new_tokens,
        )
        metadata.status = ModelStatus.LOADING
        try:
            if pipeline_type == "text-generation":
                tokenizer = AutoTokenizer.from_pretrained(
                    model_id,
                    trust_remote_code=trust_remote_code,
                )
                if tokenizer.pad_token_id is None:
                    tokenizer.pad_token = tokenizer.eos_token
                model_kwargs: dict[str, Any] = {
                    "trust_remote_code": trust_remote_code,
                    "torch_dtype": "auto",
                }
                device = self._resolve_device()
                if device == "cuda":
                    model_kwargs["device_map"] = "auto"
                model = AutoModelForCausalLM.from_pretrained(model_id, **model_kwargs)
                if device == "mps":
                    model = model.to("mps")
                loaded = pipeline(pipeline_type, model=model, tokenizer=tokenizer)
                self._tokenizers[model_id] = tokenizer
            else:
                loaded = pipeline(pipeline_type, model=model_id, device=self._pipeline_device())
            self._loaded_models[model_id] = loaded
            metadata.loaded_at = time.time()
            metadata.usage_stats.first_load_timestamp = metadata.loaded_at
            metadata.status = ModelStatus.LOADED
            return loaded
        except Exception:
            metadata.status = ModelStatus.EJECTED
            raise

    def generate(
        self, prompt: str, model_id: str | None = None, **overrides: Any
    ) -> dict[str, Any]:
        identifier = model_id or next(iter(self._loaded_models), None)
        if identifier is None:
            raise ValueError("No model is loaded")
        if identifier not in self._loaded_models:
            raise ValueError(f"Model is not loaded: {identifier}")
        metadata = self.registry.get(identifier)
        if metadata is None:
            raise RuntimeError(f"Missing registry entry for loaded model: {identifier}")
        generation = {
            "max_new_tokens": overrides.pop("max_new_tokens", metadata.max_new_tokens),
            "temperature": overrides.pop("temperature", metadata.temperature),
            "top_p": overrides.pop("top_p", metadata.top_p),
            **overrides,
        }
        started = time.monotonic()
        outputs = self._loaded_models[identifier](prompt, **generation)
        duration = time.monotonic() - started
        generated_text = "".join(str(item.get("generated_text", "")) for item in outputs)
        metadata.usage_stats.update(tokens=len(generated_text), inference_time=duration)
        return {"model_id": identifier, "outputs": outputs, "inference_time_seconds": duration}

    def eject_model(self, model_id: str) -> bool:
        if model_id not in self._loaded_models:
            return False
        metadata = self.registry.get(model_id)
        if metadata:
            metadata.status = ModelStatus.EJECTING
        del self._loaded_models[model_id]
        self._tokenizers.pop(model_id, None)
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except (ImportError, RuntimeError):
            pass
        if metadata:
            metadata.status = ModelStatus.EJECTED
        return True

    def eject_all(self) -> list[str]:
        return [model_id for model_id in list(self._loaded_models) if self.eject_model(model_id)]

    def reload(self, model_id: str | None = None, **overrides: Any) -> Any:
        identifier = model_id or next(iter(self._loaded_models), None)
        if identifier is None:
            raise ValueError("No model is loaded")
        metadata = self.registry.get(identifier)
        if metadata is None:
            raise ValueError(f"Model is not registered: {identifier}")
        configuration = {
            "pipeline_type": metadata.pipeline_type,
            "max_model_length": metadata.max_model_length,
            "temperature": metadata.temperature,
            "top_p": metadata.top_p,
            "max_new_tokens": metadata.max_new_tokens,
            **overrides,
        }
        self.eject_model(identifier)
        return self.load_model(identifier, **configuration)

    def get_model_info(self, model_id: str) -> dict[str, Any] | None:
        metadata = self.registry.get(model_id)
        return (
            {**metadata.to_dict(), "is_loaded": self._is_model_loaded(model_id)}
            if metadata
            else None
        )

    def get_memory_usage(self) -> dict[str, Any]:
        result: dict[str, Any] = {"loaded_model_count": len(self._loaded_models)}
        try:
            import torch

            if torch.cuda.is_available():
                result["cuda_allocated_gb"] = torch.cuda.memory_allocated() / 1024**3
                result["cuda_reserved_gb"] = torch.cuda.memory_reserved() / 1024**3
        except ImportError:
            pass
        return result

    @property
    def loaded_models(self) -> list[str]:
        return list(self._loaded_models)

    def _is_model_loaded(self, model_id: str) -> bool:
        metadata = self.registry.get(model_id)
        return model_id in self._loaded_models and metadata is not None and metadata.is_loaded()

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
        except ImportError:
            pass
        return "cpu"

    def _pipeline_device(self) -> str | int:
        device = self._resolve_device()
        return -1 if device == "cpu" else device
