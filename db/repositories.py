"""Repositories + unit-of-work.

The unit-of-work opens one scoped transaction, applies the ABAC GUCs, and exposes the repos plus
a session-bound audit sink — so an action and its audit row commit atomically on the same hash
chain. Repositories also apply explicit team filters (defense-in-depth alongside RLS).
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Optional, Protocol

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from contracts.models import NormalizedIncident
from db.engine import Database, apply_scope
from db.models import Approval, Feedback, Incident, Investigation
from libs.audit.sink import AuditSink, PostgresAuditSink


def _team_clause(model: Any, scope: dict[str, Any]):
    """A SQL condition restricting rows to the principal's teams (None == unrestricted, admin)."""
    if scope.get("is_admin"):
        return None
    teams = scope.get("team_ids") or []
    if not teams:
        return model.team_id.in_([])  # no teams -> see nothing
    return model.team_id.in_(teams)


class InvestigationRepository:
    def __init__(self, session: AsyncSession, scope: dict[str, Any]) -> None:
        self._s = session
        self._scope = scope

    async def create(self, *, incident: NormalizedIncident, title: str, team_id: str,
                     status: str = "created", idempotency_key: Optional[str] = None) -> Investigation:
        self._s.add(Incident(
            id=incident.incident_id, source_system=incident.source_system.value,
            fingerprint=incident.fingerprint, title=incident.title, description=incident.description,
            provider_severity=incident.provider_severity.value if incident.provider_severity else None,
            pagerduty_id=incident.pagerduty_id, servicenow_id=incident.servicenow_id,
            raw_payload=incident.raw_payload, team_id=team_id,
        ))
        inv = Investigation(
            id=uuid.uuid4(), incident_id=incident.incident_id, status=status,
            severity=incident.provider_severity.value if incident.provider_severity else None,
            title=title, team_id=team_id, idempotency_key=idempotency_key, state={},
        )
        self._s.add(inv)
        await self._s.flush()
        return inv

    async def get(self, investigation_id: uuid.UUID) -> Optional[Investigation]:
        stmt = select(Investigation).where(Investigation.id == investigation_id)
        clause = _team_clause(Investigation, self._scope)
        if clause is not None:
            stmt = stmt.where(clause)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(self, key: str) -> Optional[Investigation]:
        stmt = select(Investigation).where(Investigation.idempotency_key == key)
        clause = _team_clause(Investigation, self._scope)
        if clause is not None:
            stmt = stmt.where(clause)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def list(self, *, limit: int, after: Optional[tuple[datetime.datetime, uuid.UUID]] = None,
                   status: Optional[str] = None) -> list[Investigation]:
        stmt = select(Investigation)
        clause = _team_clause(Investigation, self._scope)
        if clause is not None:
            stmt = stmt.where(clause)
        if status:
            stmt = stmt.where(Investigation.status == status)
        if after is not None:
            stmt = stmt.where(
                tuple_(Investigation.created_at, Investigation.id) < tuple_(after[0], after[1]))
        stmt = stmt.order_by(Investigation.created_at.desc(), Investigation.id.desc()).limit(limit)
        return list((await self._s.execute(stmt)).scalars().all())

    async def set_state(self, investigation_id: uuid.UUID, state: dict[str, Any]) -> None:
        inv = await self.get(investigation_id)
        if inv is not None:
            inv.state = state

    async def set_status(self, investigation_id: uuid.UUID, status: str,
                         severity: Optional[str] = None) -> None:
        inv = await self.get(investigation_id)
        if inv is not None:
            inv.status = status
            if severity:
                inv.severity = severity


class ApprovalRepository:
    def __init__(self, session: AsyncSession, scope: dict[str, Any]) -> None:
        self._s = session
        self._scope = scope

    async def create(self, *, investigation_id: uuid.UUID, decision: str, target: str,
                     decided_by: str, team_id: str, target_id: Optional[str] = None,
                     comment: Optional[str] = None) -> Approval:
        row = Approval(id=uuid.uuid4(), investigation_id=investigation_id, decision=decision,
                       target=target, target_id=target_id, comment=comment, decided_by=decided_by,
                       team_id=team_id)
        self._s.add(row)
        await self._s.flush()
        return row

    async def list(self, investigation_id: uuid.UUID) -> list[Approval]:
        stmt = select(Approval).where(Approval.investigation_id == investigation_id)
        clause = _team_clause(Approval, self._scope)
        if clause is not None:
            stmt = stmt.where(clause)
        return list((await self._s.execute(stmt.order_by(Approval.decided_at.asc()))).scalars().all())


class FeedbackRepository:
    def __init__(self, session: AsyncSession, scope: dict[str, Any]) -> None:
        self._s = session
        self._scope = scope

    async def create(self, *, investigation_id: uuid.UUID, submitted_by: str, team_id: str,
                     rating: Optional[int] = None, useful: Optional[bool] = None,
                     category: Optional[str] = None, comment: Optional[str] = None,
                     target_id: Optional[str] = None) -> Feedback:
        row = Feedback(id=uuid.uuid4(), investigation_id=investigation_id, rating=rating,
                       useful=useful, category=category, comment=comment, target_id=target_id,
                       submitted_by=submitted_by, team_id=team_id)
        self._s.add(row)
        await self._s.flush()
        return row


class UnitOfWork(Protocol):
    investigations: Any
    approvals: Any
    feedback: Any
    audit: AuditSink

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...


class SqlAlchemyUnitOfWork:
    def __init__(self, db: Database, scope: dict[str, Any]) -> None:
        self._db = db
        self._scope = scope
        self._session: Optional[AsyncSession] = None

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._db.new_session()
        await self._session.begin()
        await apply_scope(self._session, self._scope)
        self.investigations = InvestigationRepository(self._session, self._scope)
        self.approvals = ApprovalRepository(self._session, self._scope)
        self.feedback = FeedbackRepository(self._session, self._scope)
        self.audit: AuditSink = PostgresAuditSink(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self._session is not None
        try:
            if exc_type is not None:
                await self._session.rollback()
            else:
                await self._session.commit()
        finally:
            await self._session.close()
