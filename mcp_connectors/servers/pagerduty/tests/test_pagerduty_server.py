"""Unit tests for the PagerDuty MCP integration.

Fake HTTP transport, no network. These assert the read-only guarantee, correct tool dispatch and
validation, pagination/caps, error mapping, webhook signature verification, and normalization.

Run with: pytest -q  (requires pytest, pytest-asyncio, pydantic v2).
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from contracts.enums import SeverityLevel, SourceSystem
from mcp_connectors.servers.pagerduty.client import PagerDutyClient
from mcp_connectors.servers.pagerduty.config import PagerDutyConfig
from mcp_connectors.servers.pagerduty.normalize import normalize_incident
from mcp_connectors.servers.pagerduty.server import PagerDutyMCPServer
from mcp_connectors.servers.pagerduty.tools import TOOL_SPECS
from services.webhook_ingress.providers.pagerduty import (
    is_trigger_event,
    to_normalized_incident,
    verify_signature,
)


# --------------------------------------------------------------------------- fakes

class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class FakeTransport:
    def __init__(self, queue=None, responder=None):
        self._queue = list(queue or [])
        self._responder = responder
        self.calls = 0
        self.requests = []

    async def get(self, url, *, headers, params, timeout):
        self.calls += 1
        self.requests.append((url, dict(params)))
        if self._responder is not None:
            return self._responder(url, dict(params))
        if not self._queue:
            raise RuntimeError("no fake response queued")
        return self._queue.pop(0)


def _server(transport, config=None):
    config = config or PagerDutyConfig()
    client = PagerDutyClient(
        transport, base_url=config.base_url, api_token="ro-token",
        api_version=config.api_version, config=config,
    )
    return PagerDutyMCPServer(client, config=config), client


# -------------------------------------------------------------------------- tests

def test_only_read_only_tools_are_registered():
    assert TOOL_SPECS and all(not s.mutates for s in TOOL_SPECS)
    write_verbs = ("ack", "acknowledge", "resolve", "create", "update", "delete",
                   "reassign", "snooze", "close", "merge", "post", "put")
    for s in TOOL_SPECS:
        assert not any(v in s.name.lower() for v in write_verbs), s.name
    # the client exposes no write primitive
    _, client = _server(FakeTransport())
    for method in ("post", "put", "delete", "patch"):
        assert not hasattr(client, method)


def test_list_tools_reports_read_only():
    server, _ = _server(FakeTransport())
    tools = server.list_tools()
    assert len(tools) == len(TOOL_SPECS)
    assert all(t["read_only"] for t in tools)


@pytest.mark.asyncio
async def test_get_incident_dispatch():
    tx = FakeTransport(queue=[FakeResponse(200, {"incident": {
        "id": "PT1", "title": "High CPU", "status": "triggered", "urgency": "high"}})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="pagerduty.get_incident",
                                 params={"incident_id": "PT1"}, request_id="r")
    assert res.ok is True
    assert res.data["incident"]["id"] == "PT1"


@pytest.mark.asyncio
async def test_unknown_tool_rejected():
    server, _ = _server(FakeTransport())
    res = await server.call_tool(tool="pagerduty.resolve_incident", params={}, request_id="r")
    assert res.ok is False
    assert "unknown tool" in res.error


@pytest.mark.asyncio
async def test_invalid_params_rejected():
    server, _ = _server(FakeTransport())
    res = await server.call_tool(tool="pagerduty.get_incident", params={}, request_id="r")
    assert res.ok is False
    assert "invalid params" in res.error


@pytest.mark.asyncio
async def test_pagination_follows_more_flag():
    def responder(url, params):
        if params.get("offset", 0) == 0:
            return FakeResponse(200, {"alerts": [{"id": "A1"}, {"id": "A2"}], "more": True})
        return FakeResponse(200, {"alerts": [{"id": "A3"}], "more": False})

    server, _ = _server(FakeTransport(responder=responder), PagerDutyConfig(page_limit=2))
    res = await server.call_tool(tool="pagerduty.list_alerts",
                                 params={"incident_id": "PT1"}, request_id="r")
    assert res.ok is True
    assert res.data["count"] == 3


@pytest.mark.asyncio
async def test_pagination_respects_max_items():
    def responder(url, params):  # never stops on its own
        return FakeResponse(200, {"alerts": [{"id": "X"}, {"id": "Y"}], "more": True})

    cfg = PagerDutyConfig(page_limit=2, max_items=3)
    server, _ = _server(FakeTransport(responder=responder), cfg)
    res = await server.call_tool(tool="pagerduty.list_alerts",
                                 params={"incident_id": "PT1"}, request_id="r")
    assert res.data["count"] == 3


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds():
    tx = FakeTransport(queue=[
        FakeResponse(429, {}, headers={"Retry-After": "0"}),
        FakeResponse(200, {"incident": {"id": "PT1"}}),
    ])
    server, _ = _server(tx, PagerDutyConfig(backoff_base_s=0.0, max_retries=3))
    res = await server.call_tool(tool="pagerduty.get_incident",
                                 params={"incident_id": "PT1"}, request_id="r")
    assert res.ok is True
    assert tx.calls == 2


@pytest.mark.asyncio
async def test_not_found_maps_to_error():
    tx = FakeTransport(queue=[FakeResponse(404, {})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="pagerduty.get_incident",
                                 params={"incident_id": "NOPE"}, request_id="r")
    assert res.ok is False
    assert "not found" in res.error.lower()


@pytest.mark.asyncio
async def test_auth_error_is_not_retried():
    tx = FakeTransport(queue=[FakeResponse(403, {})])
    server, _ = _server(tx, PagerDutyConfig(max_retries=3))
    res = await server.call_tool(tool="pagerduty.get_incident",
                                 params={"incident_id": "PT1"}, request_id="r")
    assert res.ok is False
    assert tx.calls == 1  # 401/403 must not retry


def test_webhook_signature_verify_and_rotation():
    secret = "s3cr3t"
    body = b'{"event":{"event_type":"incident.triggered"}}'
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, f"v1={good}", secret) is True
    # rotation: multiple signatures, one valid
    assert verify_signature(body, f"v1=deadbeef,v1={good}", secret) is True
    # tampered
    assert verify_signature(body, "v1=deadbeef", secret) is False
    assert verify_signature(body, None, secret) is False


def test_webhook_to_normalized_incident():
    payload = {"event": {
        "event_type": "incident.triggered",
        "occurred_at": "2026-06-15T10:00:00Z",
        "data": {"id": "PT1", "title": "High CPU on payments-api", "urgency": "high",
                 "incident_key": "k1", "created_at": "2026-06-15T09:59:00Z"},
    }}
    assert is_trigger_event(payload) is True
    ni = to_normalized_incident(payload)
    assert ni.source_system == SourceSystem.pagerduty
    assert ni.pagerduty_id == "PT1"
    assert ni.provider_severity == SeverityLevel.high
    assert ni.fingerprint == "k1"


def test_normalize_severity_priority_beats_urgency():
    ni = normalize_incident({"id": "X", "priority": {"summary": "P1"}, "urgency": "low"})
    assert ni.provider_severity == SeverityLevel.critical
    assert normalize_incident({"id": "Y", "urgency": "high"}).provider_severity == SeverityLevel.high
    assert normalize_incident({"id": "Z"}).provider_severity is None
