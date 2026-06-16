"""Shared MCP server contracts.

Concrete types every MCP server (PagerDuty, ServiceNow, ...) produces and the gateway consumes.
``ToolResult`` is structurally compatible with the ToolResult Protocol the agents declare, so the
same object flows agent -> gateway -> server -> back without coupling the layers.

NOTE: this local package is named ``mcp`` to match the approved Phase 3 structure. In the repo it
must be import-isolated from the official ``mcp`` SDK (src-layout or a package rename) to avoid a
top-level name collision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel


class ToolResult(BaseModel):
    tool_call_id: str
    ok: bool
    data: Any = None
    error: Optional[str] = None


class ToolContext(BaseModel):
    """Per-call context passed from the gateway to a server (scope is the ABAC envelope)."""
    request_id: str
    scope: dict[str, Any] = {}
    timeout_s: float = 10.0


@dataclass(frozen=True)
class ToolSpec:
    """A single tool a server exposes.

    ``mutates`` is ALWAYS False on this platform. The server refuses to dispatch any spec whose
    ``mutates`` is True, and a module-level assertion rejects a registry that contains one — so a
    write tool cannot be registered even by mistake.
    """
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    mutates: bool = False
    read_scopes: tuple[str, ...] = field(default=())
