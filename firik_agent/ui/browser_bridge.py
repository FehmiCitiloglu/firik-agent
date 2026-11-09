"""Compatibility import for the GUI-independent browser service."""
import sys
from firik_agent.application import browser_bridge as _implementation

sys.modules[__name__] = _implementation
