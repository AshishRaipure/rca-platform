"""Architecture Discovery Agent — configuration."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ArchitectureConfig(BaseModel):
    tool_timeout_s: float = 6.0
    max_systems: int = Field(default=10, ge=1)
    max_dependencies: int = Field(default=50, ge=1)
    max_recent_changes: int = Field(default=10, ge=0)
    include_changes: bool = True
    prompt_version: str = "architecture-v1"  # deterministic; tag recorded for audit
