"""Pre-configured model presets for HF-LLM Agent."""

from __future__ import annotations

from typing import Any, Optional


# Model configuration structure
ModelConfig = dict[str, Any]


def _build_small_models() -> dict[str, ModelConfig]:
    """Build small models dictionary."""
    return {
        "gemma-2b": {
            "model_id": "google/gemma-2b",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Google's Gemma-2B - efficient and high-quality",
        },

        "mistral-7b": {
            "model_id": "mistralai/Mistral-7B-Instruct-v0.1",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Mistral-7B - excellent balance of quality and speed",
        },

        "phi-2": {
            "model_id": "microsoft/Phi-2",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Microsoft Phi-2 - surprisingly capable",
        },

        "tinyllama": {
            "model_id": "TinyLlama/TinyLlama-1.1B",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 128,
            "description": "Tiny Llama - tiny model, big results",
        },

        "llama2-7b": {
            "model_id": "meta-llama/Llama-2-7b-hf",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Llama-2-7B - Meta's open-weight model",
        },

        "gemma-7b": {
            "model_id": "google/gemma-7b",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Google's Gemma-7B - efficient and capable",
        },

        "qwen2.5-1b": {
            "model_id": "Qwen/Qwen2.5-Coder-1.5B",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 128,
            "description": "Qwen-1.5B - Qwen's smallest model",
        },

        "llama3-8b": {
            "model_id": "meta-llama/Llama-3-8B-Instruct",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Llama-3-8B - latest from Meta",
        },

        "deepseek-coder": {
            "model_id": "deepseek-ai/deepseek-coder-6b-v1",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "DeepSeek Coder - code generation focused",
        },

        "gemma-2b-it": {
            "model_id": "google/gemma-2b-it",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Gemma-2B Instruct - instruction-tuned",
        },

        "llama3.1-8b-instruct-v0.1": {
            "model_id": "mistralai/Mistral-8x7B-Instruct-v0.1",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Mistral-8x7B - MoE model, very efficient",
        },

        "llama3.1-8b-instruct-v0.1-4096": {
            "model_id": "mistralai/Mistral-7B-Instruct-v0.1",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Mistral-7B with longer context",
        },

        "llama3.1-8b-instruct-v0.1-8k": {
            "model_id": "mistralai/Mistral-7B-Instruct-v0.1",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Mistral-7B with longer context",
        },

        "llama3.1-8b-instruct-v0.1-32k": {
            "model_id": "mistralai/Mistral-7B-Instruct-v0.1",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Mistral-7B with 32k context",
        },
    }


def _build_medium_models() -> dict[str, ModelConfig]:
    """Build medium models dictionary."""
    return {
        "gemma-7b": {
            "model_id": "google/gemma-7b",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Google's Gemma-7B",
        },

        "llama2-13b": {
            "model_id": "meta-llama/Llama-2-13b-hf",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Llama-2-13B",
        },

        "llama2-7b": {
            "model_id": "meta-llama/Llama-2-7b-hf",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Llama-2-7B",
        },
    }


def _build_large_models() -> dict[str, ModelConfig]:
    """Build large models dictionary."""
    return {
        "llama2-70b": {
            "model_id": "meta-llama/Llama-2-70b-hf",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Llama-2-70B - state-of-the-art",
        },

        "llama3-14b": {
            "model_id": "mistralai/Mistral-8x7B-Instruct-v0.1",
            "pipeline_type": "text-generation",
            "max_model_length": 4096,
            "temperature": 0.7,
            "top_p": 0.95,
            "max_new_tokens": 256,
            "description": "Mistral-8x7B MoE",
        },
    }


# =============================================================================
# Combined presets for easy access
# =============================================================================

MODEL_PRESETS = {**_build_small_models(), **_build_medium_models(), **_build_large_models()}

# Convenience accessors
SMALL_MODELS = _build_small_models()
MEDIUM_MODELS = _build_medium_models()
LARGE_MODELS = _build_large_models()


def get_preset(preset_name: str) -> Optional[ModelConfig]:
    """Get a model configuration by preset name.

    Args:
        preset_name: Name of the preset (e.g., "gemma-2b", "llama3.1-8b-instruct")

    Returns:
        Model configuration dictionary or None if not found

    Raises:
        ValueError: If preset name is invalid
    """
    if not MODEL_PRESETS:
        return None

    config = MODEL_PRESETS.get(preset_name)
    if not config:
        raise ValueError(
            f"Unknown preset '{preset_name}'. "
            f"Available presets: {list(MODEL_PRESETS.keys())}"
        )

    return config


def list_presets(category: Optional[str] = None) -> dict:
    """List available model presets.

    Args:
        category: Filter by category ('small', 'medium', 'large') or None for all

    Returns:
        Dictionary of available presets
    """
    if category is None:
        return MODEL_PRESETS

    categories = {
        "small": SMALL_MODELS,
        "medium": MEDIUM_MODELS,
        "large": LARGE_MODELS,
    }

    return categories.get(category.lower(), {})


# =============================================================================
# Example Usage (run with python -m models.presets)
# =============================================================================

if __name__ == "__main__":
    print("=== Available Model Presets ===\n")

    for category, models in [("Small", SMALL_MODELS), ("Medium", MEDIUM_MODELS),
                            ("Large", LARGE_MODELS)]:
        print(f"--- {category} Models ({len(models)} total) ---")
        for name, config in models.items():
            print(f"  • {name}")
            print(f"    Model: {config['model_id']}")
            print(f"    Description: {config.get('description', 'N/A')}")
        print()
