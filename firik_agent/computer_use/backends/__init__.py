"""Computer Use platform backends factory and exports."""
from __future__ import annotations

import sys
from typing import Optional

from firik_agent.computer_use.backends.base import ComputerBackend
from firik_agent.computer_use.backends.linux import LinuxBackend, LinuxComputerBackend
from firik_agent.computer_use.backends.macos import MacOSBackend
from firik_agent.computer_use.backends.mock import MockBackend, MockComputerBackend
from firik_agent.computer_use.backends.windows import WindowsBackend


def get_backend(platform_name: Optional[str] = None, use_mock: bool = False) -> ComputerBackend:
    """Factory to get the appropriate ComputerBackend for the host OS or test environment."""
    if use_mock:
        return MockBackend()

    target = (platform_name or sys.platform).lower()
    if target.startswith("darwin"):
        return MacOSBackend()
    elif target.startswith("win32") or target.startswith("cygwin"):
        return WindowsBackend()
    elif target.startswith("linux"):
        return LinuxBackend()
    else:
        # Fallback to mock if platform unknown/unsupported
        return MockBackend()


__all__ = [
    "ComputerBackend",
    "MacOSBackend",
    "WindowsBackend",
    "LinuxBackend",
    "LinuxComputerBackend",
    "MockBackend",
    "MockComputerBackend",
    "get_backend",
]
