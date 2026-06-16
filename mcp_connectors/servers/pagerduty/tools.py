"""PagerDuty read-only tool registry.

Every tool here is a GET. The module-level assertion at the bottom fails import if any spec is
ever marked ``mutates=True`` — the structural guarantee that a write tool cannot be registered.
"""
from __future__ import annotations

from typing import Any

from mcp_connectors.contracts import ToolContext, ToolSpec
from mcp_connectors.servers.pagerduty.client import PagerDutyClient
from mcp_connectors.servers.pagerduty.schemas import (
    GetIncidentInput,
    GetServiceInput,
    GetUserInput,
    IncidentChildInput,
    ListIncidentsInput,
    ListServicesInput,
    OnCallsInput,
    PDAlert,
    PDIncident,
    PDLogEntry,
    PDNote,
    PDOnCall,
    PDService,
    PDUser,
)


def _dump_many(model_cls, rows: list) -> list[dict[str, Any]]:
    return [model_cls.model_validate(r).model_dump(mode="json") for r in rows]


async def _h_get_incident(client: PagerDutyClient, args: GetIncidentInput, ctx: ToolContext):
    raw = await client.get_incident(args.incident_id, timeout_s=ctx.timeout_s)
    return {"incident": PDIncident.model_validate(raw).model_dump(mode="json")}


async def _h_list_incidents(client: PagerDutyClient, args: ListIncidentsInput, ctx: ToolContext):
    rows = await client.list_incidents(
        statuses=args.statuses, since=args.since, until=args.until,
        service_ids=args.service_ids, team_ids=args.team_ids, urgencies=args.urgencies,
        max_items=args.max_items, timeout_s=ctx.timeout_s,
    )
    incidents = _dump_many(PDIncident, rows)
    return {"incidents": incidents, "count": len(incidents)}


async def _h_list_alerts(client: PagerDutyClient, args: IncidentChildInput, ctx: ToolContext):
    rows = await client.list_alerts(args.incident_id, max_items=args.max_items, timeout_s=ctx.timeout_s)
    alerts = _dump_many(PDAlert, rows)
    return {"alerts": alerts, "count": len(alerts)}


async def _h_list_log_entries(client: PagerDutyClient, args: IncidentChildInput, ctx: ToolContext):
    rows = await client.list_log_entries(args.incident_id, max_items=args.max_items, timeout_s=ctx.timeout_s)
    entries = _dump_many(PDLogEntry, rows)
    return {"log_entries": entries, "count": len(entries)}


async def _h_list_notes(client: PagerDutyClient, args: IncidentChildInput, ctx: ToolContext):
    rows = await client.list_notes(args.incident_id, timeout_s=ctx.timeout_s)
    notes = _dump_many(PDNote, rows)
    return {"notes": notes, "count": len(notes)}


async def _h_get_service(client: PagerDutyClient, args: GetServiceInput, ctx: ToolContext):
    raw = await client.get_service(args.service_id, timeout_s=ctx.timeout_s)
    return {"service": PDService.model_validate(raw).model_dump(mode="json")}


async def _h_list_services(client: PagerDutyClient, args: ListServicesInput, ctx: ToolContext):
    rows = await client.list_services(
        query=args.query, team_ids=args.team_ids, max_items=args.max_items, timeout_s=ctx.timeout_s,
    )
    services = _dump_many(PDService, rows)
    return {"services": services, "count": len(services)}


async def _h_get_oncalls(client: PagerDutyClient, args: OnCallsInput, ctx: ToolContext):
    rows = await client.list_oncalls(
        escalation_policy_ids=args.escalation_policy_ids, schedule_ids=args.schedule_ids,
        since=args.since, until=args.until, max_items=args.max_items, timeout_s=ctx.timeout_s,
    )
    oncalls = _dump_many(PDOnCall, rows)
    return {"oncalls": oncalls, "count": len(oncalls)}


async def _h_get_user(client: PagerDutyClient, args: GetUserInput, ctx: ToolContext):
    raw = await client.get_user(args.user_id, timeout_s=ctx.timeout_s)
    return {"user": PDUser.model_validate(raw).model_dump(mode="json")}


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="pagerduty.get_incident",
        description="Fetch a single PagerDuty incident by id (read-only).",
        input_model=GetIncidentInput, handler=_h_get_incident,
        read_scopes=("pagerduty:incidents:read",),
    ),
    ToolSpec(
        name="pagerduty.list_incidents",
        description="List PagerDuty incidents filtered by status/service/team/urgency (read-only).",
        input_model=ListIncidentsInput, handler=_h_list_incidents,
        read_scopes=("pagerduty:incidents:read",),
    ),
    ToolSpec(
        name="pagerduty.list_alerts",
        description="List the alerts attached to a PagerDuty incident (read-only).",
        input_model=IncidentChildInput, handler=_h_list_alerts,
        read_scopes=("pagerduty:incidents:read",),
    ),
    ToolSpec(
        name="pagerduty.get_incident_log_entries",
        description="Fetch a PagerDuty incident's timeline / log entries (read-only).",
        input_model=IncidentChildInput, handler=_h_list_log_entries,
        read_scopes=("pagerduty:incidents:read",),
    ),
    ToolSpec(
        name="pagerduty.get_incident_notes",
        description="Fetch responder notes on a PagerDuty incident (read-only).",
        input_model=IncidentChildInput, handler=_h_list_notes,
        read_scopes=("pagerduty:incidents:read",),
    ),
    ToolSpec(
        name="pagerduty.get_service",
        description="Fetch a PagerDuty service by id (read-only).",
        input_model=GetServiceInput, handler=_h_get_service,
        read_scopes=("pagerduty:services:read",),
    ),
    ToolSpec(
        name="pagerduty.list_services",
        description="List/search PagerDuty services (read-only).",
        input_model=ListServicesInput, handler=_h_list_services,
        read_scopes=("pagerduty:services:read",),
    ),
    ToolSpec(
        name="pagerduty.get_oncalls",
        description="Resolve who is currently on call for given escalation policies/schedules (read-only).",
        input_model=OnCallsInput, handler=_h_get_oncalls,
        read_scopes=("pagerduty:oncalls:read",),
    ),
    ToolSpec(
        name="pagerduty.get_user",
        description="Fetch a PagerDuty user by id for responder context (read-only).",
        input_model=GetUserInput, handler=_h_get_user,
        read_scopes=("pagerduty:users:read",),
    ),
]

# STRUCTURAL GUARANTEE: a mutating tool can never be registered for this connector.
assert all(not spec.mutates for spec in TOOL_SPECS), "PagerDuty connector must expose read-only tools only"
