"""Unit tests for the Architecture Discovery agent (fake gateway). Run with: pytest -q."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from agents.architecture_discovery.agent import ArchitectureDiscoveryAgent
from agents.architecture_discovery.schemas import ArchitectureInput


class FakeAudit:
    async def record(self, **kw):
        return None


def _result(data):
    return SimpleNamespace(tool_call_id="t", ok=True, data=data, error=None)


class FakeGateway:
    def __init__(self, responses):
        self._r = responses
        self.calls = []

    async def call(self, *, tool, params, scope, request_id, timeout_s):
        self.calls.append((tool, params))
        return _result(self._r.get(tool, {}))


@pytest.mark.asyncio
async def test_assembles_context_from_cmdb():
    gw = FakeGateway({
        "servicenow.get_cmdb_ci": {"ci": {"sys_id": "ci1", "name": "order-api",
                                          "sys_class_name": "cmdb_ci_app_server",
                                          "environment": "prod", "install_status": "operational"}},
        "servicenow.get_ci_relationships": {"relationships": [
            {"target": "orders-db", "type": "depends_on"},
            {"target": "redis", "type": "depends_on"}]},
        "servicenow.list_change_requests": {"changes": [
            {"number": "CO-12345", "short_description": "deploy order-api v2.3.1",
             "state": "closed", "closed_at": "2026-06-16T00:00:00Z"}]},
    })
    agent = ArchitectureDiscoveryAgent(gateway=gw, audit=FakeAudit())
    out = await agent.run(
        ArchitectureInput(investigation_id=uuid.uuid4(), affected_systems=["order-api"]),
        request_id="r")
    assert out.degraded is False
    assert out.impacted and out.impacted[0].name == "order-api"
    assert {d.target for d in out.dependencies} == {"orders-db", "redis"}
    assert out.recent_changes and out.recent_changes[0].change_id == "CO-12345"


@pytest.mark.asyncio
async def test_degraded_without_gateway():
    agent = ArchitectureDiscoveryAgent(gateway=None, audit=FakeAudit())
    out = await agent.run(
        ArchitectureInput(investigation_id=uuid.uuid4(), affected_systems=["x"]), request_id="r")
    assert out.degraded is True
    assert any("gateway unavailable" in w for w in out.warnings)
