# RCA & Incident Troubleshooting Platform (PoC)

An AI-powered, **advisory-only** platform that investigates incidents and produces root-cause
analyses and recommendations. It is **read-only by construction**: it can investigate, correlate,
explain, recommend, and draft — but it can never close an incident/CO/ECO, execute a remediation,
or post anything externally. Its only writes are to its own RCA document, investigation state, and
audit log.

> **Docs:** system design → **[ARCHITECTURE.md](ARCHITECTURE.md)** · how to use the API →
> **[USAGE.md](USAGE.md)** · environment & secrets template → **[.env.example](.env.example)**.

> **Honesty note on validation:** all six agents and the full investigation graph are implemented
> and **statically validated** — every module compiles, the cross-module import graph resolves, and
> the shared stdlib helpers + redaction layer are executed. The unit suites are **run in CI, not in
> the authoring environment** (no network there, so dependencies can't be installed and `pytest`
> can't run). This is a PoC slice and is **not yet runnable end-to-end** (see "What's not done").

## Layout

Flat top-level packages:

| Package | Contents |
|---|---|
| `contracts/` | shared enums, models, `InvestigationState`, retrieval types. |
| `agents/` | `intake/`, `knowledge_retrieval/`, `architecture_discovery/`, `rca/`, `recommendation/`, `communication/` (Agents 1–6) + `base/` (shared Protocols + parsing). |
| `mcp_connectors/` | read-only MCP connectors: `servers/{pagerduty,servicenow,confluence}/`. |
| `libs/` | `llm/` (Bedrock tiered client), `redaction/`, `audit/` (hash-chain). |
| `db/` | ORM models, async engine + ABAC scope, repositories + unit-of-work, `migrations/`. |
| `services/` | `api/` (FastAPI), `orchestrator/` (LangGraph graph + client), `webhook_ingress/`. |
| `tools/` | `check_imports.py` — structural import-graph + name-resolution check. |

> The connector package is `mcp_connectors`, **renamed from `mcp`** so it does not collide with the
> official `mcp` SDK distribution on PyPI. The Phase 1–3 docs refer to it as `mcp/`.

## Prerequisites

- Python 3.11+
- PostgreSQL 13+ (for the API / persistence)
- For a full run: AWS Bedrock model access; read-only credentials for PagerDuty / ServiceNow /
  Confluence; a cross-account read-only AWS role.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .
```

## Validate & test (no infrastructure required)

```bash
python tools/check_imports.py                       # import graph + name resolution
python -m compileall -q agents contracts db libs mcp_connectors services
pytest                                              # unit suites (fakes; no DB/network)
```

CI runs exactly these (`.github/workflows/ci.yml`).

## Database (QA)

```bash
psql "postgresql://USER:PASS@HOST:5432/rca" -f db/migrations/0001_initial.sql
```

This creates the schema, the ABAC row-level-security policies (which read the `app.team_scope` /
`app.is_admin` GUCs the engine sets per request), and the WORM trigger on `audit_log`. Run the API
under a **non-superuser, non-BYPASSRLS** role so RLS is enforced. (The app itself connects with the
async DSN in `DATABASE_DSN`; the `psql` step above uses a normal libpq URL.)

## Configuration (environment)

These are the variables the code actually reads. Copy `.env.example` to `.env` and fill it in.

| Var | Purpose |
|---|---|
| `APP_ENV` | `development` or `production` |
| `DATABASE_DSN` | async DSN, `postgresql+asyncpg://user:pass@host/db` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` | JWT verification |
| `OIDC_ROLES_CLAIM`, `OIDC_TEAMS_CLAIM`, `OIDC_SERVICES_CLAIM` | claim → principal mapping (optional; defaults `roles`/`team_ids`/`service_ids`) |
| `AWS_REGION` | Bedrock region |
| `BEDROCK_MODEL_FAST` / `BEDROCK_MODEL_MID` / `BEDROCK_MODEL_TOP` | Bedrock model ids or inference-profile ARNs per tier |
| `BEDROCK_MODEL_VERSION_LABEL` | optional; recorded on responses for audit |
| `PAGERDUTY_READONLY_API_TOKEN` | read-only PagerDuty token |
| `SERVICENOW_INSTANCE_URL`, `SERVICENOW_READONLY_TOKEN` | read-only ServiceNow |
| `CONFLUENCE_BASE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_API_TOKEN` | read-only Confluence |
| `CORS_ORIGINS` | optional, comma-separated |

## Secrets & credentials (where API keys go, safely)

Credentials are **never** committed and never hardcoded. Use environment variables, sourced
differently per environment:

- **Local development** — `cp .env.example .env`, put real values in `.env`, and load it (e.g.
  `export $(grep -v '^#' .env | xargs)` or a dotenv loader). `.env` (and `.env.*`) are git-ignored;
  only `.env.example` (placeholders) is committed. Don't paste secrets into code, notebooks, or
  commit messages.
- **QA / production** — do **not** ship a `.env`. Inject secrets at runtime from a secret store:
  **AWS Secrets Manager** or **SSM Parameter Store**; on Kubernetes use **Secrets** (managed by
  External Secrets Operator / sealed-secrets), surfaced as env vars or mounted files.
- **AWS / Bedrock auth** — use an **IAM role** (IRSA on EKS, instance profile on EC2), not access
  keys. The app uses the default boto3 credential chain, so there are **no AWS access keys** in
  config.
- **Connector tokens** — must be **read-only**, least-privilege, and rotated regularly.

## Boot the API

```bash
uvicorn services.api.main:app
```

`/healthz`, `/readyz`, and `/v1/me` work once OIDC + DB are configured; OpenAPI is at `/docs`. See
**[USAGE.md](USAGE.md)** for the full API surface, the investigation lifecycle, and example
requests. **Investigation start/resume endpoints return `503` (`orchestrator_unavailable`)** until
the orchestrator is constructed and injected (see below) — the API boots and serves
health/identity/reads in the meantime, by design.

## Status

**Implemented & statically validated**
- All six agents — Intake, Knowledge Retrieval, Architecture Discovery, RCA, Recommendation,
  Communication — and the full investigation graph (parallel knowledge/architecture → RCA →
  confidence gate → recommendation → communication → human-review interrupt). `build_orchestrator`
  wires all six.
- Read-only connectors: PagerDuty, ServiceNow, Confluence (+ PagerDuty inbound webhook verify)
- `libs/llm` (Bedrock tiered client) with redaction baked in; `libs/redaction` (executed/verified)
- FastAPI backend: OIDC auth, RBAC + ABAC, async SQLAlchemy + unit-of-work, hash-chain audit
- Packaging, CI, and the initial DB migration

**What's not done (blocks a full end-to-end QA run)**
- **MCP gateway + policy layer** — the read-only enforcement choke point the agents call through.
- **RAG retriever + ingestion** — Agent 2 has no corpus to search yet.
- **Observability connector** (logs/metrics/traces) — RCA's evidence source.
- **Composition root** — `build_app_from_env` does not yet **construct** the orchestrator (it needs
  the gateway + retriever + Bedrock credentials to build the agents), so investigation endpoints
  return `503` for now. The graph and `build_orchestrator` are ready; they just need real
  dependencies injected.
- **Infra** — container image, Helm/Terraform, provisioned Postgres/Redis, Bedrock + connector
  credentials.
- **Execution evidence** — unit suites need a networked CI run to confirm green; no integration or
  end-to-end tests yet.

### Suggested sequence to a runnable end-to-end PoC
1. Run CI (install deps, `pytest`) → confirm the built code passes; boot the API and check
   `/healthz` + `/v1/me`.
2. Apply the migration; point the API at a QA Postgres (non-superuser role).
3. Build the **MCP gateway** + a minimal **RAG retriever** (pgvector) + small ingestion + a
   read-only **observability connector**; construct the agents and inject the orchestrator
   (`build_orchestrator` already wires all six). This turns the `503`s into a real, end-to-end RCA;
   the worked-example traces then run for real.
4. Infra: container + Helm/Terraform; Bedrock + connector credentials from a secret store.
