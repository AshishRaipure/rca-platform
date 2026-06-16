"""Incident Intake Agent — Input & Output schemas.

Public contract:
  * IntakeInput   — what the agent receives (built from InvestigationState by the node).
  * IntakeOutput  — the validated, guardrail-checked result written back to state.

Internal:
  * _LLMIntakeResult — the *raw* structured result parsed from the model, BEFORE guardrails
    (severity flooring, no-invented-systems validation, triage clamping) are applied.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import ConfidenceGrade, SeverityLevel, TriageDecision
from contracts.models import NormalizedIncident, Provenance, TriageHint


# --------------------------------------------------------------------------- input

class IntakeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    incident: NormalizedIncident
    triage_hint: Optional[TriageHint] = None


# --------------------------------------------------------------------- output parts

class IncidentClassification(BaseModel):
    suggested_severity: SeverityLevel
    severity_rationale: str
    severity_confidence: ConfidenceGrade
    severity_source: str  # "provider" | "derived" | "default"
    is_advisory: bool = True  # severity is always a suggestion; a human owns the final call


class AffectedSystem(BaseModel):
    name: str
    service_id: Optional[UUID] = None
    evidence: list[Provenance] = Field(min_length=1)  # provenance is mandatory
    confirmed_in_catalog: bool = False
    confidence: ConfidenceGrade


class InitialHypothesis(BaseModel):
    statement: str
    confidence: ConfidenceGrade
    evidence: list[Provenance] = Field(default_factory=list)
    is_preliminary: bool = True


class IntakeMetadata(BaseModel):
    model_id: str
    model_version: Optional[str] = None
    prompt_version: str
    model_tier_used: str
    escalated: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    tool_calls: list[str] = Field(default_factory=list)
    degraded: bool = False  # True when a deterministic fallback was used (LLM down/unparseable)
    warnings: list[str] = Field(default_factory=list)


class IntakeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    classification: IncidentClassification
    affected_systems: list[AffectedSystem] = Field(default_factory=list)
    initial_hypothesis: InitialHypothesis
    recommended_triage: TriageDecision
    metadata: IntakeMetadata


# ----------------------------------------------------------------- internal (raw LLM)

class _LLMAffectedSystem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    evidence_quote: str
    reason: Optional[str] = None


class _LLMIntakeResult(BaseModel):
    """Raw model output. Validated for shape only; trust is established by guardrails."""
    model_config = ConfigDict(extra="ignore")

    severity_guess: Optional[SeverityLevel] = None
    severity_rationale: str = ""
    severity_certain: bool = False
    affected_systems: list[_LLMAffectedSystem] = Field(default_factory=list)
    hypothesis_statement: str = ""
    hypothesis_evidence_quote: Optional[str] = None
    ambiguous: bool = False
    recommended_triage: Optional[TriageDecision] = None
