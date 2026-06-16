"""Communication Agent — configuration."""
from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.enums import ModelTier


class CommunicationConfig(BaseModel):
    primary_tier: ModelTier = ModelTier.mid
    llm_timeout_s: float = 30.0
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.2
    llm_max_attempts: int = Field(default=2, ge=1)
    prompt_version: str = "communication-v1"
