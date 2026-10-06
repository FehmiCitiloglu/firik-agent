"""Abstract ComputerBackend base class for OS-specific desktop automation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

from firik_agent.computer_use.models import (
    AccessibilityNode,
    ClickType,
    DisplayInfo,
    EnvironmentInfo,
    MouseButton,
    Region,
    ScrollDirection,
    WindowInfo,
)


class ComputerBackend(ABC):
    """Abstract interface defining the OS-level primitives for computer use."""

    @abstractmethod
    def detect_environment(self) -> EnvironmentInfo:
        """Discovers OS details, display server, permissions, and supported capabilities."""
        pass

    @abstractmethod
    def get_displays(self) -> List[DisplayInfo]:
        """Discovers all active physical/logical displays with bounds and scale factors."""
        pass

    @abstractmethod
    def capture_screen(
        self,
        display_id: Optional[str] = None,
        region: Optional[Region] = None,
        window_id: Optional[str] = None,
    ) -> Tuple[bytes, float, int, int]:
        """Captures raw image bytes, returning (raw_bytes, scale_factor, origin_x, origin_y)."""
        pass

    @abstractmethod
    def list_windows(self) -> List[WindowInfo]:
        """Lists active windows with bounds, titles, and visibility."""
        pass

    @abstractmethod
    def focus_window(self, window_id: str) -> bool:
        """Brings the specified window to the foreground and focuses it."""
        pass

    @abstractmethod
    def move_pointer(self, x: int, y: int, duration: float = 0.0) -> None:
        """Moves the mouse pointer to logical desktop coordinates (x, y)."""
        pass

    @abstractmethod
    def mouse_click(
        self,
        x: int,
        y: int,
        button: MouseButton = MouseButton.LEFT,
        click_type: ClickType = ClickType.SINGLE,
    ) -> None:
        """Clicks at logical desktop coordinates (x, y)."""
        pass

    @abstractmethod
    def mouse_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: MouseButton = MouseButton.LEFT,
        duration: float = 0.5,
    ) -> None:
        """Drags the cursor from (start_x, start_y) to (end_x, end_y)."""
        pass

    @abstractmethod
    def mouse_scroll(self, dx: int, dy: int) -> None:
        """Scrolls vertically (dy) and horizontally (dx)."""
        pass

    @abstractmethod
    def type_text(self, text: str) -> None:
        """Types Unicode text into the currently active element."""
        pass

    @abstractmethod
    def press_key(self, key: str) -> None:
        """Presses and releases a single key (e.g. 'Return', 'Escape', 'Tab', 'BackSpace')."""
        pass

    @abstractmethod
    def hotkey(self, keys: List[str]) -> None:
        """Presses and releases a combination of keys (e.g. ['Command', 'c'] or ['Ctrl', 'Shift', 'p'])."""
        pass

    @abstractmethod
    def get_clipboard(self) -> str:
        """Retrieves text from the system clipboard."""
        pass

    @abstractmethod
    def set_clipboard(self, text: str) -> None:
        """Sets text in the system clipboard."""
        pass

    @abstractmethod
    def get_accessibility_tree(self, window_id: Optional[str] = None) -> Optional[AccessibilityNode]:
        """Retrieves the accessibility / UI automation tree for the active window or screen."""
        pass
