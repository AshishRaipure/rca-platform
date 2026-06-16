"""RCA Agent — input & output schemas.

Output is a GRADED, evidence-referenced analysis: ranked causes + alternatives + an overall
confidence grade (never a fabricated percentage). Every cause references evidence/citation ids
drawn from what was actually provided.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import ConfidenceGrade, SeverityLevel
from contracts.models import NormalizedIncident


class RcaInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: UUID
    incident: NormalizedIncident
    severity: SeverityLevel
    classification: dict[str, Any] = Field(default_factory=dict)
    initial_hypothesis: Optional[str] = None
    affected_systems: list[str] = Field(default_factory=list)
    knowledge_summary: Optional[str] = None
    knowledge_findings: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    similar_incidents: list[dict[str, Any]] = Field(default_factory=list)
    architecture_context: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class RootCause(BaseModel):
    statement: str
    confidence: ConfidenceGrade
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: Optional[str] = None
    category: Optional[str] = None


class RcaOutput(BaseModel):
    summary: str = ""
    ranked_causes: list[RootCause] = Field(default_factory=list)
    alternatives: list[RootCause] = Field(default_factory=list)
    overall_confidence: ConfidenceGrade = ConfidenceGrade.low
    metadata: dict[str, Any] = Field(default_factory=dict)


class _LLMRcaResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    ranked_causes: list[dict[str, Any]] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    overall_confidence: str = "low"
