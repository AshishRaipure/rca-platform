# ServiceNow MCP Integration
## Implementation Guide

**Status:** Implemented (Phase 4) · **Server:** `mcp/servers/servicenow/` · **Shared transport:** `mcp/http.py`

This is the ServiceNow connector — the read-only tool server the gateway routes to. It services Agent 1's `servicenow.get_incident` and Agent 2's `servicenow.get_knowledge`, and adds the ITSM context the RCA pipeline needs most: **change requests** ("what changed?"), the incident **journal** (work notes/comments timeline), and **CMDB** configuration items and their relationships (topology for Agent 3 and the RCA agent).

Two scope notes:
- **Read-only.** ServiceNow is the system of record for incidents and changes. Closing an incident, creating/approving a change request, or editing a ticket are production actions the platform must never take. This connector reads only.
- **No inbound webhook.** The platform's trigger is PagerDuty (monitoring → PagerDuty → ServiceNow incident). ServiceNow is *queried* for the correlated incident, so there is no ServiceNow webhook here — unlike PagerDuty, which has the inbound trigger companion.

> **Validation status:** all 41 Python files in the codebase pass `py_compile` on Python 3.12 and the cross-module import check. The ServiceNow tests are included and syntax-checked, **but not executed here** (pydantic v2 / pytest / pytest-asyncio aren't installed and the network is disabled; httpx is imported lazily). Run `pytest -q` where those deps exist.

---

## 1. Read-only safety model

Identical four-layer guarantee to the PagerDuty connector:

1. **Credential layer.** The service account / OAuth token named in config must hold **read-only roles** in ServiceNow. The credential itself can't write.
2. **Client layer.** `ServiceNowClient` has exactly one request primitive — `_get`. No `post`/`put`/`patch`/`delete` exists on the class; a write cannot be expressed.
3. **Registry layer.** Every `TOOL_SPECS` entry is `mutates=False`; `tools.py` ends with an `assert all(not s.mutates ...)` that fails *import* if a mutating tool is added; the server constructor and `call_tool` independently reject `mutates=True`.
4. **Gateway layer (upstream).** The MCP gateway enforces the global read-only allowlist before the call reaches the server.

---

## 2. Tools exposed

All read-only. Names match what the agents call.

| Tool | Input | Returns |
|---|---|---|
| `servicenow.get_incident` | `sys_id` **or** `number` | one incident projection |
| `servicenow.list_incidents` | state, priority, assignment_group, cmdb_ci, opened_after, raw query, max_items | incidents + count |
| `servicenow.get_incident_journal` | `sys_id`, max_items | work notes + comments timeline |
| `servicenow.get_change_request` | `sys_id` **or** `number` | one change request |
| `servicenow.list_change_requests` | cmdb_ci, state, closed_after, raw query, max_items | change requests + count |
| `servicenow.get_knowledge` | `id`/`sys_id` **or** `number` | one KB article |
| `servicenow.search_knowledge` | `query`, max_items | KB articles + count |
| `servicenow.get_cmdb_ci` | `sys_id` **or** `name` | one configuration item |
| `servicenow.get_ci_relationships` | `ci_sys_id`, max_items | upstream/downstream CMDB relationships |
| `servicenow.get_user` | `sys_id` | one user projection |

`get_knowledge` accepts `id` specifically because Agent 2's freshness probe calls it with `{"id": <sys_id>}`; the handler treats `id` as the sys_id.

---

## 3. Client design

`ServiceNowClient` (`client.py`) uses the ServiceNow **Table API** (`/api/now/table/{table}`).

- **Auth.** The full `Authorization` header value is injected at construction (the server's factory builds it). Two modes: **OAuth bearer** (`Bearer <token>`) or **basic** (`Basic <base64(user:pass)>`). The client never reads env or logs the credential.
- **Transport seam.** Depends on the shared `HttpTransport` Protocol in `mcp/http.py` (now used by both connectors; PagerDuty's `http.py` re-exports it). httpx is imported lazily, so the module loads and tests run without it.
- **Display values.** Generic `_get_record`/`_get_list` request `sysparm_display_value=all` by default, so every field returns both a human label and the raw value/sys_id. Journal and KB reads use `true` (text). `sn_value`/`sn_display` normalize both forms.
- **Pagination.** Walks `sysparm_limit`/`sysparm_offset`; since the Table API has no "more" flag, a short page (fewer rows than the limit) signals the last page. Bounded by `max_items` and `max_pages`.
- **Retries.** Transport errors and 5xx retry with capped backoff; 429 honors `Retry-After`; 401/403 and 404 surface immediately without retry.

---

## 4. Schemas & display-value handling

`schemas.py`:
- **`sn_value(rec, field)` / `sn_display(rec, field)`** — the heart of ServiceNow handling. A field may be a scalar (`display_value=false`) or a `{display_value, value, link}` object (`=all`). These helpers return the raw value and the label respectively, for either shape.
- **Tool inputs** (`extra="forbid"`). Identifier-flexible inputs subclass `_RequiresIdentifier`, whose `model_validator` requires at least one of `sys_id`/`number`/`name`/`id`. Empty params are rejected before any call.
- **Projections** built via `from_record`, keeping the platform-relevant fields and exposing both labels and sys_ids for references (e.g. `cmdb_ci` label + `cmdb_ci_id`), since downstream agents need the label for reasoning and the sys_id for follow-up lookups.

---

## 5. Normalization

`normalize.py` maps a ServiceNow incident record to `NormalizedIncident`:
- `source_system = servicenow`, `servicenow_id = sys_id`, `fingerprint = number or sys_id`, title/description from the short description/description, `created_at` parsed from `opened_at`/`sys_created_on` (ServiceNow's space-separated datetime), `raw_payload` retained.
- **Severity mapping** (`map_severity`): incident **priority** first (1→critical, 2→high, 3→medium, 4→low, 5→info), then the `severity` field (1→high, 2→medium, 3→low), then urgency. Returns `None` when undeterminable — safe, because the Intake Agent treats provider severity as a floor and never under-rates.
- The internal investigation UUID is minted by ingestion, not derived from ServiceNow ids; the MCP read tools return ServiceNow projections (no fabricated UUID).

---

## 6. Gateway / agent-call integration

- **Registration.** `build_servicenow_server(config)` resolves the instance URL and read-only credentials from env and returns a `ServiceNowMCPServer`; the registry registers its `list_tools()` under the `servicenow.*` namespace.
- **Agent 1 (Intake).** `servicenow.get_incident {"sys_id": ...}` → gateway → server → read-only client GET → incident projection. Works unchanged with Agent 1's existing `IntakeTools.servicenow_incident`.
- **Agent 2 (Knowledge).** Its optional freshness probe calls `servicenow.get_knowledge {"id": ...}` → handled by the `id`-accepting input model.
- **Result shape.** `ToolResult` is structurally compatible with the Protocol the agents declare, so `r.ok` / `r.data` usage is unchanged.
- **Division of duties.** Gateway owns global policy, scope, audit, rate limiting; the server adds defense-in-depth and passes `scope` through `ToolContext` for scope-aware filtering.

---

## 7. Error handling

| Failure | Mapped to | Behavior |
|---|---|---|
| Network/transport error | `ServiceNowTransportError` | Retried with backoff; then failed `ToolResult`. |
| 429 rate limited | `ServiceNowRateLimitError` | Honors `Retry-After`, retries; then failed `ToolResult`. |
| 5xx upstream | `ServiceNowUpstreamError` | Retried; then failed `ToolResult`. |
| 401 / 403 | `ServiceNowAuthError` | **Not retried**; failed `ToolResult`. |
| 404 / empty result for an id | `ServiceNowNotFoundError` | Failed `ToolResult` (`not found`). |
| Non-JSON 2xx body | `ServiceNowResponseError` | Failed `ToolResult`. |
| Unknown tool | — | Failed `ToolResult` (`unknown tool`). |
| Invalid / missing-identifier params | `ValidationError` | Failed `ToolResult` (`invalid params: ...`). |
| Unexpected exception | — | Logged; generic failed `ToolResult` (no internals leaked). |

`call_tool` never raises into the gateway.

---

## 8. Security controls

| Control | Mechanism | Where |
|---|---|---|
| **Read-only (4 layers)** | read-only token/roles · GET-only client · read-only registry + import assertion · gateway allowlist | credential, `client.py`, `tools.py`/`server.py`, gateway |
| **Secret hygiene** | instance URL + credentials referenced by env-var name; Authorization assembled at composition; sent only in the header; never logged | `config.py`, `server.py`, `client.py` |
| **Strict input validation** | `extra="forbid"` inputs; identifier-required validator; rejected before any call | `schemas.py`, `server.py` |
| **Query-injection containment** | `search_knowledge` strips `^`/`=` from user text before building the encoded query | `client.py` |
| **Bounded resource use** | per-request timeout, capped retries/backoff, page size, `max_items`, `max_pages` | `config.py`, `client.py` |
| **No internals leaked** | upstream errors mapped to clean messages; unexpected errors return a generic string | `server.py` |
| **Scope passthrough** | `ToolContext.scope` carried for ABAC-aware filtering; gateway enforces scope | `server.py` |

---

## File manifest

| File | Role |
|---|---|
| `mcp/http.py` | *(new, shared)* HTTP transport seam for all connectors; lazy httpx adapter. |
| `mcp/servers/pagerduty/http.py` | *(refactored)* re-exports `mcp.http` for backward compatibility. |
| `mcp/servers/servicenow/config.py` | `ServiceNowConfig` — instance URL, auth mode, timeouts, retry, pagination caps. |
| `mcp/servers/servicenow/errors.py` | Error hierarchy with retryable flags + HTTP status. |
| `mcp/servers/servicenow/client.py` | Read-only (GET-only) Table API client: auth, display values, pagination, retries. |
| `mcp/servers/servicenow/schemas.py` | `sn_value`/`sn_display`, tool input models, projections. |
| `mcp/servers/servicenow/normalize.py` | ServiceNow → `NormalizedIncident` + priority severity mapping. |
| `mcp/servers/servicenow/tools.py` | Read-only tool registry + handlers + import-time assertion. |
| `mcp/servers/servicenow/server.py` | `ServiceNowMCPServer` + build factory + bearer/basic auth resolution. |
| `mcp/servers/servicenow/tests/test_servicenow_server.py` | Unit tests (fake transport; read-only, dispatch, pagination, errors, normalize). |

## Config & secrets / integration notes

- Set `SERVICENOW_INSTANCE_URL` and, for OAuth, `SERVICENOW_READONLY_TOKEN` — or for basic auth, `SERVICENOW_READONLY_USERNAME` / `SERVICENOW_READONLY_PASSWORD`. Credentials must hold read-only roles. They are referenced by name only.
- The connector exposes **no** create/update/close/resolve tools, and never will, by the construction guards above.
- The local package is `mcp`, matching Phase 3; in the repo it must be import-isolated from the official `mcp` SDK (src-layout or rename).
- `__init__.py` files are omitted from this drop for brevity; add per the Phase 3 package convention.

*With PagerDuty and ServiceNow done, the two systems Agent 1 reads are both covered. Natural next steps: the **MCP gateway + policy layer** that registers these servers and enforces the global read-only allowlist / ABAC / audit, or **Agent 3 (Architecture Discovery)**, which would consume `get_cmdb_ci` / `get_ci_relationships`.*
