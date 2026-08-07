"""Memory monitoring utilities for HF-LLM Agent."""

from __future__ import annotations

import gc
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MemoryStats:
    """Memory usage statistics."""

    # Python memory
    pymem_total_mb: float = 0.0
    pymem_current_mb: float = 0.0

    # GPU memory (if available)
    cuda_allocated_mb: Optional[float] = None
    cuda_reserved_mb: Optional[float] = None

    # Model objects count
    object_count: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "pymem_total_mb": round(self.pymem_total_mb, 2),
            "pymem_current_mb": round(self.pymem_current_mb, 2),
            "cuda_allocated_mb": self.cuda_allocated_mb,
            "cuda_reserved_mb": self.cuda_reserved_mb,
            "object_count": self.object_count,
        }

    def __str__(self) -> str:
        lines = [
            "=== Memory Stats ===",
            f"  Python Total:    {self.pymem_total_mb:.2f} MB",
            f"  Python Current:   {self.pymem_current_mb:.2f} MB",
            f"  Objects:          {self.object_count}",
        ]

        if self.cuda_allocated_mb is not None:
            lines.extend([
                f"  CUDA Allocated: {self.cuda_allocated_mb:.2f} MB",
                f"  CUDA Reserved:   {self.cuda_reserved_mb:.2f} MB",
            ])

        return "\n".join(lines)


class MemoryMonitor:
    """Monitors and reports memory usage."""

    def __init__(self) -> None:
        self._stats = MemoryStats()

    @staticmethod
    def get_stats() -> MemoryStats:
        """Get current memory statistics.

        Returns:
            MemoryStats object with current memory usage
        """
        stats = MemoryStats()

        # Python memory
        try:
            stats.pymem_total_mb = sys.getsizeof(sys.modules) / 1024**2
            stats.pymem_current_mb = sum(
                sys.getsizeof(v) for v in sys.modules.values()
            ) / 1024**2
        except Exception:
            pass

        # Object count
        gc.collect()
        stats.object_count = sum(
            sys.getsizeof(v) for v in gc.get_objects()
        ) / 1024**2

        # GPU memory (if available)
        try:
            import torch

            if torch.cuda.is_available():
                stats.cuda_allocated_mb = torch.cuda.memory_allocated() / 1024**2
                stats.cuda_reserved_mb = torch.cuda.memory_reserved() / 1024**2
        except ImportError:
            pass

        return stats

    @staticmethod
    def report() -> str:
        """Get formatted memory usage report.

        Returns:
            String with formatted memory stats
        """
        return str(MemoryMonitor.get_stats())

    @staticmethod
    def clear_cache() -> None:
        """Clear Python and GPU caches.

        This helps free up memory after ejecting models.
        """
        # Clear Python cache
        gc.collect()

        try:
            import torch

            if hasattr(torch, 'cuda') and torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Clear transformer cache if available
            try:
                from transformers import set_cache_type, CacheType

                set_cache_type(CacheType.CACHE_NONE)
            except ImportError:
                pass
        except ImportError:
            pass

    @staticmethod
    def estimate_model_size(model_name_or_path: str) -> float:
        """Estimate model size in GB.

        Args:
            model_name_or_path: Model ID or local path

        Returns:
            Estimated size in GB (rough estimate based on name)
        """
        # Rough estimates for common model families
        size_map = {
            "llama": {"7b": 14.0, "13b": 26.0, "70b": 140.0},
            "mistral": {"7b": 14.0, "8b": 16.0},
            "gpt2": {"medium": 1.4, "large": 4.0},
            "gemma": {"2b": 5.0, "7b": 14.0},
            "bert": {"base": 2.3, "large": 7.5},
        }

        name_lower = model_name_or_path.lower()

        for family, sizes in size_map.items():
            if family in name_lower:
                for variant, size_gb in sizes.items():
                    if variant.lower() in name_lower:
                        return size_gb

        # Default estimate based on common patterns
        if "7b" in name_lower:
            return 14.0
        elif "5b" in name_lower or "3b" in name_lower:
            return 6.0
        elif "2b" in name_lower or "1b" in name_lower:
            return 3.0

        # Small model default
        return 1.0
