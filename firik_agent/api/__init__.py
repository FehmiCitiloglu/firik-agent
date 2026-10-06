"""Public FIRIK API client, independent of the agent runtime."""

from .client import FirikAPI, FirikAPIError

__all__ = ["FirikAPI", "FirikAPIError"]
