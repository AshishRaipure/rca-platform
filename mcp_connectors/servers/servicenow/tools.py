"""ServiceNow read-only tool registry.

Every tool is a GET. The module-level assertion at the bottom fails import if any spec is ever
marked ``mutates=True`` — the structural guarantee that a write tool cannot be registered.
"""
from __future__ import annotations

from typing import Any, Optional

from mcp_connectors.contracts import ToolContext, ToolSpec
from mcp_connectors.servers.servicenow.client import ServiceNowClient
from mcp_connectors.servers.servicenow.errors import ServiceNowNotFoundError
from mcp_connectors.servers.servicenow.schemas import (
    CIRelationshipsInput,
    GetChangeInput,
    GetConfigItemInput,
    GetIncidentInput,
    GetKnowledgeInput,
    GetUserInput,
    IncidentJournalInput,
    ListChangesInput,
    ListIncidentsInput,
    SearchKnowledgeInput,
    SNChange,
    SNConfigItem,
    SNIncident,
    SNJournalEntry,
    SNKnowledge,
    SNRelationship,
    SNUser,
)


def _compose_query(parts: list[Optional[str]], *, order_by: Optional[str] = None) -> Optional[str]:
    clauses = [p for p in parts if p]
    query = "^".join(clauses) if clauses else ""
    if order_by and "ORDERBY" not in query.upper():
        query = f"{query}^{order_by}" if query else order_by
    return query or None


def _dump_many(model_cls, rows: list) -> list[dict[str, Any]]:
    return [model_cls.from_record(r).model_dump(mode="json") for r in rows]


async def _h_get_incident(client: ServiceNowClient, args: GetIncidentInput, ctx: ToolContext):
    rec = await client.get_incident(sys_id=args.sys_id, number=args.number, timeout_s=ctx.timeout_s)
    if not rec:
        raise ServiceNowNotFoundError("incident not found")
    return {"incident": SNIncident.from_record(rec).model_dump(mode="json")}


async def _h_list_incidents(client: ServiceNowClient, args: ListIncidentsInput, ctx: ToolContext):
    query = _compose_query(
        [
            f"state={args.state}" if args.state else None,
            f"priority={args.priority}" if args.priority else None,
            f"assignment_group={args.assignment_group}" if args.assignment_group else None,
            f"cmdb_ci={args.cmdb_ci}" if args.cmdb_ci else None,
            f"opened_at>{args.opened_after}" if args.opened_after else None,
            args.query,
        ],
        order_by="ORDERBYDESCsys_created_on",
    )
    rows = await client.list_incidents(query=query, limit=args.max_items, timeout_s=ctx.timeout_s)
    incidents = _dump_many(SNIncident, rows)
    return {"incidents": incidents, "count": len(incidents)}


async def _h_get_incident_journal(client: ServiceNowClient, args: IncidentJournalInput, ctx: ToolContext):
    rows = await client.get_incident_journal(args.sys_id, limit=args.max_items, timeout_s=ctx.timeout_s)
    entries = _dump_many(SNJournalEntry, rows)
    return {"journal": entries, "count": len(entries)}


async def _h_get_change(client: ServiceNowClient, args: GetChangeInput, ctx: ToolContext):
    rec = await client.get_change_request(sys_id=args.sys_id, number=args.number, timeout_s=ctx.timeout_s)
    if not rec:
        raise ServiceNowNotFoundError("change request not found")
    return {"change_request": SNChange.from_record(rec).model_dump(mode="json")}


async def _h_list_changes(client: ServiceNowClient, args: ListChangesInput, ctx: ToolContext):
    query = _compose_query(
        [
            f"cmdb_ci={args.cmdb_ci}" if args.cmdb_ci else None,
            f"state={args.state}" if args.state else None,
            f"closed_at>{args.closed_after}" if args.closed_after else None,
            args.query,
        ],
        order_by="ORDERBYDESCsys_created_on",
    )
    rows = await client.list_change_requests(query=query, limit=args.max_items, timeout_s=ctx.timeout_s)
    changes = _dump_many(SNChange, rows)
    return {"change_requests": changes, "count": len(changes)}


async def _h_get_knowledge(client: ServiceNowClient, args: GetKnowledgeInput, ctx: ToolContext):
    sys_id = args.sys_id or args.id  # Agent 2's freshness probe sends {"id": <sys_id>}
    rec = await client.get_knowledge(sys_id=sys_id, number=args.number, timeout_s=ctx.timeout_s)
    if not rec:
        raise ServiceNowNotFoundError("knowledge article not found")
    return {"article": SNKnowledge.from_record(rec).model_dump(mode="json")}


