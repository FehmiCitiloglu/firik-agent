"""Compatibility import for the GUI-independent coding actions."""
import sys
from firik_agent.application import coding_actions as _implementation

sys.modules[__name__] = _implementation
