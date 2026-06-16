"""LangGraph node for the Recommendation Agent."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from contracts.enums import SeverityLevel
from contracts.models import InvestigationState

from agents.recommendation.agent import RecommendationAgent
from agents.recommendation.schemas import RecommendationInput

logger = logging.getLogger("orchestrator.graph.recommendation")

RecommendationNode = Callable[[InvestigationState], Awaitable[dict[str, Any]]]


def _degraded(detail: str) -> dict[str, Any]:
    return {
        "recommendations": {
            "summary": "Recommendations did not run for this incident.", "steps": [],
            "advisory_notice": "Advisory only; the platform executes nothing.",
            "metadata": {"degraded": True, "warnings": [detail]},
        },
        "recommendation_metadata": {"degraded": True, "warnings": [detail]},
        "recommendation_degraded": True,
        "errors": [{"node": "recommendation", "type": "degraded", "detail": detail}],
    }


def make_recommendation_node(agent: RecommendationAgent) -> RecommendationNode:
    async def recommendation_node(state: InvestigationState) -> dict[str, Any]:
        request_id = str(state.get("investigation_id") or uuid4())
        scope: dict[str, Any] = {"investigation_id": request_id}
        try:
            classification = state.get("classification") or {}
            severity_raw = classification.get("suggested_severity") or (
                state["incident"].provider_severity.value
                if state["incident"].provider_severity else SeverityLevel.high.value)
            request = RecommendationInput(
                investigation_id=UUID(str(state["investigation_id"])),
                severity=SeverityLevel(severity_raw),
                rca=state.get("rca") or {},
                architecture_context=state.get("architecture_context") or {},
                knowledge_findings=state.get("knowledge_findings") or [],
                citations=state.get("citations") or [])
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("recommendation node invalid state: %s", exc)
            return _degraded(f"invalid state: {exc}")
        try:
            out = await agent.run(request, request_id=request_id, scope=scope)
        except Exception as exc:
            logger.exception("recommendation agent error")
            return _degraded(f"agent error: {exc}")
        p = out.model_dump(mode="json")
        return {
            "recommendations": p,
            "recommendation_metadata": p["metadata"],
            "recommendation_degraded": bool(p["metadata"].get("degraded", False)),
        }

    return recommendation_node
