"""Read-only ServiceNow REST client (Table API).

Read-only by construction: the only request primitive is ``_get`` — there is no
post/put/patch/delete method anywhere on this class, so a write cannot be issued. All public
methods are GETs against the Table API. Pagination, retries, and rate-limit handling are built in
and bounded by config.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Optional

from mcp_connectors.http import HttpResponse, HttpTransport
from mcp_connectors.servers.servicenow.config import ServiceNowConfig
from mcp_connectors.servers.servicenow.errors import (
    ServiceNowAuthError,
    ServiceNowError,
    ServiceNowNotFoundError,
    ServiceNowRateLimitError,
    ServiceNowResponseError,
    ServiceNowTransportError,
    ServiceNowUpstreamError,
)

logger = logging.getLogger("mcp.servicenow.client")


class ServiceNowClient:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        instance_url: str,
        authorization: str,
        config: Optional[ServiceNowConfig] = None,
    ) -> None:
        self._transport = transport
        self._base_url = instance_url.rstrip("/")
        self._authorization = authorization  # full Authorization header value (Bearer/Basic)
        self._config = config or ServiceNowConfig()

    # -------------------------------------------------------------- low-level GET

    def _headers(self) -> dict[str, str]:
        # the Authorization value is set verbatim and never logged
        return {
            "Authorization": self._authorization,
            "Accept": "application/json",
            "User-Agent": self._config.user_agent,
        }

    async def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
                   timeout_s: Optional[float] = None) -> Any:
        url = f"{self._base_url}{path}"
        timeout = timeout_s or self._config.request_timeout_s
        attempt = 0
        while True:
            try:
                resp: HttpResponse = await self._transport.get(
                    url, headers=self._headers(), params=params or {}, timeout=timeout,
                )
            except Exception as exc:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise ServiceNowTransportError(f"transport error: {exc}") from exc

            status = resp.status_code
            if 200 <= status < 300:
                return self._json(resp)
            if status in (401, 403):
                raise ServiceNowAuthError("authentication/authorization failed", status=status)
            if status == 404:
                raise ServiceNowNotFoundError("resource not found", status=status)
            if status == 429:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._retry_after(resp, attempt))
                    attempt += 1
                    continue
                raise ServiceNowRateLimitError("rate limited", status=status)
            if status >= 500:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise ServiceNowUpstreamError(f"upstream error {status}", status=status)
            raise ServiceNowError(f"unexpected status {status}", status=status)

    @staticmethod
    def _json(resp: HttpResponse) -> Any:
        try:
            return resp.json()
        except Exception as exc:
            raise ServiceNowResponseError(f"invalid JSON in response: {exc}") from exc

    def _backoff(self, attempt: int) -> float:
        return min(self._config.backoff_max_s, self._config.backoff_base_s * (2 ** attempt))

    def _retry_after(self, resp: HttpResponse, attempt: int) -> float:
        raw = resp.headers.get("Retry-After") if hasattr(resp.headers, "get") else None
        try:
            if raw is not None:
                return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
        return self._backoff(attempt)

    # ---------------------------------------------------------- generic table ops

    async def _get_record(self, table: str, sys_id: str, *, fields: Optional[list[str]] = None,
                          display_value: Optional[str] = None,
                          timeout_s: Optional[float] = None) -> Optional[dict]:
        params: dict[str, Any] = {
            "sysparm_display_value": display_value or self._config.default_display_value,
        }
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        body = await self._get(f"/api/now/table/{table}/{sys_id}", params=params, timeout_s=timeout_s)
        return (body or {}).get("result")

    async def _get_list(self, table: str, *, query: Optional[str] = None,
                        fields: Optional[list[str]] = None, limit: Optional[int] = None,
                        display_value: Optional[str] = None,
                        timeout_s: Optional[float] = None) -> list:
        cap = limit or self._config.max_items
        base: dict[str, Any] = {
            "sysparm_display_value": display_value or self._config.default_display_value,
        }
        if query:
            base["sysparm_query"] = query
        if fields:
            base["sysparm_fields"] = ",".join(fields)

        items: list = []
        offset = 0
        pages = 0
        while True:
            params = dict(base)
            params["sysparm_limit"] = self._config.page_limit
            params["sysparm_offset"] = offset
            body = await self._get(f"/api/now/table/{table}", params=params, timeout_s=timeout_s)
            batch = (body or {}).get("result") or []
            items.extend(batch)
            pages += 1
            if len(items) >= cap:
                return items[:cap]
            if len(batch) < self._config.page_limit:  # Table API: short page == last page
                return items
            if pages >= self._config.max_pages:
                return items
            offset += self._config.page_limit

    # ------------------------------------------------------------- read methods

    async def get_incident(self, *, sys_id: Optional[str] = None, number: Optional[str] = None,
                           timeout_s: Optional[float] = None) -> Optional[dict]:
        if sys_id:
            return await self._get_record("incident", sys_id, timeout_s=timeout_s)
        if number:
            rows = await self._get_list("incident", query=f"number={number}", limit=1,
                                        timeout_s=timeout_s)
            return rows[0] if rows else None
        return None

    async def list_incidents(self, *, query: Optional[str] = None, limit: Optional[int] = None,
                             timeout_s: Optional[float] = None) -> list:
        return await self._get_list("incident", query=query, limit=limit, timeout_s=timeout_s)

    async def get_incident_journal(self, element_id: str, *, limit: Optional[int] = None,
                                   timeout_s: Optional[float] = None) -> list:
        # work notes + comments live in sys_journal_field, keyed by the record's sys_id
        return await self._get_list(
            "sys_journal_field",
            query=f"element_id={element_id}^ORDERBYsys_created_on",
            fields=["element", "value", "sys_created_on", "sys_created_by"],
            display_value="true", limit=limit, timeout_s=timeout_s,
        )

    async def get_change_request(self, *, sys_id: Optional[str] = None,
                                 number: Optional[str] = None,
                                 timeout_s: Optional[float] = None) -> Optional[dict]:
        if sys_id:
            return await self._get_record("change_request", sys_id, timeout_s=timeout_s)
        if number:
            rows = await self._get_list("change_request", query=f"number={number}", limit=1,
                                        timeout_s=timeout_s)
            return rows[0] if rows else None
        return None

    async def list_change_requests(self, *, query: Optional[str] = None,
                                   limit: Optional[int] = None,
                                   timeout_s: Optional[float] = None) -> list:
        return await self._get_list("change_request", query=query, limit=limit, timeout_s=timeout_s)

    async def get_knowledge(self, *, sys_id: Optional[str] = None, number: Optional[str] = None,
                            timeout_s: Optional[float] = None) -> Optional[dict]:
        if sys_id:
            return await self._get_record("kb_knowledge", sys_id, display_value="true",
                                          timeout_s=timeout_s)
        if number:
            rows = await self._get_list("kb_knowledge", query=f"number={number}", limit=1,
                                        display_value="true", timeout_s=timeout_s)
            return rows[0] if rows else None
        return None

    async def search_knowledge(self, *, text: str, limit: Optional[int] = None,
                               timeout_s: Optional[float] = None) -> list:
        safe = text.replace("^", " ").replace("=", " ")
        query = (f"workflow_state=published^short_descriptionLIKE{safe}"
                 f"^ORtextLIKE{safe}")
        return await self._get_list("kb_knowledge", query=query, display_value="true",
                                    limit=limit, timeout_s=timeout_s)

    async def get_cmdb_ci(self, *, sys_id: Optional[str] = None, name: Optional[str] = None,
                          timeout_s: Optional[float] = None) -> Optional[dict]:
        if sys_id:
            return await self._get_record("cmdb_ci", sys_id, timeout_s=timeout_s)
        if name:
            rows = await self._get_list("cmdb_ci", query=f"name={name}", limit=1, timeout_s=timeout_s)
            return rows[0] if rows else None
        return None

    async def get_ci_relationships(self, ci_sys_id: str, *, limit: Optional[int] = None,
                                   timeout_s: Optional[float] = None) -> list:
        return await self._get_list(
            "cmdb_rel_ci", query=f"parent={ci_sys_id}^ORchild={ci_sys_id}",
            limit=limit, timeout_s=timeout_s,
        )

    async def get_user(self, sys_id: str, *, timeout_s: Optional[float] = None) -> Optional[dict]:
        return await self._get_record("sys_user", sys_id, timeout_s=timeout_s)
