"""ServiceNow tool input schemas + projections.

ServiceNow fields come back either as scalars (``sysparm_display_value=false``) or as
``{"display_value", "value", "link"}`` objects (``=all``). ``sn_value``/``sn_display`` normalize
both. Projections are built with ``from_record`` and keep only the fields the platform uses.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# --------------------------------------------------------- ServiceNow field extractors

def sn_value(rec: dict[str, Any], field: str, default: Any = None) -> Any:
    v = rec.get(field, default)
    if isinstance(v, dict):
        return v.get("value", default)
    return v


def sn_display(rec: dict[str, Any], field: str, default: Any = None) -> Any:
    v = rec.get(field)
    if isinstance(v, dict):
        return v.get("display_value", v.get("value", default))
    return v if v is not None else default


# ------------------------------------------------------------------- tool inputs

class _RequiresIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _at_least_one(self):
        if not any(getattr(self, f, None) for f in self.model_fields if f != "max_items"):
            raise ValueError("at least one identifier (sys_id/number/name/id) is required")
        return self


class GetIncidentInput(_RequiresIdentifier):
    sys_id: Optional[str] = None
    number: Optional[str] = None


class ListIncidentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Optional[str] = None  # raw ServiceNow encoded query, appended to convenience filters
    state: Optional[str] = None
    priority: Optional[str] = None
    assignment_group: Optional[str] = None
    cmdb_ci: Optional[str] = None
    opened_after: Optional[str] = None  # "YYYY-MM-DD hh:mm:ss"
    max_items: Optional[int] = Field(default=None, ge=1)


class IncidentJournalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sys_id: str = Field(min_length=1)
    max_items: Optional[int] = Field(default=None, ge=1)


class GetChangeInput(_RequiresIdentifier):
    sys_id: Optional[str] = None
    number: Optional[str] = None


class ListChangesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Optional[str] = None
    cmdb_ci: Optional[str] = None
    state: Optional[str] = None
    closed_after: Optional[str] = None
    max_items: Optional[int] = Field(default=None, ge=1)


class GetKnowledgeInput(_RequiresIdentifier):
    # Agent 2's freshness probe calls this tool with {"id": <sys_id>}; `id` is accepted as sys_id.
    id: Optional[str] = None
    sys_id: Optional[str] = None
    number: Optional[str] = None


class SearchKnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)
    max_items: Optional[int] = Field(default=None, ge=1)


class GetConfigItemInput(_RequiresIdentifier):
    sys_id: Optional[str] = None
    name: Optional[str] = None


class CIRelationshipsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ci_sys_id: str = Field(min_length=1)
    max_items: Optional[int] = Field(default=None, ge=1)


class GetUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sys_id: str = Field(min_length=1)


# --------------------------------------------------------------- projections

class SNIncident(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sys_id: Optional[str] = None
    number: Optional[str] = None
    short_description: Optional[str] = None
    description: Optional[str] = None
    state: Optional[str] = None
    state_label: Optional[str] = None
    priority: Optional[str] = None
    priority_label: Optional[str] = None
    urgency: Optional[str] = None
    impact: Optional[str] = None
    severity: Optional[str] = None
    category: Optional[str] = None
    opened_at: Optional[str] = None
    assignment_group: Optional[str] = None
    assigned_to: Optional[str] = None
    caller: Optional[str] = None
    cmdb_ci: Optional[str] = None
    cmdb_ci_id: Optional[str] = None
    close_code: Optional[str] = None
    close_notes: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "SNIncident":
        return cls(
            sys_id=sn_value(rec, "sys_id"), number=sn_value(rec, "number"),
            short_description=sn_display(rec, "short_description"),
            description=sn_display(rec, "description"),
            state=sn_value(rec, "state"), state_label=sn_display(rec, "state"),
            priority=sn_value(rec, "priority"), priority_label=sn_display(rec, "priority"),
            urgency=sn_display(rec, "urgency"), impact=sn_display(rec, "impact"),
            severity=sn_display(rec, "severity"), category=sn_display(rec, "category"),
            opened_at=sn_value(rec, "opened_at"),
            assignment_group=sn_display(rec, "assignment_group"),
            assigned_to=sn_display(rec, "assigned_to"), caller=sn_display(rec, "caller_id"),
            cmdb_ci=sn_display(rec, "cmdb_ci"), cmdb_ci_id=sn_value(rec, "cmdb_ci"),
            close_code=sn_display(rec, "close_code"), close_notes=sn_display(rec, "close_notes"),
        )


class SNChange(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sys_id: Optional[str] = None
    number: Optional[str] = None
    short_description: Optional[str] = None
    type: Optional[str] = None
    state: Optional[str] = None
    state_label: Optional[str] = None
    risk: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    cmdb_ci: Optional[str] = None
    cmdb_ci_id: Optional[str] = None
    assignment_group: Optional[str] = None
    close_code: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "SNChange":
        return cls(
            sys_id=sn_value(rec, "sys_id"), number=sn_value(rec, "number"),
            short_description=sn_display(rec, "short_description"),
            type=sn_display(rec, "type"),
            state=sn_value(rec, "state"), state_label=sn_display(rec, "state"),
            risk=sn_display(rec, "risk"),
            start_date=sn_value(rec, "start_date"), end_date=sn_value(rec, "end_date"),
            cmdb_ci=sn_display(rec, "cmdb_ci"), cmdb_ci_id=sn_value(rec, "cmdb_ci"),
            assignment_group=sn_display(rec, "assignment_group"),
            close_code=sn_display(rec, "close_code"),
        )


class SNJournalEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    element: Optional[str] = None  # "work_notes" | "comments"
    value: Optional[str] = None
    created_on: Optional[str] = None
    created_by: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "SNJournalEntry":
        return cls(
            element=sn_value(rec, "element"), value=sn_value(rec, "value"),
            created_on=sn_value(rec, "sys_created_on"), created_by=sn_display(rec, "sys_created_by"),
        )


class SNKnowledge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sys_id: Optional[str] = None
    number: Optional[str] = None
    short_description: Optional[str] = None
    text: Optional[str] = None
    workflow_state: Optional[str] = None
    category: Optional[str] = None
    updated_on: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "SNKnowledge":
        return cls(
            sys_id=sn_value(rec, "sys_id"), number=sn_value(rec, "number"),
            short_description=sn_display(rec, "short_description"),
            text=sn_display(rec, "text"), workflow_state=sn_display(rec, "workflow_state"),
            category=sn_display(rec, "kb_category"), updated_on=sn_value(rec, "sys_updated_on"),
        )


class SNConfigItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sys_id: Optional[str] = None
    name: Optional[str] = None
    ci_class: Optional[str] = None
    operational_status: Optional[str] = None
    environment: Optional[str] = None
    owned_by: Optional[str] = None
    support_group: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "SNConfigItem":
        return cls(
            sys_id=sn_value(rec, "sys_id"), name=sn_display(rec, "name"),
            ci_class=sn_value(rec, "sys_class_name"),
            operational_status=sn_display(rec, "operational_status"),
            environment=sn_display(rec, "environment"), owned_by=sn_display(rec, "owned_by"),
            support_group=sn_display(rec, "support_group"),
        )


class SNRelationship(BaseModel):
    model_config = ConfigDict(extra="ignore")
    parent: Optional[str] = None
    parent_id: Optional[str] = None
    child: Optional[str] = None
    child_id: Optional[str] = None
    type: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "SNRelationship":
        return cls(
            parent=sn_display(rec, "parent"), parent_id=sn_value(rec, "parent"),
            child=sn_display(rec, "child"), child_id=sn_value(rec, "child"),
            type=sn_display(rec, "type"),
        )


class SNUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sys_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    user_name: Optional[str] = None
    title: Optional[str] = None
    active: Optional[str] = None

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "SNUser":
        return cls(
            sys_id=sn_value(rec, "sys_id"), name=sn_display(rec, "name"),
            email=sn_value(rec, "email"), user_name=sn_value(rec, "user_name"),
            title=sn_display(rec, "title"), active=sn_value(rec, "active"),
        )
