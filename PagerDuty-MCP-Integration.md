# PagerDuty MCP Integration
## Implementation Guide

**Status:** Implemented (Phase 4) · **Server:** `mcp/servers/pagerduty/` · **Ingress companion:** `services/webhook_ingress/providers/pagerduty.py`

This is the PagerDuty connector for the platform. It has two halves:

- **Outbound (the MCP server):** the read-only tool server the gateway routes to. It is what already services Agent 1's `pagerduty.get_incident` / `pagerduty.list_alerts` calls, and what later agents use for timelines, on-call, and service context.
- **Inbound (the ingress companion):** webhook signature verification + normalization — the event that *starts* an investigation. This is HTTP ingestion, **not** MCP, and lives in the webhook-ingress service.

> **Validation status:** all 33 Python files in the codebase pass `py_compile` on Python 3.12 and the cross-module import check. The PagerDuty tests are included and syntax-checked, **but not executed here** (pydantic v2 / pytest / pytest-asyncio aren't installed and the network is disabled; httpx is imported lazily so nothing requires it to load). Run `pytest -q` where those deps exist.

---

## 1. Read-only safety model (the point of this connector)

PagerDuty is the incident-management system. Acknowledging, resolving, reassigning, snoozing, or creating anything in it is a **production action with real consequences** (it changes on-call state and stops escalation), so the platform must never do it. Read-only here is guaranteed at four independent layers:

1. **Credential layer.** The token named by `api_token_env` must be a PagerDuty **read-only API key**. Even a hypothetical bug cannot write, because the credential itself can't.
2. **Client layer.** `PagerDutyClient` has exactly one request primitive — `_get`. There is no `post`/`put`/`delete`/`patch` method anywhere on the class. A write cannot be expressed.
3. **Registry layer.** Every entry in `TOOL_SPECS` has `mutates=False`, and `tools.py` ends with an `assert all(not s.mutates ...)` that fails *import* if a mutating tool is ever added. The server constructor independently refuses a spec list containing a mutating tool, and `call_tool` hard-stops on `mutates=True`.
4. **Gateway layer (upstream).** The MCP gateway enforces the global read-only allowlist before the call ever reaches this server.

A jailbroken model, a prompt-injected document, or a buggy agent therefore cannot change anything in PagerDuty through this connector — there is no code path that issues a write.

---

## 2. Tools exposed

All read-only. Names match what the agents call through the gateway.

| Tool | Input | Returns |
|---|---|---|
| `pagerduty.get_incident` | `incident_id` | one incident projection |
| `pagerduty.list_incidents` | statuses, since/until, service_ids, team_ids, urgencies, max_items | incidents + count |
| `pagerduty.list_alerts` | `incident_id`, max_items | alerts + count |
| `pagerduty.get_incident_log_entries` | `incident_id`, max_items | timeline entries + count |
| `pagerduty.get_incident_notes` | `incident_id`, max_items | responder notes + count |
| `pagerduty.get_service` | `service_id` | one service projection |
| `pagerduty.list_services` | query, team_ids, max_items | services + count |
| `pagerduty.get_oncalls` | escalation_policy_ids, schedule_ids, since/until, max_items | current on-calls + count |
| `pagerduty.get_user` | `user_id` | one user projection |

`list_tools()` returns each tool's name, description, JSON-schema for its input, and a `read_only: true` flag — so the registry/gateway can advertise capabilities and assert read-only at discovery time.

---

## 3. Client design

`PagerDutyClient` (`client.py`) talks to PagerDuty REST API v2 (`https://api.pagerduty.com`).

- **Auth & headers.** `Authorization: Token token=<key>` and `Accept: application/vnd.pagerduty+json;version=2`. The token is sent only in the header and **never logged**.
- **Transport seam.** The client depends on an `HttpTransport` Protocol (`http.py`), not on httpx directly, so it is unit-testable with a fake and httpx need not be importable to load the module. `make_httpx_transport()` imports httpx lazily.
- **Retries & rate limits.** `_get` retries transport errors and 5xx with exponential backoff (capped), honors `Retry-After` on 429, and surfaces `401/403` and `404` immediately without retrying. All bounds come from config.
- **Pagination.** `_get_list` walks PagerDuty's `limit`/`offset`/`more` pages, stopping at `more=false`, at `max_items`, or at `max_pages` — whichever comes first. This bounds cost and blast radius for `list_*` tools.

---

## 4. Schemas & projections

`schemas.py` defines two kinds of model:

- **Tool inputs** (`extra="forbid"`) — strict validation of the params the gateway/agent pass (e.g. `GetIncidentInput.incident_id` is required and non-empty). Unknown params are rejected.
- **Projections** (`extra="ignore"`) — `PDIncident`, `PDAlert`, `PDService`, `PDOnCall`, `PDLogEntry`, `PDUser`, `PDNote` trim PagerDuty's verbose payloads to the fields the platform uses, so tool results are small and stable instead of raw API dumps. PagerDuty "references" collapse to a shared `PDRef` (id/type/summary/html_url).

---

## 5. Normalization

`normalize.py` maps a PagerDuty incident (REST payload *or* webhook event data) to the platform `NormalizedIncident` contract:

- `source_system = pagerduty`, `pagerduty_id`, `pagerduty_dedup_key = incident_key`, title/description, `created_at` parsed from ISO-8601, and `raw_payload` retained.
- **Severity mapping** (`map_severity`): PagerDuty priority summary first (P1/SEV1/Critical → critical … P5 → info; org-configurable conventions), falling back to urgency (high → high, low → low), else `None`. The Intake Agent treats provider severity as a floor and never under-rates, so a `None` here is safe.
- The internal investigation `incident_id` (a UUID) is **minted by ingress**, not derived from PagerDuty's id — so `normalize_incident` is used on the inbound/ingress path, while the MCP read tools return PagerDuty projections (no fabricated UUID).

---

## 6. Gateway / policy integration

The connector is designed to sit behind the gateway, not to replace it:

- **Registration.** `build_pagerduty_server(config)` resolves the read-only token from env and returns a `PagerDutyMCPServer`; the registry registers its `list_tools()` under the `pagerduty.*` namespace.
- **Call flow.** Agent → `gateway.call(tool="pagerduty.get_incident", params, scope, request_id)` → gateway applies the global read-only allowlist + ABAC scope + audit + rate-limit/circuit-breaker → `server.call_tool(...)` → read-only client GET → `ToolResult`.
- **Division of duties.** The gateway owns global policy, scope, audit, and rate limiting. The server adds defense-in-depth (its own read-only guard, input validation, error mapping) and passes `scope` through in the `ToolContext` so scope-aware filtering (e.g. team-scoped `list_services`) can be applied.
- **Result shape.** `ToolResult` (`mcp/contracts.py`) is structurally compatible with the `ToolResult` Protocol the agents already declare — so Agent 1's `r.ok` / `r.data` usage works unchanged.

---

## 7. Inbound webhook (ingress companion)

`services/webhook_ingress/providers/pagerduty.py` handles PagerDuty V3 webhooks — the trigger that starts an investigation. (Importable package name uses an underscore; the deploy directory may be `webhook-ingress`.)

- **Signature verification.** `verify_signature(raw_body, header, secret)` computes HMAC-SHA256 over the raw body and compares (constant-time) against each `v1=` signature in `X-PagerDuty-Signature`, supporting multiple signatures during secret rotation.
- **Replay protection.** `is_fresh(payload, max_age_s)` rejects stale deliveries by `occurred_at`; durable de-duplication by event id belongs in the ingress store on top of this.
- **Normalization.** `to_normalized_incident(payload)` maps a verified `incident.triggered` event to a `NormalizedIncident`, minting a fresh internal UUID. Non-trigger events are recognized (`is_trigger_event`) and ignored.

This file depends on `contracts` and on the connector's `normalize` helper — it does **not** depend on the MCP server or gateway, since it's an ingestion concern.

---

## 8. Error handling

| Failure | Mapped to | Behavior |
|---|---|---|
| Network/transport error | `PagerDutyTransportError` | Retried with backoff; then a failed `ToolResult`. |
| 429 rate limited | `PagerDutyRateLimitError` | Honors `Retry-After`, retries; then failed `ToolResult`. |
| 5xx upstream | `PagerDutyUpstreamError` | Retried with backoff; then failed `ToolResult`. |
| 401 / 403 | `PagerDutyAuthError` | **Not retried**; failed `ToolResult`. |
| 404 | `PagerDutyNotFoundError` | Failed `ToolResult` (`not found`). |
| Non-JSON 2xx body | `PagerDutyResponseError` | Failed `ToolResult`. |
| Unknown tool | — | Failed `ToolResult` (`unknown tool`). |
| Invalid params | `ValidationError` | Failed `ToolResult` (`invalid params: ...`). |
| Unexpected exception | — | Logged; failed `ToolResult` with a generic message (no internals leaked). |

`call_tool` never raises into the gateway — every outcome is a `ToolResult` with `ok` + `error`.

---

## 9. Security controls

| Control | Mechanism | Where |
|---|---|---|
| **Read-only (4 layers)** | read-only token · GET-only client · read-only registry + import assertion · gateway allowlist | credential, `client.py`, `tools.py`/`server.py`, gateway |
| **No internals leaked** | upstream errors mapped to clean messages; unexpected errors return a generic string | `server.py` |
| **Secret hygiene** | token referenced by env-var name in config; resolved at composition; sent only in the auth header; never logged | `config.py`, `server.py`, `client.py` |
| **Bounded resource use** | per-request timeout, capped retries/backoff, page size, `max_items`, `max_pages` | `config.py`, `client.py` |
| **Strict input validation** | `extra="forbid"` tool-input models; unknown/invalid params rejected before any call | `schemas.py`, `server.py` |
| **Webhook authenticity** | HMAC-SHA256 verification, constant-time compare, rotation support | ingress companion |
| **Replay resistance** | freshness window on `occurred_at` + id de-dup at the store | ingress companion |
| **Scope passthrough** | `ToolContext.scope` carried for ABAC-aware filtering; gateway enforces scope | `server.py` |

---

## File manifest

| File | Role |
|---|---|
| `mcp/contracts.py` | Shared MCP types: `ToolResult`, `ToolContext`, read-only `ToolSpec`. |
| `mcp/servers/pagerduty/config.py` | `PagerDutyConfig` — endpoints, token env, timeouts, retry, pagination caps. |
| `mcp/servers/pagerduty/errors.py` | Error hierarchy with retryable flags + HTTP status. |
| `mcp/servers/pagerduty/http.py` | `HttpTransport`/`HttpResponse` Protocols + lazy httpx adapter. |
| `mcp/servers/pagerduty/client.py` | Read-only (GET-only) REST client: auth, retries, rate limits, pagination. |
| `mcp/servers/pagerduty/schemas.py` | Tool input models + compact result projections. |
| `mcp/servers/pagerduty/normalize.py` | PagerDuty → `NormalizedIncident` + severity mapping. |
| `mcp/servers/pagerduty/tools.py` | Read-only tool registry + handlers + import-time assertion. |
| `mcp/servers/pagerduty/server.py` | `PagerDutyMCPServer` (list/dispatch/guard) + build factory. |
| `services/webhook_ingress/providers/pagerduty.py` | Inbound webhook verify + normalize (ingress companion). |
| `mcp/servers/pagerduty/tests/test_pagerduty_server.py` | Unit tests (fake transport; read-only, dispatch, pagination, errors, webhook). |

## Config & secrets / integration notes

- Set `PAGERDUTY_READONLY_API_TOKEN` (a read-only key) and `PAGERDUTY_WEBHOOK_SECRET` in the runtime environment / secrets manager. They are referenced by name only.
- The local package is named `mcp` to match Phase 3; in the repo it must be import-isolated from the official `mcp` SDK (src-layout or rename) to avoid a top-level collision.
- `__init__.py` files are omitted from this drop for brevity; add per the Phase 3 package convention.
- The connector intentionally exposes **no** acknowledge/resolve/reassign tools, and never will, by the construction guards above.

*This rounds out the PagerDuty integration (both directions). Awaiting your direction on the next component — e.g., the ServiceNow MCP server, the MCP gateway/policy layer, or Agent 3 (Architecture Discovery).*
