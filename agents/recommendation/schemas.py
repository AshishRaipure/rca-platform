"""Recommendation Agent — schemas.

Each step is tagged with a risk level and an approval requirement, and a prod-impacting flag.
The platform NEVER executes any of these; they are advice for a human to act on through the
appropriate change process.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import SeverityLevel

CATEGORIES = {"diagnostic", "mitigation", "preventive", "verification"}
RISKS = {"low", "medium", "high"}
APPROVALS = {"none", "human_approval", "human_approval_and_change"}

ADVISORY_NOTICE = (
    "Advisory only. The platform does not execute any of these steps. A human must review and "
    "perform any action through the appropriate change/ECO process."
)


class RecommendationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: UUID
    severity: SeverityLevel
    rca: dict[str, Any] = Field(default_factory=dict)
    architecture_context: dict[str, Any] = Field(default_factory=dict)
    knowledge_findings: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class RecommendationStep(BaseModel):
    action: str
    category: str = "diagnostic"
    risk: str = "low"
    prod_impacting: bool = False
    approval_requirement: str = "none"
    rationale: Optional[str] = None
    evidence_refs: list[str] = Field(default_factory=list)


class RecommendationOutput(BaseModel):
    summary: str = ""
    steps: list[RecommendationStep] = Field(default_factory=list)
    advisory_notice: str = ADVISORY_NOTICE
    metadata: dict[str, Any] = Field(default_factory=dict)


class _LLMRecResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
