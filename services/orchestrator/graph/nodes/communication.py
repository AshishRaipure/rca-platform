"""LangGraph node for the Communication Agent (drafts only)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from contracts.enums import SeverityLevel
from contracts.models import InvestigationState

from agents.communication.agent import CommunicationAgent
from agents.communication.schemas import CommunicationInput

logger = logging.getLogger("orchestrator.graph.communication")

CommunicationNode = Callable[[InvestigationState], Awaitable[dict[str, Any]]]


def _degraded(detail: str) -> dict[str, Any]:
    return {
        "communications": {
            "drafts": [], "rca_report": "", "status": "draft",
            "metadata": {"degraded": True, "warnings": [detail]},
        },
        "communication_metadata": {"degraded": True, "warnings": [detail]},
        "errors": [{"node": "communication", "type": "degraded", "detail": detail}],
    }


def make_communication_node(agent: CommunicationAgent) -> CommunicationNode:
    async def communication_node(state: InvestigationState) -> dict[str, Any]:
        request_id = str(state.get("investigation_id") or uuid4())
        scope: dict[str, Any] = {"investigation_id": request_id}
        try:
            classification = state.get("classification") or {}
            severity_raw = classification.get("suggested_severity") or (
                state["incident"].provider_severity.value
                if state["incident"].provider_severity else SeverityLevel.high.value)
            request = CommunicationInput(
                investigation_id=UUID(str(state["investigation_id"])),
                incident=state["incident"], severity=SeverityLevel(severity_raw),
                classification=classification,
                rca=state.get("rca") or {},
                recommendations=state.get("recommendations") or {})
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("communication node invalid state: %s", exc)
            return _degraded(f"invalid state: {exc}")
        try:
            out = await agent.run(request, request_id=request_id, scope=scope)
        except Exception as exc:
            logger.exception("communication agent error")
            return _degraded(f"agent error: {exc}")
        p = out.model_dump(mode="json")
        return {"communications": p, "communication_metadata": p["metadata"]}

    return communication_node
