"""Unit tests for the ServiceNow MCP integration.

Fake HTTP transport, no network. These assert the read-only guarantee, dispatch and validation
(including the ``id`` param Agent 2 sends to ``servicenow.get_knowledge``), pagination/caps, error
mapping, and normalization across ServiceNow's display-value field forms.

Run with: pytest -q  (requires pytest, pytest-asyncio, pydantic v2).
"""
from __future__ import annotations

import pytest

from contracts.enums import SeverityLevel, SourceSystem
from mcp_connectors.servers.servicenow.client import ServiceNowClient
from mcp_connectors.servers.servicenow.config import ServiceNowConfig
from mcp_connectors.servers.servicenow.normalize import normalize_incident
from mcp_connectors.servers.servicenow.server import ServiceNowMCPServer
from mcp_connectors.servers.servicenow.tools import TOOL_SPECS


# --------------------------------------------------------------------------- fakes

class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = ""

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
    config = config or ServiceNowConfig()
    client = ServiceNowClient(
        transport, instance_url="https://acme.service-now.com",
        authorization="Bearer test", config=config,
    )
    return ServiceNowMCPServer(client, config=config), client


def _dv(value, display=None):
    """display_value=all field form."""
    return {"value": value, "display_value": display if display is not None else value}


def _incident_record(sys_id="abc", number="INC0001", priority="1"):
    return {
        "sys_id": _dv(sys_id), "number": _dv(number),
        "short_description": _dv("High CPU on payments-api"),
        "description": _dv("CPU > 95% sustained"),
        "state": _dv("2", "In Progress"),
        "priority": _dv(priority, f"{priority} - Critical"),
        "urgency": _dv("1", "1 - High"),
        "cmdb_ci": _dv("ci123", "payments-api"),
        "opened_at": _dv("2026-06-15 09:59:00"),
    }


# -------------------------------------------------------------------------- tests

def test_only_read_only_tools_are_registered():
    assert TOOL_SPECS and all(not s.mutates for s in TOOL_SPECS)
    write_verbs = ("create", "update", "delete", "close", "resolve", "insert", "patch", "put", "post")
    for s in TOOL_SPECS:
        assert not any(v in s.name.lower() for v in write_verbs), s.name
    _, client = _server(FakeTransport())
    for method in ("post", "put", "delete", "patch"):
        assert not hasattr(client, method)


def test_list_tools_reports_read_only():
    server, _ = _server(FakeTransport())
    tools = server.list_tools()
    assert len(tools) == len(TOOL_SPECS)
    assert all(t["read_only"] for t in tools)


