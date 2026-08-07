"""Firik Agent: a workspace-scoped software-development agent."""

from .agent import LLMAgent, SeniorDevelopmentAgent
from .model_manager import ModelManager
from .process import DevelopmentPhase, DevelopmentProcess
from .registry import ModelMetadata, ModelRegistry, ModelStatus

__version__ = "0.2.0"

__all__ = [
    "DevelopmentPhase",
    "DevelopmentProcess",
    "LLMAgent",
    "ModelManager",
    "ModelMetadata",
    "ModelRegistry",
    "ModelStatus",
    "SeniorDevelopmentAgent",
]
