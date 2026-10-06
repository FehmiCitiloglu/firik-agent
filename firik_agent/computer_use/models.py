"""Computer Use domain models, types, and structured errors."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ClickType(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"
    TRIPLE = "triple"


class ScrollDirection(str, Enum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNAVAILABLE = "unavailable"


# --- Coordinate & Geometry Models ---

class Point(BaseModel):
    x: int
    y: int


class Region(BaseModel):
    x: int
    y: int
    width: int
    height: int


class DisplayInfo(BaseModel):
    id: str
    name: str
    x: int
    y: int
    width: int
    height: int
    scale_factor: float = 1.0
    is_primary: bool = False
    pixel_width: Optional[int] = None
    pixel_height: Optional[int] = None


class WindowInfo(BaseModel):
    id: str
    title: str
    application: str
    x: int
    y: int
    width: int
    height: int
    is_focused: bool = False
    is_minimized: bool = False
    is_visible: bool = True


class AccessibilityNode(BaseModel):
    role: str
    name: str = ""
    label: str = ""
    value: str = ""
    enabled: bool = True
    focused: bool = False
    bounds: Optional[Dict[str, int]] = None
    children: List[AccessibilityNode] = Field(default_factory=list)


class EnvironmentInfo(BaseModel):
    os: str
    os_version: str
    desktop_environment: str = "Unknown"
    display_server: str = "Unknown"  # quartz, win32, x11, wayland
    architecture: str = "Unknown"
    scale_factor: float = 1.0
    permissions: Dict[str, str] = Field(default_factory=dict)
    capabilities: Dict[str, str] = Field(default_factory=dict)
    displays: List[DisplayInfo] = Field(default_factory=list)


# --- Screenshot Models ---

class ScreenshotMetadata(BaseModel):
    display_id: Optional[str] = None
    window_id: Optional[str] = None
    origin_x: int = 0
    origin_y: int = 0
    logical_width: int
    logical_height: int
    pixel_width: int
    pixel_height: int
    scale_factor: float = 1.0
    format: str = "png"
    quality: Optional[int] = None


class ScreenshotResult(BaseModel):
    image_bytes: bytes = Field(exclude=True)
    base64_data: str
    mime_type: str = "image/png"
    metadata: ScreenshotMetadata
    text_summary: str = ""


# --- Security & Confirmation Models ---

class ConfirmationRequest(BaseModel):
    action: str
    details: Dict[str, Any]
    risk_level: RiskLevel = RiskLevel.MEDIUM
    reason: str


# --- Structured Errors ---

class ComputerUseError(Exception):
    """Base exception for all computer use operations."""
    code: str = "COMPUTER_USE_ERROR"

    def __init__(self, message: str, recovery_hint: str = ""):
        super().__init__(message)
        self.message = message
        self.recovery_hint = recovery_hint

    def to_dict(self) -> Dict[str, str]:
        res = {"error": self.code, "message": self.message}
        if self.recovery_hint:
            res["recovery_hint"] = self.recovery_hint
        return res


class PermissionDeniedError(ComputerUseError):
    code = "PERMISSION_DENIED"


class CapabilityUnavailableError(ComputerUseError):
    code = "CAPABILITY_UNAVAILABLE"


class DisplayNotFoundError(ComputerUseError):
    code = "DISPLAY_NOT_FOUND"


class WindowNotFoundError(ComputerUseError):
    code = "WINDOW_NOT_FOUND"


class InvalidCoordinatesError(ComputerUseError):
    code = "INVALID_COORDINATES"


class ActionBlockedError(ComputerUseError):
    code = "ACTION_BLOCKED"


class UserCancelledError(ComputerUseError):
    code = "USER_CANCELLED"


class TimeoutError(ComputerUseError):
    code = "TIMEOUT"
