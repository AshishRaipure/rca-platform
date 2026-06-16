"""Unit tests for the RCA agent (fake LLM). Run with: pytest -q."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from contracts.enums import SeverityLevel, SourceSystem
from contracts.models import NormalizedIncident
from agents.rca.agent import RootCauseAnalysisAgent
from agents.rca.schemas import RcaInput


class FakeAudit:
    async def record(self, **kw):
        return None


class FakeLLM:
    def __init__(self, text=None, raise_exc=None):
        self._text = text
        self._raise = raise_exc

    async def complete(self, **kw):
        if self._raise:
            raise self._raise
        return SimpleNamespace(text=self._text, model_id="m-top", model_version=None,
                               input_tokens=10, output_tokens=20, latency_ms=5)


def _incident():
    return NormalizedIncident(
        incident_id=uuid.uuid4(), source_system=SourceSystem.pagerduty, fingerprint="fp",
        title="High CPU on prod-app-07", provider_severity=SeverityLevel.high,
        created_at=datetime.now(timezone.utc))


def _request():
    return RcaInput(
        investigation_id=uuid.uuid4(), incident=_incident(), severity=SeverityLevel.high,
        classification={"suggested_severity": "high"}, affected_systems=["order-api"],
        citations=[{"citation_id": "c1", "title": "INC-04821"}],
        architecture_context={"recent_changes": [{"change_id": "CO-12345"}]})


@pytest.mark.asyncio
async def test_parses_ranked_causes_and_caps_confidence():
    text = json.dumps({
        "summary": "Regression after deploy.",
        "ranked_causes": [{"statement": "v2.3.1 leak", "confidence": "high",
                           "evidence_refs": ["c1", "bogus"], "rationale": "temporal correlation"}],
        "alternatives": [{"statement": "downstream slowness", "confidence": "low",
                          "evidence_refs": []}],
        "overall_confidence": "high"})
    agent = RootCauseAnalysisAgent(llm=FakeLLM(text=text), audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    assert out.ranked_causes[0].statement == "v2.3.1 leak"
    assert out.ranked_causes[0].evidence_refs == ["c1"]   # ungrounded "bogus" dropped
    assert out.overall_confidence.value == "high"
    assert out.alternatives                              # alternatives preserved
    assert out.metadata["degraded"] is False


@pytest.mark.asyncio
async def test_llm_unavailable_degrades_to_speculative():
    agent = RootCauseAnalysisAgent(llm=FakeLLM(raise_exc=RuntimeError("bedrock down")),
                                   audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    assert out.overall_confidence.value == "speculative"
    assert out.metadata["degraded"] is True


@pytest.mark.asyncio
async def test_ungrounded_cause_confidence_is_capped():
    text = json.dumps({
        "summary": "x", "ranked_causes": [{"statement": "guess", "confidence": "high",
                                           "evidence_refs": []}],
        "alternatives": [], "overall_confidence": "high"})
    agent = RootCauseAnalysisAgent(llm=FakeLLM(text=text), audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    assert out.ranked_causes[0].confidence.value == "low"   # no evidence -> capped
    assert out.overall_confidence.value == "low"
