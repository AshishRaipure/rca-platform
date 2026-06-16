"""Unit tests for the Recommendation agent (fake LLM). Run with: pytest -q."""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from contracts.enums import SeverityLevel
from agents.recommendation.agent import RecommendationAgent
from agents.recommendation.schemas import RecommendationInput


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
    return RecommendationInput(
        investigation_id=uuid.uuid4(), severity=SeverityLevel.high,
        rca={"summary": "regression", "overall_confidence": "high"})


@pytest.mark.asyncio
async def test_prod_impacting_step_forced_to_require_approval():
    text = json.dumps({"summary": "fix", "steps": [
        {"action": "Roll back order-api to v2.3.0", "category": "mitigation", "risk": "medium",
         "prod_impacting": True, "approval_requirement": "none"}]})
    agent = RecommendationAgent(llm=FakeLLM(text=text), audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    step = out.steps[0]
    assert step.prod_impacting is True
    assert step.approval_requirement == "human_approval"   # guardrail overrode "none"


@pytest.mark.asyncio
async def test_high_risk_prod_requires_change_approval():
    text = json.dumps({"summary": "fix", "steps": [
        {"action": "Restart the prod cluster", "category": "mitigation", "risk": "high",
         "prod_impacting": True, "approval_requirement": "human_approval"}]})
    agent = RecommendationAgent(llm=FakeLLM(text=text), audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    assert out.steps[0].approval_requirement == "human_approval_and_change"


@pytest.mark.asyncio
async def test_llm_unavailable_yields_safe_diagnostic():
    agent = RecommendationAgent(llm=FakeLLM(raise_exc=RuntimeError("down")), audit=FakeAudit())
    out = await agent.run(_request(), request_id="r")
    assert out.metadata["degraded"] is True
    assert out.steps and out.steps[0].prod_impacting is False
    assert out.steps[0].approval_requirement == "none"
