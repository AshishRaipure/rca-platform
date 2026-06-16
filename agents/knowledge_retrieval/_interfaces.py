"""Dependency interfaces (Protocols) for the Knowledge Retrieval Agent.

As with Agent 1, the agent depends on abstractions. In the repo proper, LLMClient/AuditSink/Clock
come from `libs/`, RetrieverPort from `rag/retrieval`, and MCPGateway from `mcp/gateway`. They are
re-declared here so the agent is independently reviewable and unit-testable with fakes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol
from uuid import UUID

from contracts.enums import ModelTier
from contracts.retrieval import EpisodicMatch, RetrievalFilters, RetrievedChunk


class LLMResponse(Protocol):
    text: str
    model_id: str
    model_version: Optional[str]
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMClient(Protocol):
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


class RetrieverPort(Protocol):
    """Provided by `rag/retrieval` — hybrid (BM25+dense) retrieval + RRF fusion + reranking over
    the in-VPC vector store, with ABAC scope and freshness pre-filters applied inside. Read-only."""

    async def search(
        self,
        *,
        query: str,
        filters: RetrievalFilters,
        k: int,
        expand: bool,
        request_id: str,
        timeout_s: float,
    ) -> list[RetrievedChunk]: ...

    async def search_episodic(
        self,
        *,
        query: str,
        scope: dict[str, Any],
        k: int,
        request_id: str,
        timeout_s: float,
    ) -> list[EpisodicMatch]: ...


class ToolResult(Protocol):
    tool_call_id: str
    ok: bool
    data: Any
    error: Optional[str]


class MCPGateway(Protocol):
    """Optional. Used only for default-off, read-only freshness verification of top sources."""

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


class Clock(Protocol):
    def now(self) -> datetime: ...
