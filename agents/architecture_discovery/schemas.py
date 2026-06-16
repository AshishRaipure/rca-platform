"""Architecture Discovery Agent — input & output schemas."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ArchitectureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_id: UUID
    affected_systems: list[str] = Field(default_factory=list)


class ArchitectureNodeInfo(BaseModel):
    ci_id: Optional[str] = None
    name: str
    ci_class: Optional[str] = None
    environment: Optional[str] = None
    status: Optional[str] = None


class DependencyEdge(BaseModel):
    source: str
    target: str
    relationship: str = "depends_on"


class RecentChange(BaseModel):
    change_id: str
    summary: Optional[str] = None
    state: Optional[str] = None
    when: Optional[str] = None
    risk: Optional[str] = None


class ArchitectureContext(BaseModel):
    impacted: list[ArchitectureNodeInfo] = Field(default_factory=list)
    dependencies: list[DependencyEdge] = Field(default_factory=list)
    recent_changes: list[RecentChange] = Field(default_factory=list)
    summary: str = ""
    topology_freshness: str = "unknown"
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list)
