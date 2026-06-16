"""Read-only PagerDuty REST client (API v2).

Read-only by construction: the only request primitive is ``_get`` — there is no post/put/delete
method anywhere on this class, so a write cannot be issued even if calling code tried. All public
methods are GETs against PagerDuty's read endpoints. Pagination, retries, and rate-limit handling
are built in and bounded by config.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Optional

from mcp_connectors.servers.pagerduty.config import PagerDutyConfig
from mcp_connectors.servers.pagerduty.errors import (
    PagerDutyAuthError,
    PagerDutyError,
    PagerDutyNotFoundError,
    PagerDutyRateLimitError,
    PagerDutyResponseError,
    PagerDutyTransportError,
    PagerDutyUpstreamError,
)
from mcp_connectors.servers.pagerduty.http import HttpResponse, HttpTransport

logger = logging.getLogger("mcp.pagerduty.client")


class PagerDutyClient:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        base_url: str,
        api_token: str,
        api_version: str,
        config: Optional[PagerDutyConfig] = None,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._token = api_token
        self._api_version = api_version
        self._config = config or PagerDutyConfig()

    # -------------------------------------------------------------- low-level GET

    def _headers(self) -> dict[str, str]:
        # NOTE: the token is sent in the Authorization header and never logged.
        return {
            "Authorization": f"Token token={self._token}",
            "Accept": f"application/vnd.pagerduty+json;version={self._api_version}",
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
            except Exception as exc:  # transport/network
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise PagerDutyTransportError(f"transport error: {exc}") from exc

            status = resp.status_code
            if 200 <= status < 300:
                return self._json(resp)
            if status in (401, 403):
                raise PagerDutyAuthError("authentication/authorization failed", status=status)
            if status == 404:
                raise PagerDutyNotFoundError("resource not found", status=status)
            if status == 429:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._retry_after(resp, attempt))
                    attempt += 1
                    continue
                raise PagerDutyRateLimitError("rate limited", status=status)
            if status >= 500:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise PagerDutyUpstreamError(f"upstream error {status}", status=status)
            raise PagerDutyError(f"unexpected status {status}", status=status)

    @staticmethod
    def _json(resp: HttpResponse) -> Any:
        try:
            return resp.json()
        except Exception as exc:
            raise PagerDutyResponseError(f"invalid JSON in response: {exc}") from exc

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

    async def _get_list(self, path: str, *, key: str, params: Optional[dict[str, Any]] = None,
                        max_items: Optional[int] = None, timeout_s: Optional[float] = None) -> list:
        cap = max_items or self._config.max_items
        items: list = []
        offset = 0
        pages = 0
        while True:
            page_params = dict(params or {})
            page_params["limit"] = self._config.page_limit
            page_params["offset"] = offset
            body = await self._get(path, params=page_params, timeout_s=timeout_s)
            batch = (body or {}).get(key) or []
            items.extend(batch)
            pages += 1
            if len(items) >= cap:
                return items[:cap]
            if not (body or {}).get("more"):
                return items
            if pages >= self._config.max_pages:
                return items
            offset += self._config.page_limit

    # ------------------------------------------------------------- read methods

    async def get_incident(self, incident_id: str, *, timeout_s: Optional[float] = None) -> dict:
        body = await self._get(f"/incidents/{incident_id}", timeout_s=timeout_s)
        return (body or {}).get("incident") or body or {}

    async def list_incidents(
        self, *, statuses: Optional[list[str]] = None, since: Optional[str] = None,
        until: Optional[str] = None, service_ids: Optional[list[str]] = None,
        team_ids: Optional[list[str]] = None, urgencies: Optional[list[str]] = None,
        max_items: Optional[int] = None, timeout_s: Optional[float] = None,
    ) -> list:
        params: dict[str, Any] = {}
        if statuses:
            params["statuses[]"] = statuses
        if service_ids:
            params["service_ids[]"] = service_ids
        if team_ids:
            params["team_ids[]"] = team_ids
        if urgencies:
            params["urgencies[]"] = urgencies
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return await self._get_list("/incidents", key="incidents", params=params,
                                    max_items=max_items, timeout_s=timeout_s)

    async def list_alerts(self, incident_id: str, *, max_items: Optional[int] = None,
                          timeout_s: Optional[float] = None) -> list:
        return await self._get_list(f"/incidents/{incident_id}/alerts", key="alerts",
                                    max_items=max_items, timeout_s=timeout_s)

    async def list_log_entries(self, incident_id: str, *, max_items: Optional[int] = None,
                               timeout_s: Optional[float] = None) -> list:
        return await self._get_list(f"/incidents/{incident_id}/log_entries", key="log_entries",
                                    max_items=max_items, timeout_s=timeout_s)

    async def list_notes(self, incident_id: str, *, timeout_s: Optional[float] = None) -> list:
        body = await self._get(f"/incidents/{incident_id}/notes", timeout_s=timeout_s)
        return (body or {}).get("notes") or []

    async def get_service(self, service_id: str, *, timeout_s: Optional[float] = None) -> dict:
        body = await self._get(f"/services/{service_id}", timeout_s=timeout_s)
        return (body or {}).get("service") or body or {}

    async def list_services(
        self, *, query: Optional[str] = None, team_ids: Optional[list[str]] = None,
        max_items: Optional[int] = None, timeout_s: Optional[float] = None,
    ) -> list:
        params: dict[str, Any] = {}
        if query:
            params["query"] = query
        if team_ids:
            params["team_ids[]"] = team_ids
        return await self._get_list("/services", key="services", params=params,
                                    max_items=max_items, timeout_s=timeout_s)

    async def list_oncalls(
        self, *, escalation_policy_ids: Optional[list[str]] = None,
        schedule_ids: Optional[list[str]] = None, since: Optional[str] = None,
        until: Optional[str] = None, max_items: Optional[int] = None,
        timeout_s: Optional[float] = None,
    ) -> list:
        params: dict[str, Any] = {}
        if escalation_policy_ids:
            params["escalation_policy_ids[]"] = escalation_policy_ids
        if schedule_ids:
            params["schedule_ids[]"] = schedule_ids
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return await self._get_list("/oncalls", key="oncalls", params=params,
                                    max_items=max_items, timeout_s=timeout_s)

    async def get_user(self, user_id: str, *, timeout_s: Optional[float] = None) -> dict:
        body = await self._get(f"/users/{user_id}", timeout_s=timeout_s)
        return (body or {}).get("user") or body or {}
