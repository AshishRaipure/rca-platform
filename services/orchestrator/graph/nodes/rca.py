"""LangGraph node for the RCA Agent (joins knowledge + architecture)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from contracts.enums import SeverityLevel
from contracts.models import InvestigationState

from agents.rca.agent import RootCauseAnalysisAgent
from agents.rca.schemas import RcaInput

logger = logging.getLogger("orchestrator.graph.rca")

RcaNode = Callable[[InvestigationState], Awaitable[dict[str, Any]]]


def _degraded(detail: str) -> dict[str, Any]:
    return {
        "rca": {
            "summary": "RCA did not run for this incident.",
            "ranked_causes": [{"statement": "Root cause undetermined.",
                               "confidence": "speculative", "evidence_refs": []}],
            "alternatives": [], "overall_confidence": "speculative",
            "metadata": {"degraded": True, "warnings": [detail]},
        },
        "rca_confidence": "speculative",
        "rca_metadata": {"degraded": True, "warnings": [detail]},
        "rca_degraded": True,
        "errors": [{"node": "rca", "type": "degraded", "detail": detail}],
    }


def make_rca_node(agent: RootCauseAnalysisAgent) -> RcaNode:
    async def rca_node(state: InvestigationState) -> dict[str, Any]:
        request_id = str(state.get("investigation_id") or uuid4())
        scope: dict[str, Any] = {"investigation_id": request_id}
        try:
            classification = state.get("classification") or {}
            severity_raw = classification.get("suggested_severity") or (
                state["incident"].provider_severity.value
                if state["incident"].provider_severity else SeverityLevel.high.value)
            affected = [
                a.get("name") for a in (state.get("affected_systems") or [])
                if isinstance(a, dict) and a.get("name")]
            request = RcaInput(
                investigation_id=UUID(str(state["investigation_id"])),
                incident=state["incident"], severity=SeverityLevel(severity_raw),
                classification=classification,
                initial_hypothesis=(state.get("initial_hypothesis") or {}).get("statement"),
                affected_systems=affected,
                knowledge_summary=state.get("knowledge_summary"),
                knowledge_findings=state.get("knowledge_findings") or [],
                citations=state.get("citations") or [],
                similar_incidents=state.get("similar_incidents") or [],
                architecture_context=state.get("architecture_context") or {},
                evidence=state.get("evidence") or [],
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("rca node invalid state: %s", exc)
            return _degraded(f"invalid state: {exc}")
        try:
            out = await agent.run(request, request_id=request_id, scope=scope)
        except Exception as exc:
            logger.exception("rca agent error")
            return _degraded(f"agent error: {exc}")
        p = out.model_dump(mode="json")
        return {
            "rca": p,
            "rca_confidence": p["overall_confidence"],
            "rca_metadata": p["metadata"],
            "rca_degraded": bool(p["metadata"].get("degraded", False)),
        }

    return rca_node
