"""Knowledge Retrieval Agent — configuration."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from contracts.enums import ModelTier
from contracts.retrieval import SourceType


class KnowledgeConfig(BaseModel):
    # model tiering (synthesis). Escalation is used ONLY on parse failure, never to suppress
    # conflicts (conflicts are a result we want to keep, not an error to resolve away).
    primary_tier: ModelTier = ModelTier.mid
    escalation_tier: ModelTier = ModelTier.top
    allow_escalation: bool = True

    # retrieval depth
    corpus_k: int = Field(default=12, ge=1)
    episodic_k: int = Field(default=5, ge=0)
    max_context_chunks: int = Field(default=8, ge=1)  # chunks actually shown to the model
    snippet_max_chars: int = Field(default=600, ge=80)

    # budgets
    retrieval_timeout_s: float = 8.0
    tool_timeout_s: float = 5.0
    llm_timeout_s: float = 30.0
    llm_max_tokens: int = 2048
    llm_temperature: float = 0.1
    llm_max_attempts: int = Field(default=2, ge=1)

    # behavior
    source_types: Optional[list[SourceType]] = None  # None = all permitted types
    prefer_current: bool = True
    enable_query_expansion: bool = True
    enable_episodic: bool = True
    enable_freshness_check: bool = False  # optional, read-only MCP verification of top sources

    min_sources_for_confident: int = Field(default=3, ge=1)
    prompt_version: str = "knowledge-v1"
