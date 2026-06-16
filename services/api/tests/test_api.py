"""API tests (fakes; no DB, no IdP, no network).

These exercise the HTTP layer end-to-end with in-memory fakes injected through AppDeps: a fake
token verifier (RBAC), a shared-store unit-of-work backed by InMemoryAuditSink (so audit rows are
observable), and a recording orchestrator (so we can assert start/resume were called).

Run with: pytest -q  (requires fastapi, starlette, httpx, pydantic v2). Syntax-checked here.
"""
from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from libs.audit.sink import InMemoryAuditSink
from services.api.app import create_app
from services.api.auth import VerifiedToken
from services.api.config import ApiSettings
from services.api.deps import AppDeps
from services.api.errors import Unauthorized
from services.orchestrator.client import InvestigationStatus


# --------------------------------------------------------------------------- fakes

class FakeTokenVerifier:
    """Maps an opaque test token to roles + a team. Unknown tokens -> Unauthorized."""

    TOKENS = {
        "viewer": (["viewer"], ["team-a"]),
        "responder": (["responder"], ["team-a"]),
        "approver": (["approver", "responder"], ["team-a"]),
        "admin": (["admin"], []),
        "other-team": (["responder"], ["team-b"]),
    }

    async def verify(self, token: str) -> VerifiedToken:
        if token not in self.TOKENS:
            raise Unauthorized("invalid token")
        roles, teams = self.TOKENS[token]
        return VerifiedToken(subject=f"user-{token}", email=f"{token}@example.com",
                             roles=roles, team_ids=teams)


class _Store:
    def __init__(self) -> None:
        self.investigations: dict[uuid.UUID, Any] = {}
        self.approvals: list[Any] = []
        self.feedback: list[Any] = []
        self.audit = InMemoryAuditSink()
        self._seq = 0

    def next_time(self) -> datetime.datetime:
        self._seq += 1
        return datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(
            seconds=self._seq)


def _in_scope(row: Any, scope: dict[str, Any]) -> bool:
    return scope.get("is_admin") or row.team_id in (scope.get("team_ids") or [])


class _Investigations:
    def __init__(self, store: _Store, scope: dict[str, Any]) -> None:
        self._store, self._scope = store, scope

    async def create(self, *, incident, title, team_id, status="created", idempotency_key=None):
        now = self._store.next_time()
        row = SimpleNamespace(
            id=uuid.uuid4(), incident_id=incident.incident_id, status=status,
            severity=incident.provider_severity.value if incident.provider_severity else None,
            title=title, team_id=team_id, idempotency_key=idempotency_key, state={},
            created_at=now, updated_at=now)
        self._store.investigations[row.id] = row
        return row

    async def get(self, investigation_id):
        row = self._store.investigations.get(investigation_id)
        return row if row and _in_scope(row, self._scope) else None

    async def get_by_idempotency_key(self, key):
        for row in self._store.investigations.values():
            if row.idempotency_key == key and _in_scope(row, self._scope):
                return row
        return None

    async def list(self, *, limit, after=None, status=None):
        rows = [r for r in self._store.investigations.values() if _in_scope(r, self._scope)]
        if status:
            rows = [r for r in rows if r.status == status]
        rows.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        if after is not None:
            at, aid = after
            rows = [r for r in rows if (r.created_at, r.id) < (at, aid)]
        return rows[:limit]

    async def set_status(self, investigation_id, status, severity=None):
        row = self._store.investigations.get(investigation_id)
        if row:
            row.status = status
            if severity:
                row.severity = severity

    async def set_state(self, investigation_id, state):
        row = self._store.investigations.get(investigation_id)
        if row:
            row.state = state


class _Approvals:
    def __init__(self, store: _Store, scope: dict[str, Any]) -> None:
        self._store, self._scope = store, scope

    async def create(self, *, investigation_id, decision, target, decided_by, team_id,
                     target_id=None, comment=None):
        row = SimpleNamespace(
            id=uuid.uuid4(), investigation_id=investigation_id, decision=decision, target=target,
            target_id=target_id, comment=comment, decided_by=decided_by, team_id=team_id,
            decided_at=self._store.next_time())
        self._store.approvals.append(row)
        return row

    async def list(self, investigation_id):
        return [a for a in self._store.approvals if a.investigation_id == investigation_id]


class _Feedback:
    def __init__(self, store: _Store, scope: dict[str, Any]) -> None:
        self._store, self._scope = store, scope

    async def create(self, *, investigation_id, submitted_by, team_id, rating=None, useful=None,
                     category=None, comment=None, target_id=None):
        row = SimpleNamespace(id=uuid.uuid4(), investigation_id=investigation_id)
        self._store.feedback.append(row)
        return row


class FakeUnitOfWork:
    def __init__(self, store: _Store, scope: dict[str, Any]) -> None:
        self._store, self._scope = store, scope

    async def __aenter__(self):
        self.investigations = _Investigations(self._store, self._scope)
        self.approvals = _Approvals(self._store, self._scope)
        self.feedback = _Feedback(self._store, self._scope)
        self.audit = self._store.audit
        return self

    async def __aexit__(self, *exc):
        return None


