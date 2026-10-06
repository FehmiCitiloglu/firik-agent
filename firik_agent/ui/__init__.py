"""firik masaüstü uygulaması modülü."""
from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    """firik masaüstü uygulamasını başlatır."""
    from .app import main as app_main
    return app_main()
