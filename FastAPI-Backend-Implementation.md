# FastAPI Backend
## Implementation Guide

**Status:** Implemented (Phase 4) · **Service:** `services/api/` · **Supporting:** `db/`, `libs/audit/`, `services/orchestrator/`

This is the platform's HTTP API — the front door to the multi-agent investigation workflow. It covers the six requested concerns: **APIs, Authentication, Authorization, Database Integration, LangGraph Integration, and Audit Logging**, wiring in the agents (Intake, Knowledge) and the read-only MCP connectors already built.

### The safety invariant is an API contract
No endpoint mutates production. The API can **create and read investigations**, **record a human approval decision**, and **accept feedback** — and that is the whole surface. Specifically:
- Recording an approval writes a **decision** (`approve|reject|needs_changes`) and resumes the paused workflow. It does not ack an alert, close a ticket, restart a service, or deploy anything — the platform has no such capability anywhere.
- Communications are **draft-only**; the API never posts them externally.
- "Starting an investigation" is read/reasoning only; the workflow pauses at the human-review gate.

> **Validation status:** all 65 Python files compile on Python 3.12 and pass the cross-module import check. The API tests are written and syntax-checked **but not executed here** — fastapi/starlette/httpx/sqlalchemy/langgraph/pyjwt aren't installed and the network is off, so DB/HTTP/JWT code paths import lazily. Run `pytest -q` where those deps exist.

---

## 1. APIs

REST/JSON under `/v1`, cursor pagination, `Idempotency-Key` on create, RFC 9457 `application/problem+json` errors, SSE for live status.

| Method | Path | Role (min) | Purpose |
|---|---|---|---|
| GET | `/healthz`, `/readyz` | — | Liveness / readiness. |
| GET | `/v1/me` | any authenticated | Identity + roles/teams echo. |
| POST | `/v1/investigations` | responder | Create incident+investigation, then start the workflow. `201` (or `200` on idempotent replay). |
| GET | `/v1/investigations` | viewer | List (ABAC-scoped), cursor-paginated. |
| GET | `/v1/investigations/{id}` | viewer | Detail (status, classification, knowledge summary, citations, findings). `404` if out of scope. |
| GET | `/v1/investigations/{id}/stream` | viewer | SSE: polls workflow status until a terminal/awaiting-approval state. |
| POST | `/v1/investigations/{id}/approvals` | approver | Record a human decision, then resume the workflow. `201`. |
| POST | `/v1/investigations/{id}/feedback` | viewer | Submit feedback on the investigation/output. `202`. |

Routers: `routers/system.py`, `routers/investigations.py`, `routers/review.py`. The app is assembled by `create_app(deps)` in `app.py` (request-id middleware, optional CORS, problem+json handlers, lifespan).

---

## 2. Authentication

OIDC JWT **bearer** tokens (`auth.py`).
- `OIDCTokenVerifier` validates **signature** (RS256 via the IdP **JWKS**), **issuer**, **audience**, and **expiry** (PyJWT imported lazily; JWKS keys cached). Any failure → `401` with `WWW-Authenticate: Bearer`.
- It's hidden behind a `TokenVerifier` Protocol, so tests inject a fake and the verifier can be swapped without touching routers.
- `get_principal` (in `deps.py`) extracts the bearer, verifies it, and maps claims → `Principal`. Unknown role strings are ignored rather than rejected.

---

## 3. Authorization

Two layers — RBAC at the edge, ABAC at the data layer.

**RBAC.** Roles `viewer < responder < approver`, plus `admin`. `require_roles(...)` is a dependency on each route; `admin` bypasses role checks. Wrong role → `403`.

**ABAC / multi-tenancy.** A `Principal.scope` (user id, team ids, service ids, is_admin) is pushed into Postgres via `set_config('app.user_id'/'app.team_scope'/'app.is_admin', ..., is_local => true)` at the start of every transaction (`db/engine.apply_scope`). The **RLS policies** (Phase 2 DDL) read these GUCs to filter rows. Repositories *also* apply explicit `team_id` filters as defense-in-depth, so isolation holds even if RLS isn't enabled in a given environment.

**No existence leak.** Reads for an out-of-scope investigation return `404`, not `403` — a user can't probe which ids exist outside their teams. Create is rejected (`400`) if the target team isn't in the caller's scope.

---

## 4. Database Integration

Async SQLAlchemy 2.0 (`db/`).
- `Database` (`engine.py`) wraps `create_async_engine` + `async_sessionmaker` (asyncpg DSN, `pool_pre_ping`).
- ORM models (`models.py`) cover the API's entities — incidents, investigations, approvals, feedback, communications (draft-only), and the hash-chained `audit_log` — a subset of the Phase 2 DDL; each business table carries `team_id` for RLS.
- **Unit of work** (`repositories.py`): `SqlAlchemyUnitOfWork` opens one scoped transaction, applies the ABAC GUCs, and exposes the repositories **plus a session-bound audit sink**. So an action and its audit row commit atomically on the same hash chain; if either fails, both roll back.
- **Idempotency:** create accepts `Idempotency-Key`; a prior investigation with that key (in scope) is returned with `200` instead of creating a duplicate.
- **Pagination:** keyset cursor over `(created_at, id)` — opaque base64, stable under inserts.
- **Transaction discipline:** the create/approval endpoints commit the DB write **before** calling the orchestrator, so the workflow is never started/resumed against an uncommitted record.

