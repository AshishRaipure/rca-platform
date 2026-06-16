"""Knowledge Retrieval Agent — retrieval helpers.

The agent's "tools" are read-only retrieval capabilities served by the internal RAG layer, plus
an OPTIONAL, default-off read-only MCP freshness check. There is no mutating capability here.
"""
from __future__ import annotations

from typing import Any, Final, Optional

from agents.knowledge_retrieval._interfaces import MCPGateway, RetrieverPort, ToolResult
from contracts.retrieval import EpisodicMatch, RetrievalFilters, RetrievedChunk

# Read-only MCP tools permitted ONLY for optional freshness verification of a top source.
ALLOWED_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "confluence.get_page",
        "servicenow.get_knowledge",
    }
)


class KnowledgeTools:
    def __init__(
        self,
        retriever: RetrieverPort,
        *,
        scope: dict[str, Any],
        request_id: str,
        retrieval_timeout_s: float,
        gateway: Optional[MCPGateway] = None,
        tool_timeout_s: float = 5.0,
    ) -> None:
        self._retriever = retriever
        self._scope = scope
        self._request_id = request_id
        self._retrieval_timeout_s = retrieval_timeout_s
        self._gateway = gateway
        self._tool_timeout_s = tool_timeout_s
        self.retrieval_calls = 0
        self.mcp_calls: list[str] = []

    async def search_corpus(
        self, *, query: str, filters: RetrievalFilters, k: int, expand: bool,
    ) -> list[RetrievedChunk]:
        self.retrieval_calls += 1
        return await self._retriever.search(
            query=query, filters=filters, k=k, expand=expand,
            request_id=self._request_id, timeout_s=self._retrieval_timeout_s,
        )

    async def search_episodic(self, *, query: str, k: int) -> list[EpisodicMatch]:
        self.retrieval_calls += 1
        return await self._retriever.search_episodic(
            query=query, scope=self._scope, k=k,
            request_id=self._request_id, timeout_s=self._retrieval_timeout_s,
        )

    async def verify_current(self, *, tool: str, params: dict[str, Any]) -> Optional[ToolResult]:
        """Optional read-only freshness probe. No-op unless a gateway is configured."""
        if self._gateway is None:
            return None
        if tool not in ALLOWED_TOOLS:
            raise ValueError(f"tool {tool!r} is not in the knowledge allowlist")
        self.mcp_calls.append(tool)
        return await self._gateway.call(
            tool=tool, params=params, scope=self._scope,
            request_id=self._request_id, timeout_s=self._tool_timeout_s,
        )
