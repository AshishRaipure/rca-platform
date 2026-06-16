"""Shared dependency Protocols for the analytical agents (3-6).

Identical in shape to the per-agent interfaces declared by Agents 1-2; concrete implementations
live in libs/llm, mcp gateway, and libs/audit. Defining them here keeps the new agents
dependency-inverted and unit-testable with fakes.
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
    async def complete(
        self, *, system: str, user: str, model_tier: ModelTier, max_tokens: int,
        temperature: float, request_id: str, timeout_s: float,
    ) -> "LLMResponse": ...


class ToolResult(Protocol):
    tool_call_id: str
    ok: bool
    data: Any
    error: Optional[str]


class MCPGateway(Protocol):
    async def call(
        self, *, tool: str, params: dict[str, Any], scope: dict[str, Any],
        request_id: str, timeout_s: float,
    ) -> "ToolResult": ...


class AuditSink(Protocol):
    async def record(
        self, *, category: str, action: str, actor_id: str,
        investigation_id: Optional[UUID], request_id: str,
        model_id: Optional[str] = None, model_version: Optional[str] = None,
        tool_name: Optional[str] = None, tool_params: Optional[dict[str, Any]] = None,
        result_summary: Optional[str] = None, metadata: Optional[dict[str, Any]] = None,
    ) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
