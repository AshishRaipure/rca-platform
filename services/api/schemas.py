"""API request/response schemas + cursor pagination."""
from __future__ import annotations

import base64
import datetime
import json
from typing import Any, Generic, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import SeverityLevel, SourceSystem

T = TypeVar("T")


# ----------------------------------------------------------------- requests

class CreateInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1)
    source_system: SourceSystem = SourceSystem.manual
    description: Optional[str] = None
    provider_severity: Optional[SeverityLevel] = None
    pagerduty_id: Optional[str] = None
    servicenow_id: Optional[str] = None
    fingerprint: Optional[str] = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    team_id: Optional[str] = None  # defaults to the caller's primary team


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(approve|reject|needs_changes)$")
    target: str = "review_gate"  # review_gate | recommendation
    target_id: Optional[str] = None
    comment: Optional[str] = None


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    useful: Optional[bool] = None
    category: Optional[str] = None
    comment: Optional[str] = None
    target_id: Optional[str] = None


# ----------------------------------------------------------------- responses

class InvestigationSummary(BaseModel):
    id: UUID
    status: str
    severity: Optional[str] = None
    title: str
    recommended_triage: Optional[str] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class InvestigationDetail(InvestigationSummary):
    incident_id: UUID
    team_id: str
    knowledge_summary: Optional[str] = None
    knowledge_degraded: Optional[bool] = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    classification: dict[str, Any] = Field(default_factory=dict)
    initial_hypothesis: dict[str, Any] = Field(default_factory=dict)


class ApprovalResponse(BaseModel):
    id: UUID
    investigation_id: UUID
    decision: str
    target: str
    target_id: Optional[str] = None
    comment: Optional[str] = None
    decided_by: str
    decided_at: datetime.datetime
    workflow_status: Optional[str] = None


class FeedbackResponse(BaseModel):
    id: UUID
    investigation_id: UUID
    accepted: bool = True


class MeResponse(BaseModel):
    subject: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    roles: list[str]
    team_ids: list[str]
    service_ids: list[str]


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: Optional[str] = None


# ------------------------------------------------------------------ cursors

def encode_cursor(created_at: datetime.datetime, item_id: UUID) -> str:
    payload = {"t": created_at.isoformat(), "id": str(item_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> Optional[tuple[datetime.datetime, UUID]]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return datetime.datetime.fromisoformat(payload["t"]), UUID(payload["id"])
    except Exception:
        return None
