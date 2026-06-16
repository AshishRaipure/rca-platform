"""Read-only Confluence REST client (API v1).

Read-only by construction: the only request primitive is ``_get`` — there is no
post/put/delete method anywhere on this class, so a write cannot be issued. All public methods are
GETs. Pagination, retries, and rate-limit handling are built in and bounded by config.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping, Optional

from mcp_connectors.http import HttpResponse, HttpTransport
from mcp_connectors.servers.confluence.config import ConfluenceConfig
from mcp_connectors.servers.confluence.errors import (
    ConfluenceAuthError,
    ConfluenceError,
    ConfluenceNotFoundError,
    ConfluenceRateLimitError,
    ConfluenceResponseError,
    ConfluenceTransportError,
    ConfluenceUpstreamError,
)

logger = logging.getLogger("mcp.confluence.client")


class ConfluenceClient:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        base_url: str,
        authorization: str,
        config: Optional[ConfluenceConfig] = None,
    ) -> None:
        self._transport = transport
        self.base_url = base_url.rstrip("/")
        self._authorization = authorization  # full Authorization header value (Basic/Bearer)
        self.config = config or ConfluenceConfig()
        self._api_base = self.config.api_base

    # -------------------------------------------------------------- low-level GET

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._authorization,  # set verbatim, never logged
            "Accept": "application/json",
            "User-Agent": self.config.user_agent,
        }

    async def _get(self, path: str, *, params: Optional[Mapping[str, Any]] = None,
                   timeout_s: Optional[float] = None) -> Any:
        url = f"{self.base_url}{path}"
        timeout = timeout_s or self.config.request_timeout_s
        attempt = 0
        while True:
            try:
                resp: HttpResponse = await self._transport.get(
                    url, headers=self._headers(), params=params or {}, timeout=timeout,
                )
            except Exception as exc:
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise ConfluenceTransportError(f"transport error: {exc}") from exc

            status = resp.status_code
            if 200 <= status < 300:
                return self._json(resp)
            if status in (401, 403):
                raise ConfluenceAuthError("authentication/authorization failed", status=status)
            if status == 404:
                raise ConfluenceNotFoundError("content not found", status=status)
            if status == 429:
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self._retry_after(resp, attempt))
                    attempt += 1
                    continue
                raise ConfluenceRateLimitError("rate limited", status=status)
            if status >= 500:
                if attempt < self.config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise ConfluenceUpstreamError(f"upstream error {status}", status=status)
            raise ConfluenceError(f"unexpected status {status}", status=status)

    @staticmethod
    def _json(resp: HttpResponse) -> Any:
        try:
            return resp.json()
        except Exception as exc:
            raise ConfluenceResponseError(f"invalid JSON in response: {exc}") from exc

    def _backoff(self, attempt: int) -> float:
        return min(self.config.backoff_max_s, self.config.backoff_base_s * (2 ** attempt))

    def _retry_after(self, resp: HttpResponse, attempt: int) -> float:
        raw = resp.headers.get("Retry-After") if hasattr(resp.headers, "get") else None
        try:
            if raw is not None:
                return max(0.0, float(raw))
        except (TypeError, ValueError):
            pass
        return self._backoff(attempt)

    async def _get_list(self, path: str, *, params: Optional[dict[str, Any]] = None,
                        max_items: Optional[int] = None, limit: Optional[int] = None,
                        timeout_s: Optional[float] = None) -> list:
        # Accept either `max_items` or `limit` as the caller-provided cap.
        cap = limit or max_items or self.config.max_items
        items: list = []
        start = 0
        pages = 0
        while True:
            p = dict(params or {})
            p["start"] = start
            p["limit"] = self.config.page_limit
            body = await self._get(path, params=p, timeout_s=timeout_s)
            results = (body or {}).get("results") or []
            items.extend(results)
            pages += 1
            if len(items) >= cap:
                return items[:cap]
            links = (body or {}).get("_links") or {}
            if not links.get("next"):  # Confluence signals more pages via _links.next
                return items
            if len(results) < self.config.page_limit:
                return items
            if pages >= self.config.max_pages:
                return items
            start += self.config.page_limit

    # ------------------------------------------------------------- read methods

    async def get_content_by_id(self, content_id: str, *, expand: Optional[str] = None,
                                timeout_s: Optional[float] = None) -> dict:
        params = {"expand": expand} if expand else {}
        return await self._get(f"{self._api_base}/content/{content_id}", params=params,
                               timeout_s=timeout_s)

    async def get_content_by_title(self, space_key: str, title: str, *, expand: Optional[str] = None,
                                   timeout_s: Optional[float] = None) -> Optional[dict]:
        rows = await self._get_list(
            f"{self._api_base}/content",
            params={"spaceKey": space_key, "title": title, "type": "page", "expand": expand or ""},
            limit=1, timeout_s=timeout_s,
        )
        return rows[0] if rows else None

    async def search(self, *, cql: str, expand: Optional[str] = None, limit: Optional[int] = None,
                     timeout_s: Optional[float] = None) -> list:
        return await self._get_list(
            f"{self._api_base}/content/search",
            params={"cql": cql, "expand": expand or ""}, limit=limit, timeout_s=timeout_s,
        )

    async def list_pages(self, space_key: str, *, expand: Optional[str] = None,
                         limit: Optional[int] = None, timeout_s: Optional[float] = None) -> list:
        return await self._get_list(
            f"{self._api_base}/content",
            params={"spaceKey": space_key, "type": "page", "expand": expand or ""},
            limit=limit, timeout_s=timeout_s,
        )

    async def get_child_pages(self, page_id: str, *, expand: Optional[str] = None,
                              limit: Optional[int] = None, timeout_s: Optional[float] = None) -> list:
        return await self._get_list(
            f"{self._api_base}/content/{page_id}/child/page",
            params={"expand": expand or ""}, limit=limit, timeout_s=timeout_s,
        )

    async def get_attachments(self, page_id: str, *, limit: Optional[int] = None,
                              timeout_s: Optional[float] = None) -> list:
        return await self._get_list(
            f"{self._api_base}/content/{page_id}/child/attachment",
            params={"expand": "version"}, limit=limit, timeout_s=timeout_s,
        )

    async def get_space(self, space_key: str, *, timeout_s: Optional[float] = None) -> dict:
        return await self._get(f"{self._api_base}/space/{space_key}",
                               params={"expand": "description.plain"}, timeout_s=timeout_s)
