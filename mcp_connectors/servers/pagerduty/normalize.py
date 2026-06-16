"""Normalize PagerDuty incidents into the platform's NormalizedIncident contract.

Used by the ingress/webhook path (which mints the internal investigation UUID). The MCP read
tools return PagerDuty projections; turning one into a NormalizedIncident — with a fresh internal
id — is an ingestion concern, kept here so both paths share one mapping.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from contracts.enums import SeverityLevel, SourceSystem
from contracts.models import NormalizedIncident

# Priority summaries are org-configurable; this maps the common conventions. Falls back to urgency.
_PRIORITY_TO_SEVERITY: dict[str, SeverityLevel] = {
    "P1": SeverityLevel.critical, "SEV1": SeverityLevel.critical, "CRITICAL": SeverityLevel.critical,
    "P2": SeverityLevel.high, "SEV2": SeverityLevel.high, "HIGH": SeverityLevel.high,
    "P3": SeverityLevel.medium, "SEV3": SeverityLevel.medium, "MEDIUM": SeverityLevel.medium,
    "P4": SeverityLevel.low, "SEV4": SeverityLevel.low, "LOW": SeverityLevel.low,
    "P5": SeverityLevel.info, "SEV5": SeverityLevel.info, "INFO": SeverityLevel.info,
}


def map_severity(priority_summary: Optional[str], urgency: Optional[str]) -> Optional[SeverityLevel]:
    if priority_summary:
        key = priority_summary.strip().upper().replace(" ", "").replace("-", "")
        if key in _PRIORITY_TO_SEVERITY:
            return _PRIORITY_TO_SEVERITY[key]
    if urgency:
        u = urgency.strip().lower()
        if u == "high":
            return SeverityLevel.high
        if u == "low":
            return SeverityLevel.low
    return None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def normalize_incident(raw: dict[str, Any], *, incident_id: Optional[UUID] = None) -> NormalizedIncident:
    """Accepts either a wrapped ({"incident": {...}}) or bare PagerDuty incident/event-data dict."""
    data = raw.get("incident", raw) if isinstance(raw, dict) and "incident" in raw else raw
    data = data or {}

    priority = data.get("priority") or {}
    priority_summary = priority.get("summary") if isinstance(priority, dict) else None

    return NormalizedIncident(
        incident_id=incident_id or uuid4(),
        source_system=SourceSystem.pagerduty,
        fingerprint=data.get("incident_key") or data.get("id") or uuid4().hex,
        title=data.get("title") or data.get("summary") or "PagerDuty incident",
        description=data.get("description"),
        provider_severity=map_severity(priority_summary, data.get("urgency")),
        pagerduty_id=data.get("id"),
        pagerduty_dedup_key=data.get("incident_key"),
        servicenow_id=None,
        raw_payload=data,
        created_at=_parse_dt(data.get("created_at")) or datetime.now(timezone.utc),
    )
