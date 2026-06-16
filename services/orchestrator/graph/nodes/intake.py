"""LangGraph node wrapper for the Incident Intake Agent (orchestrator service).

This is the graph ENTRY node. It is intentionally thin: it adapts the blackboard
``InvestigationState`` to the agent's typed ``IntakeInput``, runs the agent, and returns a
JSON-serializable partial-state delta for the durable checkpointer. It never raises into the
graph runtime; on hard failure it sets ``intake_failed`` so the post-intake routing (the
triage_gate / conditional edge from Phase 2 §5.3) escalates to a human instead of proceeding.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from contracts.models import InvestigationState

from agents.intake.agent import IncidentIntakeAgent
from agents.intake.errors import IntakeError, LLMUnavailableError
from agents.intake.schemas import IntakeInput

logger = logging.getLogger("orchestrator.graph.intake")

IntakeNode = Callable[[InvestigationState], Awaitable[dict[str, Any]]]


def make_intake_node(agent: IncidentIntakeAgent) -> IntakeNode:
    """Bind the agent and return the async LangGraph node callable."""

    async def intake_node(state: InvestigationState) -> dict[str, Any]:
        request_id = str(state.get("investigation_id") or uuid4())
        scope: dict[str, Any] = {"investigation_id": request_id}

        # --- adapt state -> typed input ---
        try:
            request = IntakeInput(
                investigation_id=UUID(str(state["investigation_id"])),
                incident=state["incident"],
                triage_hint=state.get("triage_hint"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error("intake node received invalid state: %s", exc)
            return {
                "intake_failed": True,
                "errors": [{"node": "intake", "type": "input", "detail": str(exc)}],
            }

        # --- run the agent; convert failures into routing signals, never crash the graph ---
        try:
            output = await agent.run(request, request_id=request_id, scope=scope)
        except LLMUnavailableError as exc:
            logger.error("intake LLM unavailable; routing to human: %s", exc)
            return {
                "intake_failed": True,
                "errors": [{"node": "intake", "type": "llm_unavailable", "detail": str(exc)}],
            }
        except IntakeError as exc:
            logger.error("intake failed: %s", exc)
            return {
                "intake_failed": True,
                "errors": [{"node": "intake", "type": type(exc).__name__, "detail": str(exc)}],
            }
        except Exception as exc:  # defensive: the graph must survive any agent bug
            logger.exception("unexpected intake error")
            return {
                "intake_failed": True,
                "errors": [{"node": "intake", "type": "unexpected", "detail": str(exc)}],
            }

        payload = output.model_dump(mode="json")
        return {
            "classification": payload["classification"],
            "affected_systems": payload["affected_systems"],
            "initial_hypothesis": payload["initial_hypothesis"],
            "recommended_triage": payload["recommended_triage"],
            "intake_metadata": payload["metadata"],
            "intake_failed": False,
        }

    return intake_node
