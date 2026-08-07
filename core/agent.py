"""LLM Agent - Main interface for the HF-LLM Agent system."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .model_manager import ModelManager
from .registry import ModelMetadata, ModelStatus

logger = logging.getLogger(__name__)


class LLMAgent:
    """
    High-level LLM Agent that manages model lifecycle.

    Features:
    - Dynamic model loading from Hugging Face Hub
    - Model ejection to free memory
    - Multiple models hot-swapping
    - Usage tracking and statistics

    Example:
        >>> from hf_llm_agent import LLMAgent
        >>> agent = LLMAgent(model_id="mistralai/Mistral-7B-Instruct-v0.1")
        >>> response = agent.generate("Explain quantum computing")
        >>> print(response)
        >>> agent.eject()  # Free memory
    """

    def __init__(
        self,
        model_id: Optional[str] = None,
        device: str = "auto",
        pipeline_type: str = "text-generation",
        max_model_length: int = 4096,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_new_tokens: int = 512,
    ) -> None:
        """
        Initialize LLM Agent.

        Args:
            model_id: Model ID to load (e.g., "mistralai/Mistral-7B-Instruct-v0.1")
            device: Device to run on ('cpu', 'cuda', 'mps') or 'auto'
            pipeline_type: Type of model pipeline
            max_model_length: Maximum sequence length
            temperature: Sampling temperature (0.0 to 1.0)
            top_p: Nucleus sampling threshold
            max_new_tokens: Maximum tokens to generate

        Raises:
            ValueError: If model_id is provided but dependencies are missing
        """
        self.model_manager = ModelManager(
            registry=None,  # Will use model_manager's registry
            device=device,
        )

        # Store default config for reloads
        self._default_config = {
            "model_id": model_id,
            "pipeline_type": pipeline_type,
            "max_model_length": max_model_length,
            "temperature": temperature,
            "top_p": top_p,
            "max_new_tokens": max_new_tokens,
        }

        # Load model if specified
        if model_id:
            self.load(model_id)

    def load(
        self,
        model_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """Load a model into the agent.

        Args:
            model_id: Model ID (uses default config if not specified)
            **kwargs: Override default config parameters

        Returns:
            Loaded pipeline/model object
        """
        if model_id is None and self._default_config["model_id"]:
            model_id = self._default_config["model_id"]

        if not model_id:
            raise ValueError("No model_id specified")

        # Merge config with defaults
        config = {**self._default_config, **kwargs}

        return self.model_manager.load_model(
            model_id=model_id,
            pipeline_type=config["pipeline_type"],
            max_model_length=config["max_model_length"],
            temperature=config["temperature"],
            top_p=config["top_p"],
            max_new_tokens=config["max_new_tokens"],
        )

    def eject(self, model_id: Optional[str] = None) -> bool:
        """Eject (unload) a model from the agent.

        Args:
            model_id: Model to eject (uses first loaded if not specified)

        Returns:
            True if successful, False otherwise
        """
        if model_id is None and self.model_manager.loaded_models:
            model_id = self.model_manager.loaded_models[0]

        if not model_id:
            raise ValueError("No models loaded to eject")

        return self.model_manager.eject_model(model_id)

    def eject_all(self) -> list[str]:
        """Eject all loaded models."""
        return self.model_manager.eject_all()

    def generate(
        self,
        prompt: str = "",
        model_id: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """Generate text using the loaded model.

        Args:
            prompt: Input text to generate from
            model_id: Specific model ID (uses first loaded if not specified)
            **kwargs: Additional generation parameters

        Returns:
            Dictionary with outputs and metadata
        """
        return self.model_manager.generate(
            model_id=model_id,
            prompt=prompt,
            **kwargs,
        )

    def search_models(
        self,
        query: str = "",
        task: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search for models on Hugging Face Hub.

        Args:
            query: Search query string
            task: Model task (e.g., "text-generation", "sentiment-analysis")
            limit: Maximum number of results

        Returns:
            List of model information dictionaries
        """
        return self.model_manager.search_models(
            query=query,
            task=task,
            limit=limit,
        )

    def get_status(self) -> dict:
        """Get current agent status.

        Returns:
            Dictionary with loaded models and memory info
        """
        return {
            "loaded_models": self.model_manager.loaded_models,
            "model_count": len(self.model_manager.loaded_models),
            "memory_usage": self.model_manager.get_memory_usage(),
        }

    def get_model_info(self, model_id: str) -> Optional[dict]:
        """Get detailed information about a model.

        Args:
            model_id: Model ID to get info for

        Returns:
            Dictionary with model metadata or None if not found
        """
        return self.model_manager.get_model_info(model_id)

    def reload(
        self,
        model_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """Reload a model (eject then load again).

        Args:
            model_id: Model to reload (uses first loaded if not specified)
            **kwargs: Parameters for model.load_model()

        Returns:
            Newly loaded pipeline/model object
        """
        return self.model_manager.reload(
            model_id=model_id,
            **kwargs,
        )

    @property
    def registry(self) -> ModelRegistry:
        """Get the model registry."""
        return self.model_manager.registry

    @property
    def manager(self) -> ModelManager:
        """Get the underlying model manager."""
        return self.model_manager

    def __repr__(self) -> str:
        loaded = len(self.model_manager.loaded_models)
        return f"LLMAgent(loaded={loaded}, device={self.model_manager.device})"
