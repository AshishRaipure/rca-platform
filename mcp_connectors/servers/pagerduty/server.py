"""PagerDuty MCP server.

The object the gateway routes to. It validates tool input, refuses any non-allowlisted or
mutating tool, dispatches to the read-only client, and returns a ``ToolResult``. The gateway
remains responsible for the global read-only policy, ABAC scope, audit, rate-limit, and circuit
breaking; this server adds defense-in-depth (its own read-only guard + structured errors).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional
from uuid import uuid4

from pydantic import ValidationError

from mcp_connectors.contracts import ToolContext, ToolResult, ToolSpec
from mcp_connectors.servers.pagerduty.client import PagerDutyClient
from mcp_connectors.servers.pagerduty.config import PagerDutyConfig
from mcp_connectors.servers.pagerduty.errors import PagerDutyError
from mcp_connectors.servers.pagerduty.http import HttpTransport, make_httpx_transport
from mcp_connectors.servers.pagerduty.tools import TOOL_SPECS

logger = logging.getLogger("mcp.pagerduty.server")


class PagerDutyMCPServer:
    server_name = "pagerduty"

    def __init__(
        self,
        client: PagerDutyClient,
        *,
        config: Optional[PagerDutyConfig] = None,
        tool_specs: Optional[list[ToolSpec]] = None,
    ) -> None:
        self._client = client
        self._config = config or PagerDutyConfig()
        specs = tool_specs or TOOL_SPECS
        # defense-in-depth: refuse to construct a server that holds any mutating tool
        if any(s.mutates for s in specs):
            raise ValueError("PagerDuty server cannot register a mutating tool")
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
        except PagerDutyError as exc:
            logger.info("pagerduty tool %s failed: %s (status=%s)", tool, exc, exc.status)
            return ToolResult(tool_call_id=call_id, ok=False, error=str(exc))
        except Exception as exc:  # never leak internals; never crash the gateway
            logger.exception("unexpected error in pagerduty tool %s", tool)
            return ToolResult(tool_call_id=call_id, ok=False, error="internal connector error")


def resolve_token(config: PagerDutyConfig) -> str:
    token = os.environ.get(config.api_token_env)
    if not token:
        raise RuntimeError(
            f"PagerDuty read-only token not found in env var {config.api_token_env!r}"
        )
    return token


def build_pagerduty_server(
    config: Optional[PagerDutyConfig] = None,
    *,
    transport: Optional[HttpTransport] = None,
    api_token: Optional[str] = None,
) -> PagerDutyMCPServer:
    """Composition root. Resolves the read-only token from env and wires the httpx transport
    unless explicitly injected (tests inject both)."""
    config = config or PagerDutyConfig()
    token = api_token if api_token is not None else resolve_token(config)
    transport = transport if transport is not None else make_httpx_transport()
    client = PagerDutyClient(
        transport, base_url=config.base_url, api_token=token,
        api_version=config.api_version, config=config,
    )
    return PagerDutyMCPServer(client, config=config)
