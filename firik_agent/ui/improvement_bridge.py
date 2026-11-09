"""Compatibility import for the GUI-independent improvement service."""
import sys
from firik_agent.application import improvement_bridge as _implementation

sys.modules[__name__] = _implementation
