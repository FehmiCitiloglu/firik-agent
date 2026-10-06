"""Coordinate system transforms, multi-monitor bounds, and scaling logic."""
from __future__ import annotations

from typing import List, Optional, Tuple
from firik_agent.computer_use.models import DisplayInfo, InvalidCoordinatesError, Point, Region


class CoordinateManager:
    """Handles logical coordinate normalization, DPI scaling, and multi-monitor geometry."""

    @staticmethod
    def get_virtual_bounds(displays: List[DisplayInfo]) -> Tuple[int, int, int, int]:
        """Calculates the bounding box (min_x, min_y, max_x, max_y) across all displays."""
        if not displays:
            return 0, 0, 1920, 1080

        min_x = min(d.x for d in displays)
        min_y = min(d.y for d in displays)
        max_x = max(d.x + d.width for d in displays)
        max_y = max(d.y + d.height for d in displays)
        return min_x, min_y, max_x, max_y

    @staticmethod
    def get_primary_display(displays: List[DisplayInfo]) -> Optional[DisplayInfo]:
        """Returns the primary display, or the first display as fallback."""
        if not displays:
            return None
        for d in displays:
            if d.is_primary:
                return d
        return displays[0]

    @staticmethod
    def find_display_for_point(displays: List[DisplayInfo], x: int, y: int) -> Optional[DisplayInfo]:
        """Finds which display contains the logical coordinate (x, y)."""
        for d in displays:
            if d.x <= x < (d.x + d.width) and d.y <= y < (d.y + d.height):
                return d
        return None

    @classmethod
    def validate_and_clamp(
        cls,
        displays: List[DisplayInfo],
        x: int,
        y: int,
        strict: bool = False,
    ) -> Point:
        """Validates that a point is on a valid display, optionally clamping to bounds."""
        target_display = cls.find_display_for_point(displays, x, y)
        if target_display:
            return Point(x=x, y=y)

        if not displays:
            return Point(x=max(0, x), y=max(0, y))

        if strict:
            min_x, min_y, max_x, max_y = cls.get_virtual_bounds(displays)
            raise InvalidCoordinatesError(
                f"Coordinates ({x}, {y}) are outside all display bounds [X: {min_x}..{max_x}, Y: {min_y}..{max_y}].",
                recovery_hint="Check screen resolution with `computer_get_displays` before clicking.",
            )

        # Clamp to primary display or nearest bounds
        primary = cls.get_primary_display(displays)
        clamped_x = max(primary.x, min(x, primary.x + primary.width - 1))
        clamped_y = max(primary.y, min(y, primary.y + primary.height - 1))
        return Point(x=clamped_x, y=clamped_y)

    @staticmethod
    def logical_to_physical(x: int, y: int, scale_factor: float) -> Tuple[int, int]:
        """Converts logical desktop coordinates to physical hardware pixels."""
        return int(round(x * scale_factor)), int(round(y * scale_factor))

    @staticmethod
    def physical_to_logical(px: int, py: int, scale_factor: float) -> Tuple[int, int]:
        """Converts physical hardware pixels to logical desktop coordinates."""
        if scale_factor <= 0:
            scale_factor = 1.0
        return int(round(px / scale_factor)), int(round(py / scale_factor))

    @staticmethod
    def crop_region_logical(
        region: Region,
        display: DisplayInfo,
    ) -> Region:
        """Clamps a requested crop region to the boundaries of the display."""
        rx = max(display.x, region.x)
        ry = max(display.y, region.y)
        rw = min(region.width, (display.x + display.width) - rx)
        rh = min(region.height, (display.y + display.height) - ry)
        return Region(x=rx, y=ry, width=max(1, rw), height=max(1, rh))
