"""Compatibility import for the GUI-independent coding service."""
import sys
from firik_agent.application import coding_bridge as _implementation

sys.modules[__name__] = _implementation
