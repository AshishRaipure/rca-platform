"""Incident Intake Agent — configuration.

Tunable behavior. Defaults are deliberately conservative: cheap-but-fast primary model,
single escalation to a stronger model on ambiguity, bounded tool calls/tokens/timeouts, and
a high default severity so the agent never silently under-rates an unclassifiable incident.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.enums import ModelTier, SeverityLevel, TriageDecision


class IntakeConfig(BaseModel):
    # model tiering
    primary_tier: ModelTier = ModelTier.fast
    escalation_tier: ModelTier = ModelTier.mid
    allow_escalation: bool = True

    # bounded read-only enrichment
    max_tool_calls: int = Field(default=3, ge=0)
    tool_timeout_s: float = 5.0

    # llm call budget
    llm_timeout_s: float = 20.0
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.0
    llm_max_attempts: int = Field(default=2, ge=1)  # retries per tier before that tier fails

    # safety guardrail tuning
    # "conservative" means do NOT under-rate: when severity cannot be derived, assume it is high.
    default_severity: SeverityLevel = SeverityLevel.high
    # a serious (high/critical) incident is never recommended for 'drop'; floor it to this.
    min_triage_for_serious: TriageDecision = TriageDecision.lite
    enable_catalog_validation: bool = True

    prompt_version: str = "intake-v1"