@pytest.mark.asyncio
async def test_get_incident_by_sys_id():
    tx = FakeTransport(queue=[FakeResponse(200, {"result": _incident_record()})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="servicenow.get_incident",
                                 params={"sys_id": "abc"}, request_id="r")
    assert res.ok is True
    assert res.data["incident"]["sys_id"] == "abc"
    assert res.data["incident"]["priority_label"] == "1 - Critical"


@pytest.mark.asyncio
async def test_get_incident_by_number():
    def responder(url, params):
        assert "number=INC0001" in params.get("sysparm_query", "")
        return FakeResponse(200, {"result": [_incident_record()]})

    server, _ = _server(FakeTransport(responder=responder))
    res = await server.call_tool(tool="servicenow.get_incident",
                                 params={"number": "INC0001"}, request_id="r")
    assert res.ok is True
    assert res.data["incident"]["number"] == "INC0001"


@pytest.mark.asyncio
async def test_get_incident_requires_identifier():
    server, _ = _server(FakeTransport())
    res = await server.call_tool(tool="servicenow.get_incident", params={}, request_id="r")
    assert res.ok is False
    assert "invalid params" in res.error


@pytest.mark.asyncio
async def test_get_knowledge_accepts_id_param():
    # Agent 2's freshness probe calls servicenow.get_knowledge with {"id": <sys_id>}
    kb = {"sys_id": "kb1", "number": "KB0001", "short_description": "Restart guide",
          "text": "steps", "workflow_state": "published", "sys_updated_on": "2026-01-01 00:00:00"}
    tx = FakeTransport(queue=[FakeResponse(200, {"result": kb})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="servicenow.get_knowledge",
                                 params={"id": "kb1"}, request_id="r")
    assert res.ok is True
    assert res.data["article"]["sys_id"] == "kb1"


@pytest.mark.asyncio
async def test_unknown_tool_rejected():
    server, _ = _server(FakeTransport())
    res = await server.call_tool(tool="servicenow.close_incident", params={}, request_id="r")
    assert res.ok is False
    assert "unknown tool" in res.error


@pytest.mark.asyncio
async def test_pagination_stops_on_short_page():
    def responder(url, params):
        if int(params.get("sysparm_offset", 0)) == 0:
            return FakeResponse(200, {"result": [_incident_record("a"), _incident_record("b")]})
        return FakeResponse(200, {"result": [_incident_record("c")]})

    server, _ = _server(FakeTransport(responder=responder), ServiceNowConfig(page_limit=2))
    res = await server.call_tool(tool="servicenow.list_incidents", params={}, request_id="r")
    assert res.ok is True
    assert res.data["count"] == 3


@pytest.mark.asyncio
async def test_pagination_respects_max_items():
    def responder(url, params):  # always a full page -> would never stop on its own
        return FakeResponse(200, {"result": [_incident_record("a"), _incident_record("b")]})

    cfg = ServiceNowConfig(page_limit=2, max_items=3)
    server, _ = _server(FakeTransport(responder=responder), cfg)
    res = await server.call_tool(tool="servicenow.list_incidents", params={}, request_id="r")
    assert res.data["count"] == 3


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds():
    tx = FakeTransport(queue=[
        FakeResponse(429, {}, headers={"Retry-After": "0"}),
        FakeResponse(200, {"result": _incident_record()}),
    ])
    server, _ = _server(tx, ServiceNowConfig(backoff_base_s=0.0, max_retries=3))
    res = await server.call_tool(tool="servicenow.get_incident",
                                 params={"sys_id": "abc"}, request_id="r")
    assert res.ok is True
    assert tx.calls == 2


@pytest.mark.asyncio
async def test_not_found_by_sys_id():
    tx = FakeTransport(queue=[FakeResponse(404, {})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="servicenow.get_incident",
                                 params={"sys_id": "missing"}, request_id="r")
    assert res.ok is False
    assert "not found" in res.error.lower()


@pytest.mark.asyncio
async def test_not_found_by_number_empty_result():
    def responder(url, params):
        return FakeResponse(200, {"result": []})

    server, _ = _server(FakeTransport(responder=responder))
    res = await server.call_tool(tool="servicenow.get_incident",
                                 params={"number": "INC9999"}, request_id="r")
    assert res.ok is False
    assert "not found" in res.error.lower()


@pytest.mark.asyncio
async def test_auth_error_is_not_retried():
    tx = FakeTransport(queue=[FakeResponse(403, {})])
    server, _ = _server(tx, ServiceNowConfig(max_retries=3))
    res = await server.call_tool(tool="servicenow.get_incident",
                                 params={"sys_id": "abc"}, request_id="r")
    assert res.ok is False
    assert tx.calls == 1


@pytest.mark.asyncio
async def test_ci_relationships_dispatch():
    rel = {"parent": _dv("ci1", "payments-api"), "child": _dv("ci2", "payments-db"),
           "type": _dv("rel", "Depends on")}
    tx = FakeTransport(queue=[FakeResponse(200, {"result": [rel]})])
    server, _ = _server(tx)
    res = await server.call_tool(tool="servicenow.get_ci_relationships",
                                 params={"ci_sys_id": "ci1"}, request_id="r")
    assert res.ok is True
    assert res.data["count"] == 1
    assert res.data["relationships"][0]["child"] == "payments-db"


def test_normalize_incident_severity_and_id():
    ni = normalize_incident(_incident_record(sys_id="sys-9", priority="1"))
    assert ni.source_system == SourceSystem.servicenow
    assert ni.servicenow_id == "sys-9"
    assert ni.provider_severity == SeverityLevel.critical
    # priority 3 -> medium
    assert normalize_incident(_incident_record(priority="3")).provider_severity == SeverityLevel.medium
