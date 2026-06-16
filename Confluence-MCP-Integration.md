# Confluence MCP Integration
## Implementation Guide

**Status:** Implemented (Phase 4) · **Server:** `mcp/servers/confluence/` · **Shared:** `mcp/contracts.py`, `mcp/http.py`

This is the Confluence connector — the read-only tool server the gateway routes to. Confluence is the **primary source of the knowledge corpus**: runbooks, RCAs, and architecture docs that Agent 2 (Knowledge Retrieval) searches. So this connector serves two roles:

- **Ingestion source.** The ingestion pipeline enumerates spaces, walks page trees, and pulls page bodies + attachments to build the RAG corpus (offline).
- **Freshness probe.** Agent 2's optional, default-off freshness check calls `confluence.get_page {"id": ...}` at query time to confirm a cited page is current.

Read-only throughout: the platform never creates, edits, moves, or deletes wiki content. There is no inbound webhook (Confluence is read, not a trigger source).

> **Validation status:** all 48 Python files in the codebase pass `py_compile` on Python 3.12 and the cross-module import check. The Confluence tests are included and syntax-checked, **but not executed here** (pydantic v2 / pytest / pytest-asyncio aren't installed and the network is disabled; httpx is imported lazily). Run `pytest -q` where those deps exist.

---

## 1. Read-only safety model

Same four-layer guarantee as the other connectors:

1. **Credential layer.** The API token / account must hold **read-only** Confluence permissions.
2. **Client layer.** `ConfluenceClient` has exactly one request primitive — `_get`. No `post`/`put`/`delete`/`patch` exists; a write cannot be expressed.
3. **Registry layer.** Every `TOOL_SPECS` entry is `mutates=False`; `tools.py` ends with an `assert all(not s.mutates ...)` that fails *import* if a mutating tool is added; the server constructor and `call_tool` independently reject `mutates=True`.
4. **Gateway layer (upstream).** The MCP gateway enforces the global read-only allowlist before the call reaches the server.

---

## 2. Tools exposed

All read-only.

| Tool | Input | Returns |
|---|---|---|
| `confluence.get_page` | `id` (+ optional `body_format`) | page: title, space, version, last_modified, url, body + text excerpt, ancestors |
| `confluence.get_page_by_title` | `space_key`, `title` | one page projection |
| `confluence.search` | `cql` **or** `text`, max_items | matching pages + the CQL used |
| `confluence.list_pages` | `space_key`, max_items | pages in a space (ingestion enumeration) |
| `confluence.get_child_pages` | `page_id`, max_items | child pages (tree traversal) |
| `confluence.get_attachments` | `page_id`, max_items | attachment metadata + download URLs |
| `confluence.get_space` | `space_key` | space metadata |

`get_page` accepts `id` specifically because that's the param Agent 2's freshness probe sends, and it expands `version` so the returned `last_modified` / `version` support a real freshness comparison.

---

## 3. Client design

`ConfluenceClient` (`client.py`) uses the Confluence REST API v1 (`/rest/api`).

- **Auth.** The full `Authorization` header is injected at construction (the factory builds it). Two modes: **basic** — Cloud uses account email as username + API token as password, base64-encoded — or **bearer** (PAT/OAuth). The client never reads env or logs the credential.
- **Transport seam.** Depends on the shared `HttpTransport` Protocol in `mcp/http.py`; httpx is imported lazily.
- **Body formats.** `get_page`/`get_page_by_title` expand `body.{storage|view}` plus `version`, `space`, `history.lastUpdated`, and `ancestors`. `storage` (XHTML source) is the default for ingestion; `view` (rendered HTML) is available. List/search results use a lighter expansion (no body) to keep them small — fetch a page for the full body.
- **Pagination.** Walks `start`/`limit`; Confluence advertises more pages via `_links.next`, so a missing `next` (or a short page) ends the walk. Bounded by `max_items` and `max_pages`.
- **Retries.** Transport errors and 5xx retry with capped backoff; 429 honors `Retry-After`; 401/403 and 404 surface immediately.

---

## 4. Schemas & HTML handling

`schemas.py`:
- **`strip_html(value, max_chars)`** — best-effort XHTML/HTML → plain text (tag strip + entity unescape + whitespace collapse, truncated) for a readable `excerpt`. It is **not** a full Confluence-macro renderer; the raw `body` is retained for the ingestion pipeline, which can render macros properly.
- **Tool inputs** (`extra="forbid"`). `get_page` requires `id`; `search` has a `model_validator` requiring `cql` **or** `text`.
- **Projections** (`CFPage`, `CFAttachment`, `CFSpace`) keep the platform-relevant fields. `CFPage` derives the absolute `url` from `_links`, surfaces `version` + `last_modified` (for freshness), the text `excerpt`, the raw `body`, and the `ancestors` breadcrumb.

---

## 5. RAG / ingestion role

This connector is what the ingestion pipeline (Phase 2 §RAG) reads to populate Agent 2's corpus:
- **Enumerate** a space with `list_pages`, then **traverse** with `get_child_pages`.
- **Fetch bodies** with `get_page` (`body_format=storage`), strip/normalize, then chunk in the ingestion layer.
- **Capture freshness** from each page's `version` + `last_modified`, stored as document metadata — which is exactly what powers Agent 2's outcome/freshness guardrails (R-6) and the optional `confluence.get_page` re-check at query time.
- **Attachments** (`get_attachments`) expose download URLs for attached runbooks/diagrams the pipeline may ingest separately.

The connector returns clean projections; chunking, embedding, and store-writes remain the ingestion layer's job.

---

## 6. Gateway / agent-call integration

- **Registration.** `build_confluence_server(config)` resolves the base URL + read-only credentials from env and returns a `ConfluenceMCPServer`; the registry registers its `list_tools()` under the `confluence.*` namespace.
- **Agent 2 (Knowledge).** Its freshness probe calls `confluence.get_page {"id": ...}` → gateway → server → read-only GET → page projection with `version`/`last_modified`.
- **Result shape.** `ToolResult` is structurally compatible with the Protocol the agents declare.
- **Division of duties.** Gateway owns global policy, scope, audit, rate limiting; the server adds defense-in-depth and passes `scope` through `ToolContext`.

---

## 7. Error handling

| Failure | Mapped to | Behavior |
|---|---|---|
| Network/transport error | `ConfluenceTransportError` | Retried; then failed `ToolResult`. |
| 429 rate limited | `ConfluenceRateLimitError` | Honors `Retry-After`, retries; then failed `ToolResult`. |
| 5xx upstream | `ConfluenceUpstreamError` | Retried; then failed `ToolResult`. |
| 401 / 403 | `ConfluenceAuthError` | **Not retried**; failed `ToolResult`. |
| 404 / empty result | `ConfluenceNotFoundError` | Failed `ToolResult` (`not found`). |
| Non-JSON 2xx body | `ConfluenceResponseError` | Failed `ToolResult`. |
| Unknown tool | — | Failed `ToolResult` (`unknown tool`). |
| Invalid params (e.g. search without cql/text) | `ValidationError` | Failed `ToolResult` (`invalid params: ...`). |
| Unexpected exception | — | Logged; generic failed `ToolResult` (no internals leaked). |

`call_tool` never raises into the gateway.

---

## 8. Security controls

| Control | Mechanism | Where |
|---|---|---|
| **Read-only (4 layers)** | read-only token/perms · GET-only client · read-only registry + import assertion · gateway allowlist | credential, `client.py`, `tools.py`/`server.py`, gateway |
| **Secret hygiene** | base URL + credentials by env-var name; Authorization assembled at composition; sent only in the header; never logged | `config.py`, `server.py`, `client.py` |
| **Strict input validation** | `extra="forbid"` inputs; search requires cql/text; rejected before any call | `schemas.py`, `server.py` |
| **Untrusted-content awareness** | wiki content is untrusted; the connector returns it as data — downstream agents already fence retrieved content and re-validate citations (R-9) | consumers (Agent 2) |
| **Bounded resource use** | per-request timeout, capped retries/backoff, page size, `max_items`, `max_pages` | `config.py`, `client.py` |
| **No internals leaked** | upstream errors mapped to clean messages; unexpected errors return a generic string | `server.py` |
| **Scope passthrough** | `ToolContext.scope` carried for ABAC-aware filtering; gateway enforces scope | `server.py` |

---

## File manifest

| File | Role |
|---|---|
| `mcp/servers/confluence/config.py` | `ConfluenceConfig` — base URL, auth mode, body format, timeouts, retry, pagination caps. |
| `mcp/servers/confluence/errors.py` | Error hierarchy with retryable flags + HTTP status. |
| `mcp/servers/confluence/client.py` | Read-only (GET-only) REST client: auth, body expansion, pagination, retries. |
| `mcp/servers/confluence/schemas.py` | `strip_html`, tool input models, projections. |
| `mcp/servers/confluence/tools.py` | Read-only tool registry + handlers + import-time assertion. |
| `mcp/servers/confluence/server.py` | `ConfluenceMCPServer` + build factory + basic/bearer auth resolution. |
| `mcp/servers/confluence/tests/test_confluence_server.py` | Unit tests (fake transport; read-only, dispatch, HTML strip, CQL, pagination, errors). |

## Config & secrets / integration notes

- Set `CONFLUENCE_BASE_URL` (e.g. `https://acme.atlassian.net/wiki`) and `CONFLUENCE_API_TOKEN`; for basic auth also `CONFLUENCE_EMAIL`. Credentials must be read-only. Referenced by name only.
- This connector uses Confluence REST v1 for broad compatibility; a v2 client can be slotted behind the same `ConfluenceClient` surface later.
- The connector exposes **no** create/update/delete tools, and never will, by the construction guards above.
- `__init__.py` files are omitted from this drop for brevity; add per the Phase 3 package convention.

*PagerDuty, ServiceNow, and Confluence connectors are now done. The remaining read sources from Phase 1/2 (e.g. logs/metrics, code host) could follow, but the highest-leverage next pieces are the **MCP gateway + policy layer** (registers all servers; enforces the global read-only allowlist, ABAC, audit, rate-limit, circuit-breaking) or **Agent 3 (Architecture Discovery)**.*
