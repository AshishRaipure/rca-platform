"""Orchestrator client — the API's seam to the durable workflow.

The API never runs the graph inline (investigations pause for hours awaiting human approval). It
calls this client to start, query, and resume a workflow. The LangGraph-backed implementation runs
the compiled graph against a checkpointer keyed by ``investigation_id`` and stops at the
human-review interrupt; ``resume_after_approval`` injects the decision and continues.

In production this same interface is fronted by Temporal (R-1), which owns durable timers/retries
and drives the graph as its unit of work.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel

from contracts.models import NormalizedIncident, TriageHint


class InvestigationStatus(BaseModel):
    investigation_id: UUID
    status: str  # running | awaiting_approval | completed | failed | escalated | dropped
    current: Optional[str] = None
    recommended_triage: Optional[str] = None
    severity: Optional[str] = None
    degraded: bool = False


class OrchestratorClient(Protocol):
    async def start(self, *, investigation_id: UUID, incident: NormalizedIncident,
                    triage_hint: Optional[TriageHint], scope: dict[str, Any]) -> InvestigationStatus: ...

    async def get_status(self, investigation_id: UUID,
                         scope: dict[str, Any]) -> InvestigationStatus: ...

    async def resume_after_approval(self, *, investigation_id: UUID, decision: str,
                                    target: str, target_id: Optional[str], decided_by: str,
                                    scope: dict[str, Any]) -> InvestigationStatus: ...


def _status_from_state(investigation_id: UUID, snapshot: Any) -> InvestigationStatus:
    """Derive an InvestigationStatus from a LangGraph StateSnapshot."""
    values = getattr(snapshot, "values", {}) or {}
    nexts = list(getattr(snapshot, "next", ()) or ())
    classification = values.get("classification") or {}
    if values.get("intake_failed"):
        status = "escalated"
    elif "human_review" in nexts:
        status = "awaiting_approval"
    elif not nexts:
        status = values.get("status") or "completed"
        if status == "triaged":
            status = "running"
    else:
        status = "running"
    return InvestigationStatus(
        investigation_id=investigation_id,
        status=status,
        current=nexts[0] if nexts else None,
        recommended_triage=values.get("recommended_triage"),
        severity=classification.get("suggested_severity") or values.get("severity"),
        degraded=bool(values.get("knowledge_degraded")),
    )


class LangGraphOrchestratorClient:
    def __init__(self, graph_app: Any) -> None:
        self._graph = graph_app  # a compiled StateGraph (see graph/build.py)

    @staticmethod
    def _config(investigation_id: UUID, scope: dict[str, Any]) -> dict[str, Any]:
        return {"configurable": {"thread_id": str(investigation_id), "scope": scope}}

    async def start(self, *, investigation_id, incident, triage_hint, scope) -> InvestigationStatus:
        initial: dict[str, Any] = {
            "investigation_id": str(investigation_id),
            "incident": incident,
            "triage_hint": triage_hint,
        }
        config = self._config(investigation_id, scope)
        # runs to the interrupt before human_review (or to END for dropped/escalated)
        await self._graph.ainvoke(initial, config)
        snapshot = await self._graph.aget_state(config)
        return _status_from_state(investigation_id, snapshot)

    async def get_status(self, investigation_id, scope) -> InvestigationStatus:
        snapshot = await self._graph.aget_state(self._config(investigation_id, scope))
        return _status_from_state(investigation_id, snapshot)

    async def resume_after_approval(self, *, investigation_id, decision, target, target_id,
                                    decided_by, scope) -> InvestigationStatus:
        config = self._config(investigation_id, scope)
        # inject the recorded human decision, then continue past the interrupt
        await self._graph.aupdate_state(config, {"human_decision": {
            "decision": decision, "target": target, "target_id": target_id,
            "decided_by": decided_by,
        }})
        await self._graph.ainvoke(None, config)
        snapshot = await self._graph.aget_state(config)
        return _status_from_state(investigation_id, snapshot)
