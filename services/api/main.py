"""Production entrypoint: `uvicorn services.api.main:app`.

Wires the components that exist today (DB, OIDC verifier, unit-of-work). The orchestrator is left
pluggable: it requires the LLM client (libs/llm), the MCP gateway, and the RAG retriever to build
the agent nodes, which are assembled by the broader composition root. Until those are wired, the
API still boots and serves health/identity/read endpoints; investigation start/resume return 503.
"""
from __future__ import annotations

from typing import Any, Optional

from db.engine import Database
from db.repositories import SqlAlchemyUnitOfWork
from services.api.app import create_app
from services.api.auth import OIDCTokenVerifier
from services.api.config import ApiSettings
from services.api.deps import AppDeps
from services.orchestrator.client import LangGraphOrchestratorClient, OrchestratorClient


def build_orchestrator(*, intake_agent: Any, knowledge_agent: Any,
                       architecture_agent: Optional[Any] = None,
                       rca_agent: Optional[Any] = None,
                       recommendation_agent: Optional[Any] = None,
                       communication_agent: Optional[Any] = None,
                       checkpointer: Optional[Any] = None) -> OrchestratorClient:
    """Assemble the full LangGraph workflow from agent instances and wrap it in the client.

    Intake + knowledge are required; architecture / rca / recommendation / communication are wired
    when provided (the graph builder degrades gracefully if a later stage is absent). Construct the
    agents from the composition root with libs/llm, the MCP gateway, and the RAG retriever.
    """
    from services.orchestrator.graph.build import build_investigation_graph
    from services.orchestrator.graph.nodes.intake import make_intake_node
    from services.orchestrator.graph.nodes.knowledge import make_knowledge_node

    nodes: dict[str, Any] = {
        "intake_node": make_intake_node(intake_agent),
        "knowledge_node": make_knowledge_node(knowledge_agent),
    }
    if architecture_agent is not None:
        from services.orchestrator.graph.nodes.architecture import make_architecture_node
        nodes["architecture_node"] = make_architecture_node(architecture_agent)
    if rca_agent is not None:
        from services.orchestrator.graph.nodes.rca import make_rca_node
        nodes["rca_node"] = make_rca_node(rca_agent)
    if recommendation_agent is not None:
        from services.orchestrator.graph.nodes.recommendation import make_recommendation_node
        nodes["recommendation_node"] = make_recommendation_node(recommendation_agent)
    if communication_agent is not None:
        from services.orchestrator.graph.nodes.communication import make_communication_node
        nodes["communication_node"] = make_communication_node(communication_agent)

    graph = build_investigation_graph(checkpointer=checkpointer, **nodes)
    return LangGraphOrchestratorClient(graph)


def build_app_from_env(orchestrator: Optional[OrchestratorClient] = None):
    settings = ApiSettings.from_env()
    database = Database(settings.db_dsn)
    deps = AppDeps(
        settings=settings,
        token_verifier=OIDCTokenVerifier(settings),
        uow_factory=lambda scope: SqlAlchemyUnitOfWork(database, scope),
        orchestrator=orchestrator,
        aclose=database.dispose,
    )
    return create_app(deps)


app = build_app_from_env()
