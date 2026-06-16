"""PagerDuty V3 webhook verification + normalization (ingress companion).

This is the INBOUND half of the PagerDuty integration and is deliberately NOT part of the MCP
server: receiving a webhook is HTTP ingestion (the webhook-ingress service), not a tool call. It
verifies the HMAC signature, rejects stale deliveries, and maps a trigger event to the platform's
NormalizedIncident — the event that starts an investigation.

(The deployment directory may be named ``webhook-ingress``; the Python package uses an
underscore so it is importable.)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from contracts.models import NormalizedIncident
from mcp_connectors.servers.pagerduty.normalize import _parse_dt, normalize_incident

logger = logging.getLogger("ingress.pagerduty")

SIGNATURE_HEADER = "X-PagerDuty-Signature"
_SIGNATURE_PREFIX = "v1="

# Events that start an investigation. Other event types are acknowledged and ignored.
TRIGGER_EVENT_TYPES = frozenset({"incident.triggered"})


def verify_signature(raw_body: bytes, signature_header: Optional[str], secret: str) -> bool:
    """Verify a PagerDuty V3 webhook signature.

    The header carries one or more ``v1=<hex>`` signatures (multiple during secret rotation). We
    accept the delivery if any provided signature matches our HMAC-SHA256 of the raw body, using a
    constant-time comparison.
    """
    if not signature_header or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = [
        part.strip()[len(_SIGNATURE_PREFIX):]
        for part in signature_header.split(",")
        if part.strip().startswith(_SIGNATURE_PREFIX)
    ]
    return any(hmac.compare_digest(expected, candidate) for candidate in provided)


def event_type(payload: dict[str, Any]) -> Optional[str]:
    return (payload.get("event") or {}).get("event_type")


def is_trigger_event(payload: dict[str, Any]) -> bool:
    return event_type(payload) in TRIGGER_EVENT_TYPES


def is_fresh(payload: dict[str, Any], *, max_age_s: float = 600.0) -> bool:
    """Reject deliveries whose ``occurred_at`` is too old (basic replay protection).

    Returns True when no timestamp is present (cannot judge) or it is within ``max_age_s``.
    Durable de-duplication by event id belongs in the ingress store, on top of this check.
    """
    occurred = _parse_dt((payload.get("event") or {}).get("occurred_at"))
    if occurred is None:
        return True
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - occurred).total_seconds()
    return age <= max_age_s


def to_normalized_incident(
    payload: dict[str, Any], *, incident_id: Optional[UUID] = None,
) -> NormalizedIncident:
    """Map a verified PagerDuty webhook event to a NormalizedIncident.

    A fresh internal investigation UUID is minted here (``incident_id`` defaulting to a new uuid4
    inside ``normalize_incident``) — distinct from PagerDuty's own incident id.
    """
    data = (payload.get("event") or {}).get("data") or {}
    return normalize_incident(data, incident_id=incident_id)
