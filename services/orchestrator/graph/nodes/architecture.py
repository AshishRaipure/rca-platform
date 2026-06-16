"""LangGraph node for the Architecture Discovery Agent (runs parallel to knowledge)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from contracts.models import InvestigationState

from agents.architecture_discovery.agent import ArchitectureDiscoveryAgent
from agents.architecture_discovery.schemas import ArchitectureInput

logger = logging.getLogger("orchestrator.graph.architecture")

ArchitectureNode = Callable[[InvestigationState], Awaitable[dict[str, Any]]]


def _degraded(detail: str) -> dict[str, Any]:
    return {
        "architecture_context": {
            "impacted": [], "dependencies": [], "recent_changes": [],
            "summary": "Architecture discovery did not run for this incident.",
            "topology_freshness": "unknown", "degraded": True, "warnings": [detail],
        },
        "architecture_metadata": {"degraded": True, "warnings": [detail]},
        "architecture_degraded": True,
        "errors": [{"node": "architecture", "type": "degraded", "detail": detail}],
    }


def make_architecture_node(agent: ArchitectureDiscoveryAgent) -> ArchitectureNode:
    async def architecture_node(state: InvestigationState) -> dict[str, Any]:
        request_id = str(state.get("investigation_id") or uuid4())
        scope: dict[str, Any] = {"investigation_id": request_id}
        try:
            affected = [
                a.get("name") for a in (state.get("affected_systems") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            request = ArchitectureInput(
                investigation_id=UUID(str(state["investigation_id"])), affected_systems=affected)
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("architecture node invalid state: %s", exc)
            return _degraded(f"invalid state: {exc}")
        try:
            ctx = await agent.run(request, request_id=request_id, scope=scope)
        except Exception as exc:
            logger.exception("architecture agent error")
            return _degraded(f"agent error: {exc}")
        p = ctx.model_dump(mode="json")
        return {
            "architecture_context": p,
            "architecture_metadata": {"degraded": p["degraded"], "warnings": p["warnings"]},
            "architecture_degraded": p["degraded"],
        }

    return architecture_node
