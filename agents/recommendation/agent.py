"""Recommendation Agent (Agent 5) — core implementation.

Turns the RCA into prioritized, risk-tagged steps. It NEVER executes anything. A structural
guardrail guarantees that every production-impacting step carries a human-approval requirement,
regardless of what the model returns. Degrades to a safe diagnostic-only recommendation if the
model is unavailable.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import ValidationError

from agents.base.interfaces import AuditSink, Clock, LLMClient
from agents.base.parsing import SystemClock, extract_json
from agents.recommendation.config import RecommendationConfig
from agents.recommendation.errors import RecommendationInputError
from agents.recommendation.prompts import REPAIR_SUFFIX, SYSTEM_PROMPT, build_user_prompt
from agents.recommendation.schemas import (
    APPROVALS,
    CATEGORIES,
    RISKS,
    RecommendationInput,
    RecommendationOutput,
    RecommendationStep,
    _LLMRecResult,
)

logger = logging.getLogger("agents.recommendation")

AGENT_NAME = "recommendation"


class RecommendationAgent:
    AGENT_NAME = AGENT_NAME

    def __init__(
        self, *, llm: LLMClient, audit: AuditSink,
        config: Optional[RecommendationConfig] = None, clock: Optional[Clock] = None,
    ) -> None:
        self._llm = llm
        self._audit = audit
        self._config = config or RecommendationConfig()
        self._clock = clock or SystemClock()
        self._last_model_id: Optional[str] = None
        self._last_model_version: Optional[str] = None

    async def run(
        self, request: RecommendationInput, *, request_id: str,
        scope: Optional[dict[str, Any]] = None,
    ) -> RecommendationOutput:
        if not isinstance(request, RecommendationInput):
            raise RecommendationInputError("request must be a RecommendationInput")
        try:
            parsed = await self._synthesize(request, request_id)
        except Exception as exc:
            logger.warning("recommendation synthesis unavailable: %s", exc)
            return self._fallback(str(exc))

        steps = [self._normalize(s) for s in parsed.steps if isinstance(s, dict) and s.get("action")]
        steps = [s for s in steps if s is not None][: self._config.max_steps]
        if not steps:
            return self._fallback("model produced no usable steps")

        out = RecommendationOutput(
            summary=parsed.summary or "", steps=steps,
            metadata={"degraded": False, "prompt_version": self._config.prompt_version,
                      "model_id": self._last_model_id,
                      "prod_impacting_steps": sum(1 for s in steps if s.prod_impacting)})
        await self._safe_audit(request, request_id, out)
        return out

    def _normalize(self, raw: dict[str, Any]) -> Optional[RecommendationStep]:
        action = (raw.get("action") or "").strip()
        if not action:
            return None
        category = raw.get("category") if raw.get("category") in CATEGORIES else "diagnostic"
        risk = raw.get("risk") if raw.get("risk") in RISKS else "low"
        prod = bool(raw.get("prod_impacting", False))
        approval = raw.get("approval_requirement")
        if approval not in APPROVALS:
            approval = "none"
        # GUARDRAIL: any prod-impacting step must require human approval; high-risk prod actions
        # require change approval too. This cannot be downgraded by the model.
        if prod and approval == "none":
            approval = "human_approval"
        if prod and risk == "high" and approval != "human_approval_and_change":
            approval = "human_approval_and_change"
        refs = [r for r in (raw.get("evidence_refs") or []) if isinstance(r, str)]
        return RecommendationStep(
            action=action, category=category, risk=risk, prod_impacting=prod,
            approval_requirement=approval, rationale=raw.get("rationale"), evidence_refs=refs)

    def _fallback(self, detail: str) -> RecommendationOutput:
        return RecommendationOutput(
            summary="Automated recommendation unavailable; proceed with safe diagnostics.",
            steps=[RecommendationStep(
                action=("Gather additional diagnostics (logs, metrics, recent changes) and engage "
                        "the on-call service owner before making any change."),
                category="diagnostic", risk="low", prod_impacting=False,
                approval_requirement="none")],
            metadata={"degraded": True, "prompt_version": self._config.prompt_version,
                      "warnings": [detail]})

    async def _synthesize(self, request: RecommendationInput, request_id: str) -> _LLMRecResult:
        user = build_user_prompt(request)
        last_exc: Optional[Exception] = None
        for attempt in range(self._config.llm_max_attempts):
            prompt = user if attempt == 0 else user + REPAIR_SUFFIX
            resp = await self._llm.complete(
                system=SYSTEM_PROMPT, user=prompt, model_tier=self._config.primary_tier,
                max_tokens=self._config.llm_max_tokens, temperature=self._config.llm_temperature,
                request_id=request_id, timeout_s=self._config.llm_timeout_s)
            self._last_model_id = getattr(resp, "model_id", None)
            self._last_model_version = getattr(resp, "model_version", None)
            try:
                return _LLMRecResult.model_validate(extract_json(resp.text))
            except (ValueError, ValidationError) as exc:
                last_exc = exc
                logger.warning("recommendation parse failed (attempt %d): %s", attempt, exc)
        raise RecommendationInputError(f"could not parse recommendation output: {last_exc}")

    async def _safe_audit(self, request, request_id, out: RecommendationOutput) -> None:
        try:
            await self._audit.record(
                category="agent_output", action="recommendation.completed", actor_id=AGENT_NAME,
                investigation_id=request.investigation_id, request_id=request_id,
                model_id=self._last_model_id, model_version=self._last_model_version,
                result_summary=(f"steps={len(out.steps)} "
                                f"prod_impacting={out.metadata.get('prod_impacting_steps', 0)}"),
                metadata={"degraded": out.metadata.get("degraded", False),
                          "prompt_version": self._config.prompt_version})
        except Exception:
            logger.warning("recommendation audit failed", exc_info=True)
