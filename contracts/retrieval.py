"""Retrieval contracts (contracts layer).

Shared between the RAG layer (`rag/retrieval/*`) and the Knowledge Retrieval Agent. The agent
depends on these shapes, not on a specific vector store — keeping retrieval store-agnostic
(OpenSearch primary, pgvector alternative; Phase 2 §0). Outcome + freshness metadata travel with
every result so the agent can honor R-6 (historical RCAs can be wrong) and never present a
refuted or stale source as fact.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import SourceSystem


class SourceType(str, Enum):
    runbook = "runbook"
    confluence = "confluence"
    rca = "rca"
    incident = "incident"
    knowledge_base = "knowledge_base"
    slack = "slack"
    ticket = "ticket"
    other = "other"


class DocumentOutcome(str, Enum):
    """Was the conclusion in this source borne out?"""
    confirmed = "confirmed"      # human-confirmed correct (episodic ground truth / validated RCA)
    refuted = "refuted"          # later shown to be wrong
    unconfirmed = "unconfirmed"  # never validated either way


class RetrievalFilters(BaseModel):
    """Pre-filters applied by the retriever. ABAC scope is mandatory and never widened."""
    scope: dict = Field(default_factory=dict)  # team_scope / service_scope / sensitivity
    source_types: Optional[list[SourceType]] = None
    prefer_current: bool = True


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk_id: str
    document_id: str
    source_system: SourceSystem
    source_type: SourceType
    title: str
    uri: Optional[str] = None
    text: str
    score: float = 0.0
    rerank_score: Optional[float] = None
    last_modified: Optional[datetime] = None
    is_current: bool = True
    outcome: Optional[DocumentOutcome] = None
    sensitivity: Optional[str] = None


class EpisodicMatch(BaseModel):
    """A similar PAST incident. Episodic memory holds only human-confirmed outcomes (Phase 2 §7.3)."""
    model_config = ConfigDict(extra="ignore")

    incident_id: str
    title: str
    similarity: float = 0.0
    occurred_at: Optional[datetime] = None
    confirmed_root_cause: Optional[str] = None
    confirmed_resolution: Optional[str] = None
    outcome: DocumentOutcome = DocumentOutcome.confirmed
    is_current: bool = True
    uri: Optional[str] = None
