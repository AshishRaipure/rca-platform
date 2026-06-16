"""Unit tests for the Communication agent (fake LLM). Run with: pytest -q."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from contracts.enums import SeverityLevel, SourceSystem
from contracts.models import NormalizedIncident
from agents.communication.agent import CommunicationAgent
from agents.communication.schemas import CommunicationInput


class FakeAudit:
    async def record(self, **kw):
        return None


class FakeLLM:
    def __init__(self, text=None, raise_exc=None):
        self._text, self._raise = text, raise_exc

    async def complete(self, **kw):
        if self._raise:
            raise self._raise
        return SimpleNamespace(text=self._text, model_id="m-mid", model_version=None,
                               input_tokens=5, output_tokens=10, latency_ms=3)


def _request():
    return CommunicationInput(
        investigation_id=uuid.uuid4(),
        incident=NormalizedIncident(
            incident_id=uuid.uuid4(), source_system=SourceSystem.pagerduty, fingerprint="fp",
            title="High CPU on prod-app-07", provider_severity=SeverityLevel.high,
            created_at=datetime.now(timezone.utc)),
        severity=SeverityLevel.high,
        rca={"overall_confidence": "high",
             "ranked_causes": [{"statement": "v2.3.1 leak"}]},
        recommendations={"steps": [{"action": "Roll back to v2.3.0"}]})


@pytest.mark.asyncio
async def test_drafts_are_draft_status():
    text = json.dumps({"slack": "update", "worknote": "note", "exec_summary": "summary",
                       "rca_report": "# RCA"})
    agent = CommunicationAgent(llm=FakeLLM(text=text), audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    assert out.status == "draft"
    assert {d.channel for d in out.drafts} == {"slack", "servicenow_worknote", "exec_summary"}
    assert all(d.status == "draft" for d in out.drafts)
    assert out.rca_report


@pytest.mark.asyncio
async def test_llm_unavailable_uses_template_drafts():
    agent = CommunicationAgent(llm=FakeLLM(raise_exc=RuntimeError("down")), audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    assert out.metadata["degraded"] is True
    assert out.status == "draft"
    # templated content is grounded in the structured RCA
    assert any("v2.3.1 leak" in d.content for d in out.drafts)
    assert "v2.3.1 leak" in out.rca_report
