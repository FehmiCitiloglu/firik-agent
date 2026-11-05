"""Validated learning settings, deliberately separate from execution grants."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ImprovementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["off", "observe", "propose", "experiment"] = "observe"
    automatic_reflection: bool = False
    memory_token_budget: int = Field(default=600, ge=0, le=8000)
    max_events_per_session: int = Field(default=256, ge=8, le=2000)
    max_pending_jobs: int = Field(default=8, ge=1, le=32)
    max_proposals_per_review: int = Field(default=3, ge=1, le=8)
    review_cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    max_review_seconds: int = Field(default=20, ge=1, le=120)
    retention_days: int = Field(default=90, ge=1, le=3650)
    max_records: int = Field(default=10000, ge=100, le=100000)
    max_candidate_workspaces: int = Field(default=4, ge=1, le=20)
    candidate_storage_mb: int = Field(default=512, ge=64, le=4096)
    private_projects: list[str] = Field(default_factory=list, max_length=1000)
    # These are explicit assignments, not claims about model availability.
    # No route changes the active chat/coding provider, and no network fallback.
    model_routes: dict[str, dict[str, str]] = Field(default_factory=dict)
    periodic_review_seconds: int = Field(default=0, ge=0, le=604800)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "ImprovementConfig":
        if not config:
            return cls()
        section = config.get("self_improvement", config)
        # A full application config without the section means defaults.
        if "self_improvement" not in config and any(k in config for k in ("agent", "providers", "skills")):
            section = {}
        if not isinstance(section, dict):
            raise ValueError("self_improvement must be a mapping")
        result = cls.model_validate(section)
        allowed = {"reflection", "memory", "skill", "evaluation", "coding", "embedding", "reranking"}
        for role, route in result.model_routes.items():
            if role not in allowed or set(route) - {"provider", "model", "revision"}:
                raise ValueError("Unknown auxiliary route or route fields")
            if not route.get("provider") or not route.get("model"):
                raise ValueError("Auxiliary routes require an explicit provider and model")
        return result
