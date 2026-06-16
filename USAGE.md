# Using the platform

This explains how to run the API and drive an investigation. **What "using it" means:** you submit
an incident, the platform investigates and returns an advisory RCA + ranked recommendations + draft
communications, a human reviews and approves/rejects at the review gate, and a human then acts
through your normal change process. The platform never acts on production itself.

> **Current limitation (read first).** The investigation engine is wired but the orchestrator is not
> yet *constructed* at boot (it needs the MCP gateway, the RAG retriever, and Bedrock credentials —
> see ARCHITECTURE.md §12). Until then, endpoints that start or resume a workflow return
> **`503 orchestrator_unavailable`**: `POST /v1/investigations`, `.../stream`, and `.../approvals`.
> The rest — health, identity, and reads (`list` / `get`) — work once Postgres + OIDC are configured.

## 1. Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .

# configure environment (see .env.example for the full list); never commit real secrets
export $(grep -v '^#' .env | xargs)        # if you keep a local .env (it is gitignored)

# database schema + RLS + WORM trigger
psql "$DATABASE_DSN_PSQL" -f db/migrations/0001_initial.sql   # run under a non-superuser role

uvicorn services.api.main:app --port 8080
```

`DATABASE_DSN` is the async DSN the app uses (`postgresql+asyncpg://…`). For the `psql` migration
step use a normal libpq URL (`postgresql://…`).

## 2. Authentication & roles

The API verifies a **Bearer JWT** from your OIDC identity provider (RS256, validated against the
JWKS; issuer + audience checked). Send it on every request:

```
Authorization: Bearer <your-oidc-access-token>
```

Roles (from the token's roles claim) gate what you can do:

| Role | Can |
|---|---|
| `viewer` | list/get investigations, stream, submit feedback |
| `responder` | the above + **create** investigations |
| `approver` | the above + **record approval decisions** |
| `admin` | cross-team visibility (bypasses team scoping) |

Team membership (teams claim) scopes which investigations you can see/act on (enforced in the DB via
row-level security).

## 3. API surface

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/healthz` | none | liveness |
| GET | `/readyz` | none | readiness |
| GET | `/v1/me` | any | echo your identity, roles, teams |
| POST | `/v1/investigations` | responder, approver | create an incident + start an investigation |
| GET | `/v1/investigations` | viewer+ | list (cursor-paginated; `?status=&cursor=&limit=`) |
| GET | `/v1/investigations/{id}` | viewer+ | full detail (classification, findings, citations, …) |
| GET | `/v1/investigations/{id}/stream` | viewer+ | Server-Sent Events of status until `awaiting_approval`/terminal |
| POST | `/v1/investigations/{id}/approvals` | approver | record approve / reject / needs_changes |
| POST | `/v1/investigations/{id}/feedback` | viewer+ | rate the investigation |

Errors are RFC 9457 problem+json. `POST` create accepts an optional `Idempotency-Key` header (a
repeat key returns the existing investigation with `200`).

## 4. Investigation lifecycle

![Investigation workflow and lifecycle](docs/diagrams/investigation-workflow.svg)

Statuses: `running` -> `awaiting_approval` -> `completed` / `changes_requested` / `closed_rejected`; `escalated` (low-confidence RCA or intake failure) and `dropped` ("drop" triage) are the other terminal states.

The workflow durably pauses at `awaiting_approval`. The approval decision releases (or withholds)
the advisory output; any actual remediation is performed by a human outside the platform.

## 5. Examples

Create an investigation:

```bash
curl -sS -X POST http://localhost:8080/v1/investigations \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
        "title": "High CPU on prod-app-07",
        "source_system": "pagerduty",
        "description": "CPU pinned at 100% since 02:14 UTC after the order-api deploy",
        "provider_severity": "high",
        "team_id": "team-payments"
      }'
```

Read it back, then watch progress:

```bash
curl -sS http://localhost:8080/v1/investigations/$ID \
  -H "Authorization: Bearer $TOKEN"

curl -sS -N http://localhost:8080/v1/investigations/$ID/stream \
  -H "Authorization: Bearer $TOKEN"            # SSE; closes at awaiting_approval/terminal
```

Approve at the review gate (must be an `approver`):

```bash
curl -sS -X POST http://localhost:8080/v1/investigations/$ID/approvals \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"decision": "approve", "target": "review_gate", "comment": "RCA looks right; proceeding via CO"}'
```

`decision` is one of `approve | reject | needs_changes`. Send feedback any time:

```bash
curl -sS -X POST http://localhost:8080/v1/investigations/$ID/feedback \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"useful": true, "rating": 5, "comment": "correct root cause"}'
```

## 6. Interactive docs

With the API running, FastAPI serves OpenAPI at `/docs` (Swagger UI) and `/openapi.json` — the
quickest way to explore request/response schemas.
