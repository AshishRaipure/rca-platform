"""Shared cross-boundary models + the orchestration state shape (contracts layer).

`NormalizedIncident` and `TriageHint` are produced upstream (webhook-ingress / intake-service)
and consumed by the Intake Agent. `Provenance` is the citation/evidence shape attached to every
claim (provenance enforcement, Phase 2 §3.6). `InvestigationState` is the LangGraph blackboard
state; agent outputs are stored as JSON-serializable structures so durable checkpointing works.
"""
from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Optional, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import SeverityLevel, SourceSystem, TriageDecision


class Provenance(BaseModel):
    """Where a claim came from. Every affected system and hypothesis must carry >=1 of these."""
    model_config = ConfigDict(frozen=True)

    source: str  # e.g. "incident_data", "servicenow.short_description", "service_catalog"
    detail: str  # the exact quote / what was matched
    tool_call_id: Optional[str] = None


class NormalizedIncident(BaseModel):
    """Provider-agnostic incident produced by webhook-ingress / intake-service."""
    model_config = ConfigDict(extra="ignore")

    incident_id: UUID
    source_system: SourceSystem
    fingerprint: str
    title: str
    description: Optional[str] = None
    provider_severity: Optional[SeverityLevel] = None
    pagerduty_id: Optional[str] = None
    pagerduty_dedup_key: Optional[str] = None
    servicenow_id: Optional[str] = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class TriageHint(BaseModel):
    """Cheap pre-triage decision from ingress; the in-graph triage_gate makes the final call."""
    decision: TriageDecision
    reason: Optional[str] = None
    source: str = "ingress_pretriage"


class InvestigationState(TypedDict, total=False):
    """LangGraph blackboard state (slice used through Phase 1 / Agent 1).

    Agent-output fields are JSON-serializable dict/list structures (not Pydantic instances) so
    they round-trip cleanly through the durable checkpointer (Phase 2 §6.4). Accumulating fields
    use add-reducers; set-once fields use last-value-wins (no reducer).
    """
    # ---- inputs (set by the orchestrator when the workflow starts) ----
    investigation_id: str
    incident: NormalizedIncident
    triage_hint: Optional[TriageHint]

    # ---- Intake Agent outputs ----
    classification: dict[str, Any]
    affected_systems: list[dict[str, Any]]
    initial_hypothesis: dict[str, Any]
    recommended_triage: str
    intake_metadata: dict[str, Any]
    intake_failed: bool

    # ---- Knowledge Retrieval Agent outputs ----
    knowledge_summary: str
    knowledge_findings: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    similar_incidents: list[dict[str, Any]]
    knowledge_conflicts: list[dict[str, Any]]
    knowledge_coverage: dict[str, Any]
    knowledge_metadata: dict[str, Any]
    knowledge_degraded: bool

    # ---- Architecture Discovery Agent outputs ----
    architecture_context: dict[str, Any]
    architecture_metadata: dict[str, Any]
    architecture_degraded: bool

    # ---- RCA Agent outputs ----
    rca: dict[str, Any]
    rca_confidence: str
    rca_metadata: dict[str, Any]
    rca_degraded: bool

    # ---- Recommendation Agent outputs ----
    recommendations: dict[str, Any]
    recommendation_metadata: dict[str, Any]
    recommendation_degraded: bool

    # ---- Communication Agent outputs ----
    communications: dict[str, Any]
    communication_metadata: dict[str, Any]

    # ---- human review ----
    human_decision: dict[str, Any]

    # ---- cross-cutting ----
    status: str
    errors: Annotated[list[dict[str, Any]], operator.add]
