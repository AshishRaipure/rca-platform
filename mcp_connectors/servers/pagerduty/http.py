"""Backward-compatible re-export of the shared HTTP transport seam.

The transport seam now lives in ``mcp.http`` (shared across connectors). This module re-exports it
so existing imports (``from mcp_connectors.servers.pagerduty.http import ...``) keep working.
"""
from __future__ import annotations

from mcp_connectors.http import HttpResponse, HttpTransport, make_httpx_transport  # noqa: F401

__all__ = ["HttpResponse", "HttpTransport", "make_httpx_transport"]
