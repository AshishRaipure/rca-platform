"""Knowledge Retrieval Agent — Input & Output schemas.

Public contract:
  * KnowledgeInput  — built from InvestigationState by the node (incident + intake signals).
  * KnowledgeOutput — a grounded, fully-cited synthesis: findings, citations, similar incidents,
    explicit conflicts, and a coverage/confidence self-assessment.

Every finding must carry >=1 citation (grounding is mandatory). Every conflict references >=2
citations. Confidence and similarity are GRADES, never fabricated numbers.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import ConfidenceGrade, SeverityLevel, SourceSystem
from contracts.models import NormalizedIncident
from contracts.retrieval import DocumentOutcome, SourceType


# --------------------------------------------------------------------------- input

class KnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    incident: NormalizedIncident
    severity: SeverityLevel
    affected_systems: list[str] = Field(default_factory=list)  # names from intake
    initial_hypothesis: Optional[str] = None


# --------------------------------------------------------------------- output parts

class Citation(BaseModel):
    """A stable, downstream-reusable reference to a retrieved source."""
    citation_id: str  # short handle assigned by the agent, e.g. "c1"
    document_id: str
    chunk_id: Optional[str] = None
    source_system: SourceSystem
    source_type: SourceType
    title: str
    uri: Optional[str] = None
    snippet: str = ""
    is_current: bool = True
    outcome: Optional[DocumentOutcome] = None


class KnowledgeFinding(BaseModel):
    statement: str
    citation_ids: list[str] = Field(min_length=1)  # grounding is mandatory
    confidence: ConfidenceGrade
    caveat: Optional[str] = None  # set for stale / refuted / contested sources


class SimilarIncident(BaseModel):
    incident_id: str
    title: str
    similarity: ConfidenceGrade  # graded for human consumption, not a raw float
    confirmed_root_cause: Optional[str] = None
    confirmed_resolution: Optional[str] = None
    outcome: DocumentOutcome = DocumentOutcome.confirmed
    citation_id: Optional[str] = None


class KnowledgeConflict(BaseModel):
    description: str
    citation_ids: list[str] = Field(min_length=2)
    kind: str = "guidance"  # "guidance" | "root_cause" | "outcome"


class KnowledgeCoverage(BaseModel):
    retrieval_confidence: ConfidenceGrade
    num_sources: int = 0
    num_similar_incidents: int = 0
    has_runbook: bool = False
    has_confirmed_rca: bool = False
    stale_sources: int = 0
    gaps: list[str] = Field(default_factory=list)


class KnowledgeMetadata(BaseModel):
    model_id: str
    model_version: Optional[str] = None
    prompt_version: str
    model_tier_used: str
    corpus_k: int = 0
    episodic_k: int = 0
    num_retrieved: int = 0
    num_episodic: int = 0
    expanded: bool = False
    freshness_checked: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    degraded: bool = False  # True when synthesis was skipped (retrieval-only) or retrieval failed
    warnings: list[str] = Field(default_factory=list)


class KnowledgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    summary: str
    findings: list[KnowledgeFinding] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    similar_incidents: list[SimilarIncident] = Field(default_factory=list)
    conflicts: list[KnowledgeConflict] = Field(default_factory=list)
    coverage: KnowledgeCoverage
    metadata: KnowledgeMetadata


# ----------------------------------------------------------------- internal (raw LLM)

class _LLMFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    statement: str
    citation_ids: list[str] = Field(default_factory=list)
    confidence: Optional[ConfidenceGrade] = None
    caveat: Optional[str] = None


class _LLMConflict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    citation_ids: list[str] = Field(default_factory=list)
    kind: str = "guidance"


class _LLMKnowledgeResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    findings: list[_LLMFinding] = Field(default_factory=list)
    conflicts: list[_LLMConflict] = Field(default_factory=list)
    retrieval_confidence: Optional[ConfidenceGrade] = None
    gaps: list[str] = Field(default_factory=list)
