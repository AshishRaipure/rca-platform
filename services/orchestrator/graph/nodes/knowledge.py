"""LangGraph node wrapper for the Knowledge Retrieval Agent (orchestrator service).

Runs in parallel with the Architecture Discovery node after the triage gate (Phase 2 §5.3). It
reads the Intake Agent's outputs from state, runs retrieval+synthesis, and returns a
JSON-serializable delta. It never raises into the graph; on failure it returns an empty,
explicitly-degraded knowledge result (missing knowledge is survivable — the RCA agent and the
confidence-gate handle low coverage).
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from contracts.enums import SeverityLevel
from contracts.models import InvestigationState

from agents.knowledge_retrieval.agent import KnowledgeRetrievalAgent
from agents.knowledge_retrieval.schemas import KnowledgeInput

logger = logging.getLogger("orchestrator.graph.knowledge")

KnowledgeNode = Callable[[InvestigationState], Awaitable[dict[str, Any]]]


def _degraded_delta(detail: str) -> dict[str, Any]:
    return {
        "knowledge_summary": "Knowledge retrieval did not run for this incident.",
        "knowledge_findings": [],
        "citations": [],
        "similar_incidents": [],
        "knowledge_conflicts": [],
        "knowledge_coverage": {
            "retrieval_confidence": "speculative", "num_sources": 0,
            "num_similar_incidents": 0, "has_runbook": False, "has_confirmed_rca": False,
            "stale_sources": 0, "gaps": ["knowledge retrieval unavailable"],
        },
        "knowledge_metadata": {"degraded": True, "warnings": [detail]},
        "knowledge_degraded": True,
        "errors": [{"node": "knowledge", "type": "degraded", "detail": detail}],
    }


def make_knowledge_node(agent: KnowledgeRetrievalAgent) -> KnowledgeNode:
    async def knowledge_node(state: InvestigationState) -> dict[str, Any]:
        request_id = str(state.get("investigation_id") or uuid4())
        scope: dict[str, Any] = {"investigation_id": request_id}

        # --- adapt state -> typed input (decoupled from the Intake schema; read primitives) ---
        try:
            classification = state.get("classification") or {}
            severity_raw = classification.get("suggested_severity") or (
                state["incident"].provider_severity.value
                if state["incident"].provider_severity else SeverityLevel.high.value
            )
            affected = [
                a.get("name") for a in (state.get("affected_systems") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            hyp = (state.get("initial_hypothesis") or {}).get("statement")
            request = KnowledgeInput(
                investigation_id=UUID(str(state["investigation_id"])),
                incident=state["incident"],
                severity=SeverityLevel(severity_raw),
                affected_systems=affected,
                initial_hypothesis=hyp,
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("knowledge node received invalid state: %s", exc)
            return _degraded_delta(f"invalid state: {exc}")

        try:
            output = await agent.run(request, request_id=request_id, scope=scope)
        except Exception as exc:  # never crash the graph; degrade
            logger.exception("knowledge agent error")
            return _degraded_delta(f"agent error: {exc}")

        payload = output.model_dump(mode="json")
        return {
            "knowledge_summary": payload["summary"],
            "knowledge_findings": payload["findings"],
            "citations": payload["citations"],
            "similar_incidents": payload["similar_incidents"],
            "knowledge_conflicts": payload["conflicts"],
            "knowledge_coverage": payload["coverage"],
            "knowledge_metadata": payload["metadata"],
            "knowledge_degraded": payload["metadata"]["degraded"],
        }

    return knowledge_node
