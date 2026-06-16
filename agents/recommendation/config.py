"""Recommendation Agent — configuration."""
from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.enums import ModelTier


class RecommendationConfig(BaseModel):
    primary_tier: ModelTier = ModelTier.mid
    llm_timeout_s: float = 30.0
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.1
    llm_max_attempts: int = Field(default=2, ge=1)
    max_steps: int = Field(default=8, ge=1)
    prompt_version: str = "recommendation-v1"
