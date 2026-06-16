"""libs/llm configuration.

This is the ONLY place model strings live — agents pass a ``ModelTier`` and never see a concrete
model id (Phase 1 tiering). The defaults are placeholders: the real Bedrock model ids or
inference-profile ARNs are deployment-specific and set via env (open decision in the ADR).
"""
from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field

from contracts.enums import ModelTier

# Placeholders. Override via BEDROCK_MODEL_FAST/MID/TOP with the deployment's model ids or
# inference-profile ARNs (e.g. "anthropic.claude-3-5-sonnet-20241022-v2:0" or an arn:aws:bedrock:...).
_DEFAULT_MODEL_IDS: dict[str, str] = {
    ModelTier.fast.value: "anthropic.claude-haiku-4-5",   # fast tier (Haiku-class)
    ModelTier.mid.value: "anthropic.claude-sonnet-4-6",   # mid tier (Sonnet-class)
    ModelTier.top.value: "anthropic.claude-opus-4",       # top tier (Opus-class)
}


class LLMConfig(BaseModel):
    region: str = ""
    region_env: str = "AWS_REGION"
    anthropic_version: str = "bedrock-2023-05-31"  # Bedrock Anthropic Messages schema version

    model_ids: dict[str, str] = Field(default_factory=lambda: dict(_DEFAULT_MODEL_IDS))
    model_version_label: Optional[str] = None  # optional, recorded on the response for audit

    connect_timeout_s: float = 5.0
    read_timeout_s: float = 60.0
    max_retries: int = Field(default=2, ge=0)
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0

    def model_for(self, tier: ModelTier) -> str:
        return self.model_ids.get(tier.value, "")

    @classmethod
    def from_env(cls) -> "LLMConfig":
        ids = dict(_DEFAULT_MODEL_IDS)
        for tier, env in (("fast", "BEDROCK_MODEL_FAST"), ("mid", "BEDROCK_MODEL_MID"),
                          ("top", "BEDROCK_MODEL_TOP")):
            val = os.environ.get(env)
            if val:
                ids[tier] = val
        return cls(
            region=os.environ.get("AWS_REGION", ""),
            model_ids=ids,
            model_version_label=os.environ.get("BEDROCK_MODEL_VERSION_LABEL") or None,
        )
