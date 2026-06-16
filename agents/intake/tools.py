"""Incident Intake Agent — read-only MCP tools.

`ALLOWED_TOOLS` is the agent-scoped subset of the global read-only allowlist. The MCP gateway
enforces read-only globally; this is defense-in-depth at the agent boundary. There is, by
construction, no mutating tool here (Phase 1 §0 / §6.4).
"""
from __future__ import annotations

from typing import Any, Final

from agents.intake._interfaces import MCPGateway, ToolResult

ALLOWED_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "pagerduty.get_incident",
        "pagerduty.list_alerts",
        "servicenow.get_incident",
    }
)


class IntakeTools:
    """Thin, read-only helpers. Every call is routed through the MCP gateway and recorded."""

    def __init__(
        self,
        gateway: MCPGateway,
        *,
        scope: dict[str, Any],
        request_id: str,
        timeout_s: float,
    ) -> None:
        self._gw = gateway
        self._scope = scope
        self._request_id = request_id
        self._timeout_s = timeout_s
        self.calls: list[str] = []

    async def _call(self, tool: str, params: dict[str, Any]) -> ToolResult:
        if tool not in ALLOWED_TOOLS:
            # Should be impossible from this module; guards against future drift.
            raise ValueError(f"tool {tool!r} is not in the intake allowlist")
        self.calls.append(tool)
        return await self._gw.call(
            tool=tool,
            params=params,
            scope=self._scope,
            request_id=self._request_id,
            timeout_s=self._timeout_s,
        )

    async def pagerduty_incident(self, pagerduty_id: str) -> ToolResult:
        return await self._call("pagerduty.get_incident", {"id": pagerduty_id})

    async def servicenow_incident(self, sys_id: str) -> ToolResult:
        return await self._call("servicenow.get_incident", {"sys_id": sys_id})
