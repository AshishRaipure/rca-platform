"""Communication Agent — schemas.

Produces DRAFTS only (Slack update, ServiceNow work-note draft, exec summary) plus the platform's
own RCA report. The platform never posts these anywhere; status is always 'draft'.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from contracts.enums import SeverityLevel
from contracts.models import NormalizedIncident


class CommunicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: UUID
    incident: NormalizedIncident
    severity: SeverityLevel
    classification: dict[str, Any] = Field(default_factory=dict)
    rca: dict[str, Any] = Field(default_factory=dict)
    recommendations: dict[str, Any] = Field(default_factory=dict)


class CommunicationDraft(BaseModel):
    channel: str  # slack | servicenow_worknote | exec_summary
    audience: Optional[str] = None
    content: str
    status: str = "draft"


class CommunicationOutput(BaseModel):
    drafts: list[CommunicationDraft] = Field(default_factory=list)
    rca_report: str = ""
    status: str = "draft"
    metadata: dict[str, Any] = Field(default_factory=dict)


class _LLMCommResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slack: str = ""
    worknote: str = ""
    exec_summary: str = ""
    rca_report: str = ""
