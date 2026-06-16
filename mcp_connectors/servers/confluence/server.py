"""Confluence MCP server.

The object the gateway routes to. It validates tool input, refuses any non-allowlisted or
mutating tool, dispatches to the read-only client, and returns a ``ToolResult``. The gateway owns
global read-only policy, ABAC scope, audit, rate-limit, and circuit breaking; this server adds
defense-in-depth (its own read-only guard + structured errors).
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any, Optional
from uuid import uuid4

from pydantic import ValidationError

from mcp_connectors.contracts import ToolContext, ToolResult, ToolSpec
from mcp_connectors.http import HttpTransport, make_httpx_transport
from mcp_connectors.servers.confluence.client import ConfluenceClient
from mcp_connectors.servers.confluence.config import ConfluenceConfig
from mcp_connectors.servers.confluence.errors import ConfluenceError
from mcp_connectors.servers.confluence.tools import TOOL_SPECS

logger = logging.getLogger("mcp.confluence.server")


class ConfluenceMCPServer:
    server_name = "confluence"

    def __init__(
        self,
        client: ConfluenceClient,
        *,
        config: Optional[ConfluenceConfig] = None,
        tool_specs: Optional[list[ToolSpec]] = None,
    ) -> None:
        self._client = client
        self._config = config or ConfluenceConfig()
        specs = tool_specs or TOOL_SPECS
        if any(s.mutates for s in specs):
            raise ValueError("Confluence server cannot register a mutating tool")
        self._tools: dict[str, ToolSpec] = {s.name: s for s in specs}

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.input_model.model_json_schema(),
                "read_only": not s.mutates,
                "read_scopes": list(s.read_scopes),
            }
            for s in self._tools.values()
        ]

    async def call_tool(
        self, *, tool: str, params: dict[str, Any], scope: Optional[dict[str, Any]] = None,
        request_id: str, timeout_s: Optional[float] = None,
    ) -> ToolResult:
        call_id = f"{request_id}:{tool}:{uuid4().hex[:8]}"
        spec = self._tools.get(tool)
        if spec is None:
            return ToolResult(tool_call_id=call_id, ok=False, error=f"unknown tool: {tool}")
        if spec.mutates:  # unreachable given construction guard, kept as a hard stop
            return ToolResult(tool_call_id=call_id, ok=False,
                              error="mutating tools are not permitted on this platform")

        try:
            args = spec.input_model.model_validate(params or {})
        except ValidationError as exc:
            return ToolResult(tool_call_id=call_id, ok=False, error=f"invalid params: {exc}")

        ctx = ToolContext(
            request_id=request_id, scope=scope or {},
            timeout_s=timeout_s or self._config.request_timeout_s,
        )
        try:
            data = await spec.handler(self._client, args, ctx)
            return ToolResult(tool_call_id=call_id, ok=True, data=data)
        except ConfluenceError as exc:
            logger.info("confluence tool %s failed: %s (status=%s)", tool, exc, exc.status)
            return ToolResult(tool_call_id=call_id, ok=False, error=str(exc))
        except Exception:
            logger.exception("unexpected error in confluence tool %s", tool)
            return ToolResult(tool_call_id=call_id, ok=False, error="internal connector error")


def resolve_base_url(config: ConfluenceConfig) -> str:
    url = config.base_url or os.environ.get(config.base_url_env, "")
    if not url:
        raise RuntimeError(
            f"Confluence base URL not set (config.base_url or env {config.base_url_env!r})"
        )
    return url


def resolve_authorization(config: ConfluenceConfig) -> str:
    """Build the Authorization header from read-only credentials in the environment."""
    token = os.environ.get(config.token_env)
    if not token:
        raise RuntimeError(f"Confluence token not found in env {config.token_env!r}")
    if config.auth_mode == "bearer":
        return f"Bearer {token}"
    # basic: Cloud uses account email as username and the API token as password
    user = os.environ.get(config.username_env)
    if not user:
        raise RuntimeError(f"Confluence username not found in env {config.username_env!r}")
    encoded = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def build_confluence_server(
    config: Optional[ConfluenceConfig] = None,
    *,
    transport: Optional[HttpTransport] = None,
    base_url: Optional[str] = None,
    authorization: Optional[str] = None,
) -> ConfluenceMCPServer:
    """Composition root. Resolves base URL + read-only credentials from env and wires the shared
    httpx transport unless explicitly injected (tests inject all three)."""
    config = config or ConfluenceConfig()
    url = base_url if base_url is not None else resolve_base_url(config)
    auth = authorization if authorization is not None else resolve_authorization(config)
    transport = transport if transport is not None else make_httpx_transport()
    client = ConfluenceClient(transport, base_url=url, authorization=auth, config=config)
    return ConfluenceMCPServer(client, config=config)
