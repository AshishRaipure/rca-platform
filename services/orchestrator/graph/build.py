"""Build the investigation reasoning graph (LangGraph).

Wires the agent nodes into the workflow from Phase 2 §5.3:

    intake -> triage_gate -> (parallel: knowledge [, architecture]) -> human_review(interrupt)

`intake` and `knowledge` nodes exist today; `architecture`/`rca`/`recommendation`/`communication`
are accepted as optional callables and slotted in as they are built. The graph is compiled with
``interrupt_before=["human_review"]`` so the workflow durably pauses for human approval; the
orchestrator resumes it after a decision is recorded.

NOTE (Phase 1 R-1): for production durability across long human pauses, the canonical executor is
Temporal, with LangGraph confined to intra-investigation reasoning. This in-process compiled graph
+ a persistent checkpointer is the lighter-weight path and the unit of work Temporal drives.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

Node = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _after_intake(state: dict[str, Any]) -> str:
    return "escalate" if state.get("intake_failed") else "triage"


def _after_triage(state: dict[str, Any], *, has_architecture: bool) -> Any:
    # the in-graph gate respects the Intake Agent's clamped recommendation
    if state.get("recommended_triage") == "drop":
        return "drop"
    return ["knowledge", "architecture"] if has_architecture else ["knowledge"]


def _after_rca(state: dict[str, Any]) -> str:
    # confidence gate: low/speculative analyses escalate straight to human review
    conf = state.get("rca_confidence")
    return "escalate" if conf in ("low", "speculative") else "recommend"


async def _triage_gate(state: dict[str, Any]) -> dict[str, Any]:
    # refinement point; the agent already clamped 'drop' for serious incidents
    return {"status": "triaged"}


async def _human_review(state: dict[str, Any]) -> dict[str, Any]:
    # reached only after the interrupt is resumed with a recorded decision
    decision = (state.get("human_decision") or {}).get("decision")
    if decision == "reject":
        return {"status": "closed_rejected"}
    if decision == "needs_changes":
        return {"status": "changes_requested"}
    return {"status": "completed"}


def build_investigation_graph(
    *,
    intake_node: Node,
    knowledge_node: Node,
    architecture_node: Optional[Node] = None,
    rca_node: Optional[Node] = None,
    recommendation_node: Optional[Node] = None,
    communication_node: Optional[Node] = None,
    checkpointer: Optional[Any] = None,
) -> Any:
    """Construct and compile the StateGraph. langgraph is imported lazily."""
    from langgraph.graph import END, START, StateGraph

    from contracts.models import InvestigationState

    graph = StateGraph(InvestigationState)
    graph.add_node("intake", intake_node)
    graph.add_node("triage_gate", _triage_gate)
    graph.add_node("knowledge", knowledge_node)
    has_architecture = architecture_node is not None
    if has_architecture:
        graph.add_node("architecture", architecture_node)
    graph.add_node("human_review", _human_review)

    graph.add_edge(START, "intake")
    graph.add_conditional_edges(
        "intake", _after_intake, {"triage": "triage_gate", "escalate": "human_review"})
    graph.add_conditional_edges(
        "triage_gate", lambda s: _after_triage(s, has_architecture=has_architecture),
        {"knowledge": "knowledge", "architecture": "architecture", "drop": END},
    )

    # knowledge (and architecture) feed the downstream chain; until RCA/rec/comms exist, they
    # converge on human_review.
    downstream_entry = "human_review"
    if rca_node is not None:
        graph.add_node("rca", rca_node)
        downstream_entry = "rca"
    graph.add_edge("knowledge", downstream_entry)
    if has_architecture:
        graph.add_edge("architecture", downstream_entry)

    if rca_node is not None:
        if recommendation_node is not None:
            graph.add_node("recommendation", recommendation_node)
            # confidence gate after RCA: escalate low/speculative straight to human review
            graph.add_conditional_edges(
                "rca", _after_rca,
                {"recommend": "recommendation", "escalate": "human_review"})
            if communication_node is not None:
                graph.add_node("communication", communication_node)
                graph.add_edge("recommendation", "communication")
                graph.add_edge("communication", "human_review")
            else:
                graph.add_edge("recommendation", "human_review")
        else:
            graph.add_edge("rca", "human_review")

    graph.add_edge("human_review", END)

    return graph.compile(checkpointer=checkpointer, interrupt_before=["human_review"])
