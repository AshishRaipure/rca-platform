"""SQLAlchemy ORM models for the entities the API persists.

A subset of the Phase 2 DDL (the tables the API touches). Every business table carries a
``team_id`` for ABAC/RLS. The full schema (hypotheses, evidence, topology, knowledge_documents,
etc.) lives in the Phase 2 migrations; this module covers what the API service reads/writes.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_system: Mapped[str] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_severity: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    pagerduty_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    servicenow_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    team_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True),
                                                          server_default=func.now())


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    severity: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    team_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True),
                                                          server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Approval(Base):
    """Records a HUMAN decision only. It never triggers an action by itself (Phase 2 §approvals)."""
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id"), index=True)
    decision: Mapped[str] = mapped_column(String(32))            # approve | reject | needs_changes
    target: Mapped[str] = mapped_column(String(64))              # review_gate | recommendation
    target_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(128))
    team_id: Mapped[str] = mapped_column(String(64), index=True)
    decided_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True),
                                                          server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id"), index=True)
    rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    useful: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    submitted_by: Mapped[str] = mapped_column(String(128))
    team_id: Mapped[str] = mapped_column(String(64), index=True)
    submitted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True),
                                                            server_default=func.now())


class Communication(Base):
    """Draft-only. The API never posts these externally (Phase 2 §comms MVP)."""
    __tablename__ = "communications"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    audience: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    content: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64))
    team_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True),
                                                          server_default=func.now())


class AuditLogRow(Base):
    """Append-only, hash-chained. WORM in production (no UPDATE/DELETE grants)."""
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(128))
    investigation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tool_params: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audit_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    this_hash: Mapped[str] = mapped_column(String(64), unique=True)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
