"""Screenshot processing, resizing, compression, and multimodal formatting."""
from __future__ import annotations

import base64
import io
from typing import Optional, Tuple
from PIL import Image

from firik_agent.computer_use.models import ScreenshotMetadata, ScreenshotResult


class ScreenshotProcessor:
    """Handles image compression, format conversion, and multimodal metadata."""

    MAX_LOGICAL_WIDTH = 1920
    MAX_LOGICAL_HEIGHT = 1080

    @classmethod
    def process_image(
        cls,
        raw_bytes: bytes,
        scale_factor: float = 1.0,
        origin_x: int = 0,
        origin_y: int = 0,
        display_id: Optional[str] = None,
        window_id: Optional[str] = None,
        fmt: str = "png",
        quality: Optional[int] = None,
        max_width: Optional[int] = None,
        max_height: Optional[int] = None,
    ) -> ScreenshotResult:
        """Processes raw screenshot bytes into an optimized, encoded ScreenshotResult."""
        img = Image.open(io.BytesIO(raw_bytes))
        pixel_width, pixel_height = img.size

        # Determine logical dimensions
        logical_width = int(round(pixel_width / scale_factor)) if scale_factor > 0 else pixel_width
        logical_height = int(round(pixel_height / scale_factor)) if scale_factor > 0 else pixel_height

        # Resizing if exceeds maximum limits
        target_max_w = max_width or (cls.MAX_LOGICAL_WIDTH * int(scale_factor))
        target_max_h = max_height or (cls.MAX_LOGICAL_HEIGHT * int(scale_factor))

        if pixel_width > target_max_w or pixel_height > target_max_h:
            img.thumbnail((target_max_w, target_max_h), Image.Resampling.LANCZOS)
            pixel_width, pixel_height = img.size
            logical_width = int(round(pixel_width / scale_factor))
            logical_height = int(round(pixel_height / scale_factor))

        out_fmt = fmt.lower()
        if out_fmt in ("jpg", "jpeg"):
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            mime_type = "image/jpeg"
            save_fmt = "JPEG"
            save_quality = quality or 85
        else:
            mime_type = "image/png"
            save_fmt = "PNG"
            save_quality = quality or 90

        buffer = io.BytesIO()
        if save_fmt == "JPEG":
            img.save(buffer, format=save_fmt, quality=save_quality, optimize=True)
        else:
            img.save(buffer, format=save_fmt, optimize=True)

        final_bytes = buffer.getvalue()
        b64_str = base64.b64encode(final_bytes).decode("ascii")

        meta = ScreenshotMetadata(
            display_id=display_id,
            window_id=window_id,
            origin_x=origin_x,
            origin_y=origin_y,
            logical_width=logical_width,
            logical_height=logical_height,
            pixel_width=pixel_width,
            pixel_height=pixel_height,
            scale_factor=scale_factor,
            format=out_fmt,
            quality=save_quality,
        )

        text_summary = (
            f"[Screenshot: {logical_width}x{logical_height} logical ({pixel_width}x{pixel_height} px), "
            f"scale: {scale_factor}x, format: {out_fmt}]"
        )

        return ScreenshotResult(
            image_bytes=final_bytes,
            base64_data=b64_str,
            mime_type=mime_type,
            metadata=meta,
            text_summary=text_summary,
        )
