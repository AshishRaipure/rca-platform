"""Shared HTTP transport seam for all MCP connectors.

Connectors depend on the ``HttpTransport`` Protocol, not on a concrete HTTP library, so they are
unit-testable with a fake and httpx need not be importable to load them. The real adapter imports
httpx lazily, inside the factory.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any: ...


class HttpTransport(Protocol):
    async def get(
        self, url: str, *, headers: Mapping[str, str], params: Mapping[str, Any], timeout: float,
    ) -> "HttpResponse": ...


def make_httpx_transport(*, verify: bool = True) -> HttpTransport:
    """Build an httpx-backed transport. httpx is imported lazily so this file loads without it."""
    import httpx  # noqa: WPS433 (intentional lazy import)

    class _HttpxTransport:
        def __init__(self) -> None:
            self._client = httpx.AsyncClient(verify=verify)

        async def get(self, url, *, headers, params, timeout):
            return await self._client.get(
                url, headers=dict(headers), params=dict(params), timeout=timeout,
            )

        async def aclose(self) -> None:
            await self._client.aclose()

    return _HttpxTransport()
