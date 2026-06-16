"""RCA Agent — configuration."""
from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.enums import ModelTier


class RcaConfig(BaseModel):
    primary_tier: ModelTier = ModelTier.top  # RCA reasoning runs on the strongest tier
    llm_timeout_s: float = 40.0
    llm_max_tokens: int = 3072
    llm_temperature: float = 0.1
    llm_max_attempts: int = Field(default=2, ge=1)
    max_causes: int = Field(default=5, ge=1)
    require_alternatives: bool = True
    prompt_version: str = "rca-v1"
