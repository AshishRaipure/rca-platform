"""Append-only, tamper-evident audit log (Phase 2 §1.10).

Each record extends a hash chain: ``this_hash = sha256(prev_hash || canonical(entry))``. Any
later tampering breaks the chain from that point on. This module is the canonical home for the
``AuditSink`` Protocol that the agents already depend on (their locally-declared Protocols are
structurally identical), plus a Postgres-backed sink and an in-memory sink for tests/dev.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional, Protocol
from uuid import UUID, uuid4

GENESIS_HASH = "0" * 64
# a fixed key so concurrent audit appends serialize on one advisory lock within their tx
AUDIT_ADVISORY_LOCK_KEY = 8_273_001


def _canonical(entry: dict[str, Any]) -> bytes:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_entry_hash(prev_hash: str, entry: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(_canonical(entry))
    return h.hexdigest()


def _entry(category: str, action: str, actor_id: str, investigation_id: Optional[UUID],
           request_id: str, model_id: Optional[str], model_version: Optional[str],
           tool_name: Optional[str], tool_params: Optional[dict[str, Any]],
           result_summary: Optional[str], metadata: Optional[dict[str, Any]],
           occurred_at: datetime) -> dict[str, Any]:
    return {
        "category": category, "action": action, "actor_id": actor_id,
        "investigation_id": str(investigation_id) if investigation_id else None,
        "request_id": request_id, "model_id": model_id, "model_version": model_version,
        "tool_name": tool_name, "tool_params": tool_params, "result_summary": result_summary,
        "metadata": metadata, "occurred_at": occurred_at.isoformat(),
    }


class AuditSink(Protocol):
    async def record(
        self, *, category: str, action: str, actor_id: str,
        investigation_id: Optional[UUID], request_id: str,
        model_id: Optional[str] = None, model_version: Optional[str] = None,
        tool_name: Optional[str] = None, tool_params: Optional[dict[str, Any]] = None,
        result_summary: Optional[str] = None, metadata: Optional[dict[str, Any]] = None,
    ) -> None: ...


class PostgresAuditSink:
    """Writes one chained row per event. Bind it to the SAME session/transaction as the action
    being audited so the audit write commits atomically with the action."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def record(self, *, category, action, actor_id, investigation_id, request_id,
                     model_id=None, model_version=None, tool_name=None, tool_params=None,
                     result_summary=None, metadata=None) -> None:
        # imported lazily so this module loads without SQLAlchemy installed
        from sqlalchemy import select, text

        from db.models import AuditLogRow

        occurred_at = datetime.now(timezone.utc)
        entry = _entry(category, action, actor_id, investigation_id, request_id, model_id,
                       model_version, tool_name, tool_params, result_summary, metadata, occurred_at)

        # serialize chain appends across concurrent transactions
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": AUDIT_ADVISORY_LOCK_KEY}
        )
        head = (
            await self._session.execute(
                select(AuditLogRow).order_by(AuditLogRow.seq.desc()).limit(1)
            )
        ).scalar_one_or_none()
        prev_hash = head.this_hash if head else GENESIS_HASH
        this_hash = compute_entry_hash(prev_hash, entry)

        self._session.add(AuditLogRow(
            id=uuid4(), category=category, action=action, actor_id=actor_id,
            investigation_id=investigation_id, request_id=request_id, model_id=model_id,
            model_version=model_version, tool_name=tool_name, tool_params=tool_params,
            result_summary=result_summary, audit_metadata=metadata,
            prev_hash=prev_hash, this_hash=this_hash, occurred_at=occurred_at,
        ))
        await self._session.flush()


class InMemoryAuditSink:
    """Dev/test sink that maintains the same hash chain in memory."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._prev_hash = GENESIS_HASH

    async def record(self, *, category, action, actor_id, investigation_id, request_id,
                     model_id=None, model_version=None, tool_name=None, tool_params=None,
                     result_summary=None, metadata=None) -> None:
        occurred_at = datetime.now(timezone.utc)
        entry = _entry(category, action, actor_id, investigation_id, request_id, model_id,
                       model_version, tool_name, tool_params, result_summary, metadata, occurred_at)
        this_hash = compute_entry_hash(self._prev_hash, entry)
        self.events.append({**entry, "prev_hash": self._prev_hash, "this_hash": this_hash,
                            "seq": len(self.events) + 1})
        self._prev_hash = this_hash

    def verify_chain(self) -> bool:
        prev = GENESIS_HASH
        for row in self.events:
            entry = {k: row[k] for k in (
                "category", "action", "actor_id", "investigation_id", "request_id", "model_id",
                "model_version", "tool_name", "tool_params", "result_summary", "metadata",
                "occurred_at")}
            if row["prev_hash"] != prev or row["this_hash"] != compute_entry_hash(prev, entry):
                return False
            prev = row["this_hash"]
        return True
