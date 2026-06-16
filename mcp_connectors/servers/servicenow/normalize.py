"""Normalize ServiceNow incidents into the platform's NormalizedIncident contract.

Used by ingestion/correlation when the platform pulls the ServiceNow incident linked to a
PagerDuty alert. The MCP read tools return ServiceNow projections; turning one into a
NormalizedIncident (with a fresh internal UUID) is an ingestion concern, kept here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from contracts.enums import SeverityLevel, SourceSystem
from contracts.models import NormalizedIncident
from mcp_connectors.servers.servicenow.schemas import sn_display, sn_value

# ServiceNow incident priority: 1=Critical, 2=High, 3=Moderate, 4=Low, 5=Planning
_PRIORITY_TO_SEVERITY: dict[str, SeverityLevel] = {
    "1": SeverityLevel.critical,
    "2": SeverityLevel.high,
    "3": SeverityLevel.medium,
    "4": SeverityLevel.low,
    "5": SeverityLevel.info,
}
# severity field fallback: 1=High, 2=Medium, 3=Low
_SEVERITY_FIELD_TO_SEVERITY: dict[str, SeverityLevel] = {
    "1": SeverityLevel.high,
    "2": SeverityLevel.medium,
    "3": SeverityLevel.low,
}


def map_severity(priority: Optional[str], urgency: Optional[str],
                 severity_field: Optional[str] = None) -> Optional[SeverityLevel]:
    if priority and str(priority) in _PRIORITY_TO_SEVERITY:
        return _PRIORITY_TO_SEVERITY[str(priority)]
    if severity_field and str(severity_field) in _SEVERITY_FIELD_TO_SEVERITY:
        return _SEVERITY_FIELD_TO_SEVERITY[str(severity_field)]
    if urgency and str(urgency) in ("1", "high"):
        return SeverityLevel.high
    if urgency and str(urgency) in ("3", "low"):
        return SeverityLevel.low
    return None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "T").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize_incident(record: dict[str, Any], *, incident_id: Optional[UUID] = None) -> NormalizedIncident:
    record = record or {}
    sys_id = sn_value(record, "sys_id")
    number = sn_value(record, "number")
    created = _parse_dt(sn_value(record, "opened_at")) or _parse_dt(sn_value(record, "sys_created_on"))

    return NormalizedIncident(
        incident_id=incident_id or uuid4(),
        source_system=SourceSystem.servicenow,
        fingerprint=number or sys_id or uuid4().hex,
        title=sn_display(record, "short_description") or "ServiceNow incident",
        description=sn_display(record, "description"),
        provider_severity=map_severity(
            sn_value(record, "priority"), sn_value(record, "urgency"), sn_value(record, "severity"),
        ),
        pagerduty_id=None,
        pagerduty_dedup_key=None,
        servicenow_id=sys_id,
        raw_payload=record,
        created_at=created or datetime.now(timezone.utc),
    )