---

## 5. LangGraph Integration

The API never runs the graph inline — investigations pause for hours awaiting human approval. It depends on an `OrchestratorClient` Protocol (`services/orchestrator/client.py`):
- `start(...)` runs the compiled graph to the **human-review interrupt** (or to END for dropped/escalated) and returns a derived status.
- `get_status(...)` reads the checkpointed `StateSnapshot`.
- `resume_after_approval(...)` injects the recorded decision via `aupdate_state` and continues past the interrupt.

`graph/build.py` assembles the `StateGraph` from injected node callables: `intake → triage_gate → (parallel: knowledge[, architecture]) → human_review`, compiled with `interrupt_before=["human_review"]`. Intake and Knowledge are wired today; RCA/Recommendation/Communication slot in as they're built. `_after_triage` honors the Intake Agent's clamped triage (a serious incident can't be dropped).

**Production durability (R-1):** the same `OrchestratorClient` interface is fronted by **Temporal** in production, which owns durable timers/retries and drives the LangGraph graph as its unit of work. The in-process compiled graph + persistent checkpointer is the lighter path behind the identical seam.

---

## 6. Audit Logging

Append-only, tamper-evident hash chain (`libs/audit/sink.py` — the canonical home for the `AuditSink` Protocol the agents already use).
- Each row: `this_hash = sha256(prev_hash || canonical(entry))`, starting from a fixed genesis hash. Tampering with any row breaks every hash after it.
- `PostgresAuditSink` serializes appends with `pg_advisory_xact_lock` inside the transaction, reads the chain head, and inserts the next row — **bound to the same session/transaction as the audited action** (via the unit of work), so they're atomic.
- The `audit_log` table is **WORM** in production (no UPDATE/DELETE grants).
- API actions audited: `investigation.created`, `approval.recorded`, `feedback.submitted` (actor, investigation, request id, summary). A request-id middleware tags every response (`X-Request-ID`) and ties logs together.
- `InMemoryAuditSink` (with `verify_chain()`) backs tests/dev.

---

## Error model

All errors are `application/problem+json` (RFC 9457): `ApiError` subclasses map to `400/401/403/404/409/422/429/503`; request-validation failures return `422` with field details; any unhandled exception becomes a generic `500` (no internals leaked). `401` carries `WWW-Authenticate`.

---

## File manifest

| File | Role |
|---|---|
| `services/api/config.py` | `ApiSettings.from_env` (DB DSN, OIDC, CORS, paging, SSE). |
| `services/api/errors.py` | Problem model, `ApiError` hierarchy, problem+json handlers. |
| `services/api/auth.py` | `Role`, `Principal` (+scope), `VerifiedToken`, `TokenVerifier`, `OIDCTokenVerifier`, claim→principal mapping. |
| `services/api/schemas.py` | Request/response DTOs, `Page[T]`, cursor encode/decode. |
| `services/api/deps.py` | `AppDeps`, `get_principal` (authn), `require_roles` (RBAC), `require_orchestrator`. |
| `services/api/routers/system.py` | Health + `/v1/me`. |
| `services/api/routers/investigations.py` | Create/list/get/detail/SSE. |
| `services/api/routers/review.py` | Approvals (decision-only) + feedback. |
| `services/api/app.py` | `create_app(deps)` factory. |
| `services/api/main.py` | `uvicorn` entrypoint + `build_orchestrator` seam. |
| `db/models.py` | ORM models (incidents, investigations, approvals, feedback, communications, audit_log). |
| `db/engine.py` | Async engine/session + ABAC scope (RLS GUCs). |
| `db/repositories.py` | Repositories + `SqlAlchemyUnitOfWork` (atomic action+audit). |
| `libs/audit/sink.py` | `AuditSink` Protocol + hash chain + Postgres/in-memory sinks. |
| `services/orchestrator/client.py` | `OrchestratorClient` Protocol + LangGraph client. |
| `services/orchestrator/graph/build.py` | StateGraph builder (intake→triage→parallel→human_review interrupt). |
| `services/api/tests/test_api.py` | TestClient tests with fakes (authn/RBAC/ABAC, create+start+audit, idempotency, pagination, approval+resume, feedback, problem+json). |

## Composition seam / what's pluggable

`create_app(deps)` takes a fully-built `AppDeps`, so everything is injectable and testable. `main.build_app_from_env()` wires the pieces that exist today (DB, OIDC verifier, unit-of-work) and leaves the **orchestrator pluggable**: `build_orchestrator(...)` assembles the LangGraph workflow from agent instances, which require the **LLM client (libs/llm)**, the **MCP gateway**, and the **RAG retriever** — assembled by the broader composition root. Until those are wired, the API boots and serves health/identity/reads; investigation **start/resume** return `503` (`orchestrator_unavailable`) rather than failing opaquely.

`__init__.py` files are omitted from this drop per the Phase 3 convention; add them to make the packages importable at runtime.

*Highest-leverage next pieces: the **MCP gateway + policy layer** (registers the connectors; enforces the global read-only allowlist, ABAC, audit, rate-limit, circuit-breaking — and hosts the structural "no mutating tool ever registered" test), **libs/llm** (the tiered Claude client the agents depend on), or **Agent 3 (Architecture Discovery)** to extend the graph's parallel branch.*