class FakeOrchestrator:
    def __init__(self) -> None:
        self.start_calls: list[uuid.UUID] = []
        self.resume_calls: list[tuple[uuid.UUID, str]] = []

    async def start(self, *, investigation_id, incident, triage_hint, scope):
        self.start_calls.append(investigation_id)
        return InvestigationStatus(investigation_id=investigation_id, status="awaiting_approval")

    async def get_status(self, investigation_id, scope):
        return InvestigationStatus(investigation_id=investigation_id, status="awaiting_approval")

    async def resume_after_approval(self, *, investigation_id, decision, target, target_id,
                                    decided_by, scope):
        self.resume_calls.append((investigation_id, decision))
        return InvestigationStatus(investigation_id=investigation_id, status="completed")


# --------------------------------------------------------------------------- harness

def _build(store: Optional[_Store] = None, orchestrator: Optional[FakeOrchestrator] = None):
    store = store or _Store()
    orchestrator = orchestrator or FakeOrchestrator()
    deps = AppDeps(
        settings=ApiSettings(environment="test", default_page_size=25),
        token_verifier=FakeTokenVerifier(),
        uow_factory=lambda scope: FakeUnitOfWork(store, scope),
        orchestrator=orchestrator,
    )
    return TestClient(create_app(deps)), store, orchestrator


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client, token="responder", **body) -> Any:
    payload = {"title": "DB latency spike", "source_system": "manual"}
    payload.update(body)
    return client.post("/v1/investigations", json=payload, headers=_auth(token))


# --------------------------------------------------------------------------- tests

def test_missing_token_is_401_problem_json():
    client, _, _ = _build()
    r = client.get("/v1/me")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "WWW-Authenticate" in r.headers
    assert r.json()["status"] == 401


def test_invalid_token_is_401():
    client, _, _ = _build()
    assert client.get("/v1/me", headers=_auth("nope")).status_code == 401


def test_me_returns_identity():
    client, _, _ = _build()
    r = client.get("/v1/me", headers=_auth("approver"))
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "user-approver"
    assert set(body["roles"]) == {"approver", "responder"}


def test_viewer_cannot_create_investigation_403():
    client, _, orch = _build()
    r = _create(client, token="viewer")
    assert r.status_code == 403
    assert orch.start_calls == []


def test_create_investigation_starts_workflow_and_audits():
    client, store, orch = _build()
    r = _create(client, token="responder")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "awaiting_approval"      # reflects orchestrator
    assert len(orch.start_calls) == 1
    actions = [e["action"] for e in store.audit.events]
    assert "investigation.created" in actions
    assert store.audit.verify_chain()


def test_idempotency_key_returns_existing():
    client, store, orch = _build()
    headers = {**_auth("responder"), "Idempotency-Key": "abc-123"}
    payload = {"title": "same", "source_system": "manual"}
    first = client.post("/v1/investigations", json=payload, headers=headers)
    second = client.post("/v1/investigations", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(orch.start_calls) == 1                 # second call short-circuits


def test_get_investigation_404_when_out_of_scope_or_absent():
    client, _, _ = _build()
    assert client.get(f"/v1/investigations/{uuid.uuid4()}",
                      headers=_auth("viewer")).status_code == 404


def test_get_investigation_200():
    client, store, _ = _build()
    created = _create(client, token="responder").json()
    r = client.get(f"/v1/investigations/{created['id']}", headers=_auth("viewer"))
    assert r.status_code == 200
    assert r.json()["incident_id"]


def test_other_team_cannot_read_investigation():
    client, store, _ = _build()
    created = _create(client, token="responder").json()  # team-a
    r = client.get(f"/v1/investigations/{created['id']}", headers=_auth("other-team"))
    assert r.status_code == 404                            # no existence leak


def test_list_is_cursor_paginated():
    client, store, _ = _build()
    for i in range(3):
        _create(client, token="responder", title=f"inc-{i}")
    first = client.get("/v1/investigations?limit=2", headers=_auth("responder")).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    nxt = client.get(f"/v1/investigations?limit=2&cursor={first['next_cursor']}",
                     headers=_auth("responder")).json()
    assert len(nxt["items"]) == 1
    assert nxt["next_cursor"] is None


def test_record_approval_resumes_workflow_and_audits():
    client, store, orch = _build()
    created = _create(client, token="responder").json()
    r = client.post(f"/v1/investigations/{created['id']}/approvals",
                    json={"decision": "approve", "target": "review_gate"}, headers=_auth("approver"))
    assert r.status_code == 201
    assert r.json()["workflow_status"] == "completed"
    assert orch.resume_calls and orch.resume_calls[0][1] == "approve"
    assert "approval.recorded" in [e["action"] for e in store.audit.events]


def test_responder_cannot_record_approval_403():
    client, store, _ = _build()
    created = _create(client, token="responder").json()
    # 'responder' token lacks the approver role
    r = client.post(f"/v1/investigations/{created['id']}/approvals",
                    json={"decision": "approve"}, headers=_auth("responder"))
    assert r.status_code == 403


def test_submit_feedback_202():
    client, store, _ = _build()
    created = _create(client, token="responder").json()
    r = client.post(f"/v1/investigations/{created['id']}/feedback",
                    json={"useful": True, "rating": 5}, headers=_auth("viewer"))
    assert r.status_code == 202
    assert r.json()["accepted"] is True


def test_create_rejects_unknown_fields_422():
    client, _, _ = _build()
    r = client.post("/v1/investigations", json={"title": "x", "bogus": 1}, headers=_auth("responder"))
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
