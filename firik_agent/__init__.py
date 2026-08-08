"""Firik Agent: a workspace-scoped software-development agent."""

from .agent import DevelopmentAgent, LLMAgent
from .model_manager import ModelManager
from .process import DevelopmentPhase, DevelopmentProcess
from .registry import ModelMetadata, ModelRegistry, ModelStatus

__version__ = "0.2.0"

__all__ = [
    "DevelopmentAgent",
    "DevelopmentPhase",
    "DevelopmentProcess",
    "LLMAgent",
    "ModelManager",
    "ModelMetadata",
    "ModelRegistry",
    "ModelStatus",
]
