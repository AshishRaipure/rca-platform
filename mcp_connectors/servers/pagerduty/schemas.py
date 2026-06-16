"""PagerDuty tool input schemas + compact result projections.

Input models validate the params the gateway/agent pass. Projection models trim PagerDuty's
verbose payloads (``extra="ignore"``) to the fields the platform actually consumes, so tool
results are small and stable rather than raw API dumps.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------- tool inputs

class GetIncidentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str = Field(min_length=1)


class ListIncidentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statuses: list[str] = Field(default_factory=lambda: ["triggered", "acknowledged"])
    since: Optional[str] = None
    until: Optional[str] = None
    service_ids: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)
    urgencies: list[str] = Field(default_factory=list)
    max_items: Optional[int] = Field(default=None, ge=1)


class IncidentChildInput(BaseModel):
    """Shared input for incident sub-resources (alerts, log entries, notes)."""
    model_config = ConfigDict(extra="forbid")
    incident_id: str = Field(min_length=1)
    max_items: Optional[int] = Field(default=None, ge=1)


class GetServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: str = Field(min_length=1)


class ListServicesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Optional[str] = None
    team_ids: list[str] = Field(default_factory=list)
    max_items: Optional[int] = Field(default=None, ge=1)


class OnCallsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    escalation_policy_ids: list[str] = Field(default_factory=list)
    schedule_ids: list[str] = Field(default_factory=list)
    since: Optional[str] = None
    until: Optional[str] = None
    max_items: Optional[int] = Field(default=None, ge=1)


class GetUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1)


# --------------------------------------------------------------- projections

class PDRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    html_url: Optional[str] = None


class PDIncident(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    incident_number: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    urgency: Optional[str] = None
    priority: Optional[PDRef] = None
    service: Optional[PDRef] = None
    escalation_policy: Optional[PDRef] = None
    teams: list[PDRef] = Field(default_factory=list)
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    incident_key: Optional[str] = None
    created_at: Optional[str] = None
    html_url: Optional[str] = None


class PDAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    status: Optional[str] = None
    alert_key: Optional[str] = None
    severity: Optional[str] = None
    summary: Optional[str] = None
    service: Optional[PDRef] = None
    created_at: Optional[str] = None
    body: dict[str, Any] = Field(default_factory=dict)


class PDService(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    teams: list[PDRef] = Field(default_factory=list)
    html_url: Optional[str] = None


class PDOnCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user: Optional[PDRef] = None
    escalation_policy: Optional[PDRef] = None
    schedule: Optional[PDRef] = None
    escalation_level: Optional[int] = None
    start: Optional[str] = None
    end: Optional[str] = None


class PDLogEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    created_at: Optional[str] = None
    agent: Optional[PDRef] = None
    channel: dict[str, Any] = Field(default_factory=dict)


class PDUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    time_zone: Optional[str] = None


class PDNote(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    content: Optional[str] = None
    created_at: Optional[str] = None
    user: Optional[PDRef] = None
