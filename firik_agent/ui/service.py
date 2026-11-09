"""Compatibility import for the shared application service."""
import sys
from firik_agent.application import service as _implementation

# Preserve module identity for existing consumers and dependency injection.
sys.modules[__name__] = _implementation
