"""Dependency interfaces (Protocols) for the Incident Intake Agent.

The agent depends on these abstractions, not on concrete infrastructure. In the real repo the
implementations live in `libs/llm`, `mcp/gateway`, `libs/audit`, and `db`/`rag` ports. Defining
them here keeps the agent dependency-inverted and unit-testable with fakes (see tests/).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol
from uuid import UUID

from contracts.enums import ModelTier


class LLMResponse(Protocol):
    text: str
    model_id: str
    model_version: Optional[str]
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMClient(Protocol):
    """Provided by `libs/llm` — wraps Bedrock (primary) / Anthropic API, tier routing,
    prompt caching, retries, tracing, and the no-training/ZDR configuration."""

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model_tier: ModelTier,
        max_tokens: int,
        temperature: float,
        request_id: str,
        timeout_s: float,
    ) -> "LLMResponse": ...


class ToolResult(Protocol):
    tool_call_id: str
    ok: bool
    data: Any
    error: Optional[str]


class MCPGateway(Protocol):
    """Provided by `mcp/gateway` — the single choke point for all tool calls. It independently
    enforces the global read-only allowlist, ABAC scope, audit, rate limiting, and circuit
    breaking. The agent can only ever *read*."""

    async def call(
        self,
        *,
        tool: str,
        params: dict[str, Any],
        scope: dict[str, Any],
        request_id: str,
        timeout_s: float,
    ) -> "ToolResult": ...


class AuditSink(Protocol):
    """Provided by `libs/audit` — append-only, tamper-evident audit (Phase 2 §1.10)."""

    async def record(
        self,
        *,
        category: str,
        action: str,
        actor_id: str,
        investigation_id: Optional[UUID],
        request_id: str,
        model_id: Optional[str] = None,
        model_version: Optional[str] = None,
        tool_name: Optional[str] = None,
        tool_params: Optional[dict[str, Any]] = None,
        result_summary: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None: ...


class ServiceCatalogPort(Protocol):
    """Read-only service-catalog / topology lookup, used for the no-invented-systems guardrail."""

    async def resolve(self, name: str, scope: dict[str, Any]) -> Optional[UUID]:
        """Return the service_id if ``name`` maps to a known service/topology node, else None."""
        ...


class Clock(Protocol):
    def now(self) -> datetime: ...
