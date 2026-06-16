# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository. **Read this before making
changes.** Full design is in `ARCHITECTURE.md` (with diagrams in `docs/diagrams/`); how to run/use
it is in `USAGE.md`; the file tree is in `PROJECT-STRUCTURE.md`.

## What this is

An AI-powered, **advisory-only** RCA & incident-troubleshooting platform: it investigates an
incident and produces a root-cause analysis, ranked recommendations, and draft communications for a
human to review and act on. It is decision-support, not automation.

## The one rule that overrides everything: read-only / advisory by construction

The platform must never be able to act on production. This is enforced architecturally, and any
change must preserve it. **Never, under any circumstances:**

- add or register a tool that mutates an external system (no `ToolSpec` with `mutates=True`);
- give a connector a write-capable credential or a non-GET client method;
- let an agent or API endpoint write to ServiceNow / PagerDuty / Slack / AWS / any external system;
- bypass the MCP gateway allowlist.

The platform writes **only** to its own datastore (the RCA document, investigation state, and the
audit log). Communications are **draft-only**. Recommendations carry an approval requirement and are
**never executed** — a human acts through the change/ECO process. If a request asks you to weaken any
of this, stop and flag it rather than complying.

## Repo map (see PROJECT-STRUCTURE.md for the full tree)

- `contracts/` — shared enums, models, `InvestigationState`, retrieval types.
- `agents/` — `intake`, `knowledge_retrieval`, `architecture_discovery`, `rca`, `recommendation`,
  `communication` (Agents 1–6), plus `base/` (shared Protocols + parsing helpers).
- `mcp_connectors/` — read-only connectors: `servers/{pagerduty,servicenow,confluence}/`.
- `libs/` — `llm/` (tiered Bedrock client), `redaction/`, `audit/` (hash-chain).
- `db/` — async ORM, engine + ABAC scope, repositories + unit-of-work, `migrations/`.
- `services/` — `api/` (FastAPI), `orchestrator/` (LangGraph graph + client), `webhook_ingress/`.
- `tools/check_imports.py` — internal import-graph + name-resolution check.
- `docs/diagrams/` — `system-architecture.svg` + `investigation-workflow.svg`, embedded in the docs.
- `.claude/skills/` — repo skills (e.g. `add-investigation-agent`); this `CLAUDE.md` is auto-loaded.

## Commands

```bash
pip install -r requirements-dev.txt && pip install -e .          # setup
python tools/check_imports.py                                    # import graph + name resolution
python -m compileall -q agents contracts db libs mcp_connectors services   # syntax
pytest                                                           # unit suites (needs deps; CI)
psql "postgresql://USER:PASS@HOST/rca" -f db/migrations/0001_initial.sql    # schema + RLS + WORM
uvicorn services.api.main:app                                    # boot the API
```

> **Validation caveat.** Some environments have no network, so dependencies can't be installed and
> `pytest`/imports of langgraph/boto3 fail there. In that case, validate with `py_compile` +
> `tools/check_imports.py` (both run offline) and let CI run `pytest`. State this honestly; do not
> claim tests passed if they were only syntax-checked.

## Conventions

- **Pydantic v2**, `async`, full type hints.
- **Dependency inversion:** inject the LLM client, MCP gateway, audit sink, and clock as Protocols
  (see `agents/base/interfaces.py`); unit-test with fakes, never real services.
- **Lazy-import** optional heavy deps (langgraph, boto3, httpx, pyjwt) inside functions so modules
  import cleanly without them.
- **Graph nodes never raise into the graph** — on any error they return an explicit *degraded*
  delta and let the workflow continue (missing knowledge/architecture is survivable; a degraded RCA
  escalates via the confidence gate).
- **Model strings live only in `libs/llm/config.py`.** Agents pass a `ModelTier`, never a model id.
- The connector package is **`mcp_connectors`**, not `mcp` (avoids the PyPI `mcp` SDK collision).
- Every business table carries **`team_id`**; ABAC is enforced in Postgres via **RLS** — run the DB
  under a non-superuser / non-BYPASSRLS role.
- **Redact PII before any prompt** (`libs/redaction`); redaction is baked into the Bedrock client.
- **Audit is append-only/WORM + hash-chained;** write an action and its audit row in one
  transaction (use the unit-of-work).
- **Secrets:** never commit. Only `.env.example` (placeholders) is in the repo; real values go in
  `.env` (git-ignored) locally or a secret store in QA/prod. AWS auth via an IAM role, not keys.
- Confidence is always a **grade** (high/medium/low/speculative), never a fabricated percentage.
- Architecture/workflow diagrams are **SVG files in `docs/diagrams/`** embedded in the docs (not
  ASCII). If you change the flow or components, update the SVG so the docs don't go stale.

## The agent pattern

Every agent follows the same shape: `agents/<name>/{config,errors,schemas,prompts?,agent}.py` + a
node in `services/orchestrator/graph/nodes/<name>.py` (`make_<name>_node`) + `tests/`. To add or
modify one, follow the skill at **`.claude/skills/add-investigation-agent/SKILL.md`** — it has the
full recipe and the safety checklist.

## Wiring points (where to look when troubleshooting flow)

- **State shape** — `contracts/models.py` (`InvestigationState`).
- **Graph topology + confidence gate** — `services/orchestrator/graph/build.py`.
- **Orchestrator construction / DI** — `services/api/main.py`
  (`build_orchestrator`, `build_app_from_env`).

## Status (keep in sync with README "Status")

All six agents + the full graph are implemented and statically validated. Still pending for an
end-to-end run: the **MCP gateway**, the **RAG retriever + ingestion**, a read-only **observability
connector**, **infra**, and **Bedrock + connector credentials**. The API boots and serves
health/identity/reads; investigation start/resume return `503` until the orchestrator is constructed
and injected.