async def _h_search_knowledge(client: ServiceNowClient, args: SearchKnowledgeInput, ctx: ToolContext):
    rows = await client.search_knowledge(text=args.query, limit=args.max_items, timeout_s=ctx.timeout_s)
    articles = _dump_many(SNKnowledge, rows)
    return {"articles": articles, "count": len(articles)}


async def _h_get_cmdb_ci(client: ServiceNowClient, args: GetConfigItemInput, ctx: ToolContext):
    rec = await client.get_cmdb_ci(sys_id=args.sys_id, name=args.name, timeout_s=ctx.timeout_s)
    if not rec:
        raise ServiceNowNotFoundError("configuration item not found")
    return {"ci": SNConfigItem.from_record(rec).model_dump(mode="json")}


async def _h_get_ci_relationships(client: ServiceNowClient, args: CIRelationshipsInput, ctx: ToolContext):
    rows = await client.get_ci_relationships(args.ci_sys_id, limit=args.max_items, timeout_s=ctx.timeout_s)
    rels = _dump_many(SNRelationship, rows)
    return {"relationships": rels, "count": len(rels)}


async def _h_get_user(client: ServiceNowClient, args: GetUserInput, ctx: ToolContext):
    rec = await client.get_user(args.sys_id, timeout_s=ctx.timeout_s)
    if not rec:
        raise ServiceNowNotFoundError("user not found")
    return {"user": SNUser.from_record(rec).model_dump(mode="json")}


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(name="servicenow.get_incident",
             description="Fetch a ServiceNow incident by sys_id or number (read-only).",
             input_model=GetIncidentInput, handler=_h_get_incident,
             read_scopes=("servicenow:incident:read",)),
    ToolSpec(name="servicenow.list_incidents",
             description="List ServiceNow incidents by state/priority/group/CI/date (read-only).",
             input_model=ListIncidentsInput, handler=_h_list_incidents,
             read_scopes=("servicenow:incident:read",)),
    ToolSpec(name="servicenow.get_incident_journal",
             description="Fetch an incident's work notes and comments timeline (read-only).",
             input_model=IncidentJournalInput, handler=_h_get_incident_journal,
             read_scopes=("servicenow:incident:read",)),
    ToolSpec(name="servicenow.get_change_request",
             description="Fetch a ServiceNow change request by sys_id or number (read-only).",
             input_model=GetChangeInput, handler=_h_get_change,
             read_scopes=("servicenow:change:read",)),
    ToolSpec(name="servicenow.list_change_requests",
             description="List recent change requests, e.g. those touching a CI (read-only).",
             input_model=ListChangesInput, handler=_h_list_changes,
             read_scopes=("servicenow:change:read",)),
    ToolSpec(name="servicenow.get_knowledge",
             description="Fetch a ServiceNow knowledge article by sys_id/id or number (read-only).",
             input_model=GetKnowledgeInput, handler=_h_get_knowledge,
             read_scopes=("servicenow:knowledge:read",)),
    ToolSpec(name="servicenow.search_knowledge",
             description="Search published ServiceNow knowledge articles by text (read-only).",
             input_model=SearchKnowledgeInput, handler=_h_search_knowledge,
             read_scopes=("servicenow:knowledge:read",)),
    ToolSpec(name="servicenow.get_cmdb_ci",
             description="Fetch a CMDB configuration item by sys_id or name (read-only).",
             input_model=GetConfigItemInput, handler=_h_get_cmdb_ci,
             read_scopes=("servicenow:cmdb:read",)),
    ToolSpec(name="servicenow.get_ci_relationships",
             description="Fetch upstream/downstream CMDB relationships for a CI (read-only).",
             input_model=CIRelationshipsInput, handler=_h_get_ci_relationships,
             read_scopes=("servicenow:cmdb:read",)),
    ToolSpec(name="servicenow.get_user",
             description="Fetch a ServiceNow user by sys_id for responder context (read-only).",
             input_model=GetUserInput, handler=_h_get_user,
             read_scopes=("servicenow:user:read",)),
]

# STRUCTURAL GUARANTEE: a mutating tool can never be registered for this connector.
assert all(not spec.mutates for spec in TOOL_SPECS), "ServiceNow connector must expose read-only tools only"
