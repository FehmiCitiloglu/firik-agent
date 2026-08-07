"""Model Manager - Handles loading, using, and ejecting models from Hugging Face."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Union

# Lazy imports to avoid requiring heavy dependencies at import time
try:
    from huggingface_hub import HfApi, snapshot_download
    HAS_HF_HUB = True
except ImportError:
    HfApi = None  # type: ignore[misc]
    HAS_HF_HUB = False

try:
    from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
    HAS_TRANSFORMERS = True
except ImportError:
    AutoModelForCausalLM = None  # type: ignore[misc]
    AutoTokenizer = None  # type: ignore[misc]
    pipeline = None  # type: ignore[misc]
    HAS_TRANSFORMERS = False

from .registry import ModelMetadata, ModelRegistry, ModelStatus

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages the lifecycle of Hugging Face models (load, use, eject)."""

    def __init__(
        self,
        registry: Optional[ModelRegistry] = None,
        device: str = "auto",
    ) -> None:
        """
        Initialize ModelManager.

        Args:
            registry: Optional model registry (creates new if not provided)
            device: Device to run models on ('cpu', 'cuda', 'mps') or 'auto' for detection
        """
        self.registry = registry or ModelRegistry()
        self.device = device
        self._loaded_models: dict[str, Any] = {}  # model_id -> pipeline/model
        self._tokenizers: dict[str, Any] = {}  # model_id -> tokenizer

    def _resolve_device(self) -> str:
        """Auto-detect the best available device."""
        if self.device == "auto":
            try:
                import torch

                if torch.cuda.is_available():
                    return "cuda"
                elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                    return "mps"
            except ImportError:
                pass
        return self.device

    def _validate_dependencies(self) -> None:
        """Ensure required dependencies are installed."""
        if not HAS_HF_HUB:
            raise ImportError(
                "huggingface_hub is required. Install with: pip install huggingface_hub"
            )
        if not HAS_TRANSFORMERS:
            raise ImportError(
                "transformers is required. Install with: pip install transformers"
            )

    def search_models(
        self,
        query: str = "",
        task: Optional[str] = None,
        limit: int = 10,
        sort: str = "downloads",
    ) -> list[dict]:
        """Search for models on Hugging Face Hub.

        Args:
            query: Search query string
            task: Model task (e.g., "text-generation", "sentiment-analysis")
            limit: Maximum number of results
            sort: Sort criteria ('downloads', 'likes')

        Returns:
            List of model information dictionaries
        """
        self._validate_dependencies()

        api = HfApi()  # type: ignore[assignment]
        models = api.list_models(
            search=query,
            task=task or "text-generation",
            sort=sort,
            direction=-1,  # descending
            limit=limit,
        )

        results = []
        for model in models:
            results.append({
                "id": str(model.id),
                "downloads": model.downloads,
                "likes": model.likes if hasattr(model, 'likes') else 0,
                "pipeline_tag": str(model.pipeline_tag) if hasattr(model, 'pipeline_tag') else None,
                "tags": getattr(model, 'tag', []),
            })

        return results

    def load_model(
        self,
        model_id: str = None,
        pipeline_type: str = "text-generation",
        max_model_length: int = 4096,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_new_tokens: int = 512,
    ) -> Any:
        """Load a model from Hugging Face Hub.

        Args:
            model_id: Model identifier or path (e.g., "mistralai/Mistral-7B-Instruct-v0.1")
            pipeline_type: Type of model pipeline
            max_model_length: Maximum sequence length
            temperature: Sampling temperature (0.0 to 1.0)
            top_p: Nucleus sampling threshold
            max_new_tokens: Maximum tokens to generate

        Returns:
            Loaded pipeline/model object

        Raises:
            ValueError: If model is already loaded or not found
            FileNotFoundError: If model doesn't exist on Hub
        """
        if model_id is None:
            raise ValueError("model_id must be provided")

        # Check if already loaded
        if self._is_model_loaded(model_id):
            logger.warning(f"Model {model_id} is already loaded")
            return self._loaded_models[model_id]

        self._validate_dependencies()

        # Register metadata
        meta = self.registry.register(
            model_id=model_id,
            pipeline_type=pipeline_type,
            max_model_length=max_model_length,
            temperature=temperature,
            top_p=top_p,
            max_new_tokens=max_new_tokens,
        )

        device = self._resolve_device()
        meta.loaded_at = time.time()

        try:
            logger.info(f"Loading model {model_id} on device {device}")

            if pipeline_type == "text-generation":
                # Load tokenizer and model
                tokenizer = AutoTokenizer.from_pretrained(
                    model_id,
                    trust_remote_code=True,
                )

                # Handle special token for Mistral family
                if "mistral" in model_id.lower():
                    tokenizer.pad_token = tokenizer.eos_token

                model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    device_map=device if device != "cpu" else None,
                    trust_remote_code=True,
                    torch_dtype="auto",  # Auto-detect best dtype (bfloat16/fp16/etc)
                )

                # Create pipeline for easy generation
                pipe = pipeline(
                    "text-generation",
                    model=model,
                    tokenizer=tokenizer,
                )

                self._loaded_models[model_id] = pipe
                self._tokenizers[model_id] = tokenizer

            elif pipeline_type == "sentiment-analysis":
                pipe = pipeline(
                    "sentiment-analysis",
                    model=model_id,
                    device=device if device != "cpu" else None,
                )
                self._loaded_models[model_id] = pipe

            elif pipeline_type == "text-classification":
                pipe = pipeline(
                    "text-classification",
                    model=model_id,
                    device=device if device != "cpu" else None,
                )
                self._loaded_models[model_id] = pipe

            # Update status to LOADED
            meta.status = ModelStatus.LOADED
            logger.info(f"Model {model_id} loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load model {model_id}: {e}")
            meta.status = ModelStatus.EJECTED  # Rollback status
            raise

        return self._loaded_models[model_id]

    def _is_model_loaded(self, model_id: str) -> bool:
        """Check if a model is currently loaded."""
        return (model_id in self._loaded_models) and (self.registry.get(model_id).is_loaded())

    def generate(
        self,
        model_id: str = None,
        prompt: str = "",
        **kwargs,
    ) -> list[dict]:
        """Generate text using a loaded model.

        Args:
            model_id: Model to use (uses first loaded if not specified)
            prompt: Input text
            **kwargs: Additional generation parameters

        Returns:
            List of generated outputs with metadata
        """
        if model_id is None and not self._loaded_models:
            raise ValueError("No models loaded. Load a model first or specify model_id.")

        # Use specified model or first available
        if model_id:
            pipe = self._loaded_models[model_id]
            meta = self.registry.get(model_id)
        else:
            model_id = next(iter(self._loaded_models))
            pipe = self._loaded_models[model_id]
            meta = self.registry.get(model_id)

        # Get generation params from metadata with overrides
        generation_kwargs = {
            "max_new_tokens": kwargs.pop("max_new_tokens", meta.max_new_tokens),
            "temperature": kwargs.pop("temperature", meta.temperature),
            "top_p": kwargs.pop("top_p", meta.top_p),
        }

        # Time the inference
        start_time = time.time()
        try:
            outputs = pipe(prompt, **generation_kwargs)
            inference_time = time.time() - start_time

        except Exception as e:
            logger.error(f"Generation failed for {model_id}: {e}")
            raise

        # Update usage stats
        if meta:
            tokens = len(outputs[0]["generated_text"]) if outputs else 0
            meta.usage_stats.update(tokens=tokens, inference_time=inference_time)

        return {
            "model_id": model_id,
            "outputs": outputs,
            "inference_time_seconds": inference_time,
            **kwargs,
        }

    def eject_model(self, model_id: str) -> bool:
        """Eject (unload) a model to free up memory.

        Args:
            model_id: Model to eject

        Returns:
            True if successful, False otherwise
        """
        if model_id not in self._loaded_models:
            logger.warning(f"Model {model_id} is not loaded")
            return False

        # Update status
        meta = self.registry.get(model_id)
        if meta:
            meta.status = ModelStatus.EJECTING

        try:
            # Clear the model and tokenizer
            if model_id in self._loaded_models:
                del self._loaded_models[model_id]
            if model_id in self._tokenizers:
                del self._tokenizers[model_id]

            # Force garbage collection for CUDA models
            try:
                import torch

                if hasattr(torch, 'cuda') and torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

            meta.status = ModelStatus.EJECTED
            logger.info(f"Model {model_id} ejected successfully")

        except Exception as e:
            logger.error(f"Failed to eject model {model_id}: {e}")
            if meta:
                meta.status = ModelStatus.LOADED  # Rollback
            return False

        return True

    def eject_all(self) -> list[str]:
        """Eject all loaded models.

        Returns:
            List of model IDs that were ejected
        """
        ejected = []
        for model_id in list(self._loaded_models.keys()):
            if self.eject_model(model_id):
                ejected.append(model_id)
        return ejected

    def reload(
        self,
        model_id: str = None,
        **kwargs,
    ) -> Any:
        """Reload a model (eject then load again).

        Args:
            model_id: Model to reload (uses first loaded if not specified)
            **kwargs: Parameters for model.load_model()

        Returns:
            Newly loaded pipeline/model object
        """
        if model_id is None and not self._loaded_models:
            raise ValueError("No models loaded to reload.")

        if model_id is None:
            model_id = next(iter(self._loaded_models))

        # Eject first
        self.eject_model(model_id)

        # Load again with provided parameters or existing config
        meta = self.registry.get(model_id)
        if not meta:
            raise ValueError(f"Model {model_id} not in registry")

        return self.load_model(
            model_id=model_id,
            pipeline_type=meta.pipeline_type or "text-generation",
            max_model_length=kwargs.pop("max_model_length", meta.max_model_length),
            temperature=kwargs.pop("temperature", meta.temperature),
            top_p=kwargs.pop("top_p", meta.top_p),
            max_new_tokens=kwargs.pop("max_new_tokens", meta.max_new_tokens),
        )

    @property
    def loaded_models(self) -> list[str]:
        """List of currently loaded model IDs."""
        return [model_id for model_id, _ in self._loaded_models.items()]

    @property
    def registry(self) -> ModelRegistry:
        """Get the model registry."""
        return self.registry

    def get_model_info(self, model_id: str) -> Optional[dict]:
        """Get detailed information about a model."""
        meta = self.registry.get(model_id)
        if not meta:
            return None

        return {
            **meta.to_dict(),
            "is_loaded": self._is_model_loaded(model_id),
        }

    def get_memory_usage(self) -> dict[str, Any]:
        """Get memory usage information for loaded models."""
        import gc

        # Count Python objects
        gc.collect()

        info = {
            "loaded_model_count": len(self._loaded_models),
            "total_python_objects": gc.get_count(),
        }

        try:
            import torch

            if torch.cuda.is_available():
                allocated = torch.cuda.memory_allocated() / 1024**3
                reserved = torch.cuda.memory_reserved() / 1024**3
                info["cuda_allocated_gb"] = allocated
                info["cuda_reserved_gb"] = reserved
        except ImportError:
            pass

        return info
