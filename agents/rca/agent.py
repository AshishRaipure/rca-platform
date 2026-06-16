"""Root Cause Analysis Agent (Agent 4) — core implementation.

Correlates the intake classification, the retrieved knowledge, the architecture/recent-change
context, and any evidence into a ranked, evidence-referenced set of root causes with a graded
confidence and explicit alternatives. It is advisory only and never executes anything. If the
model is unavailable or unparseable, it degrades to an explicit low-confidence result so the
confidence gate escalates to a human.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import ValidationError

from contracts.enums import ConfidenceGrade

from agents.base.interfaces import AuditSink, Clock, LLMClient
from agents.base.parsing import CONF_RANK, SystemClock, extract_json, grade_from_str, min_conf
from agents.rca.config import RcaConfig
from agents.rca.errors import RcaInputError, RcaUnavailableError
from agents.rca.prompts import REPAIR_SUFFIX, SYSTEM_PROMPT, build_user_prompt
from agents.rca.schemas import RcaInput, RcaOutput, RootCause, _LLMRcaResult

logger = logging.getLogger("agents.rca")

AGENT_NAME = "rca"


class RootCauseAnalysisAgent:
    AGENT_NAME = AGENT_NAME

    def __init__(
        self, *, llm: LLMClient, audit: AuditSink,
        config: Optional[RcaConfig] = None, clock: Optional[Clock] = None,
    ) -> None:
        self._llm = llm
        self._audit = audit
        self._config = config or RcaConfig()
        self._clock = clock or SystemClock()
        self._last_model_id: Optional[str] = None
        self._last_model_version: Optional[str] = None

    async def run(
        self, request: RcaInput, *, request_id: str, scope: Optional[dict[str, Any]] = None,
    ) -> RcaOutput:
        if not isinstance(request, RcaInput):
            raise RcaInputError("request must be an RcaInput")

        valid_refs = {
            c.get("citation_id") for c in request.citations
            if isinstance(c, dict) and c.get("citation_id")
        }
        valid_refs |= {
            e.get("id") for e in request.evidence if isinstance(e, dict) and e.get("id")
        }

        try:
            parsed = await self._synthesize(request, request_id)
        except Exception as exc:  # LLM down or unparseable -> degrade (gate will escalate)
            logger.warning("rca synthesis unavailable: %s", exc)
            return self._fallback(request, request_id, str(exc))

        causes = self._build(parsed.ranked_causes, valid_refs)[: self._config.max_causes]
        alternatives = self._build(parsed.alternatives, valid_refs)
        if not causes:
            return self._fallback(request, request_id, "model produced no usable causes")

        overall = grade_from_str(parsed.overall_confidence)
        strongest = max(causes, key=lambda c: CONF_RANK[c.confidence]).confidence
        overall = min_conf(overall, strongest)  # never claim more confidence than the best cause

        out = RcaOutput(
            summary=parsed.summary or "", ranked_causes=causes, alternatives=alternatives,
            overall_confidence=overall,
            metadata={"degraded": False, "prompt_version": self._config.prompt_version,
                      "model_id": self._last_model_id, "model_version": self._last_model_version},
        )
        await self._safe_audit(request, request_id, out)
        return out

    def _build(self, raw: list[dict[str, Any]], valid_refs: set) -> list[RootCause]:
        causes: list[RootCause] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            stmt = (item.get("statement") or "").strip()
            if not stmt:
                continue
            refs = [r for r in (item.get("evidence_refs") or []) if isinstance(r, str)]
            if valid_refs:
                refs = [r for r in refs if r in valid_refs]  # drop ungrounded references
            conf = grade_from_str(item.get("confidence"))
            if not refs and conf in (ConfidenceGrade.high, ConfidenceGrade.medium):
                conf = ConfidenceGrade.low  # no grounded evidence -> cap confidence
            causes.append(RootCause(
                statement=stmt, confidence=conf, evidence_refs=refs,
                rationale=item.get("rationale"), category=item.get("category")))
        return causes

    def _fallback(self, request: RcaInput, request_id: str, detail: str) -> RcaOutput:
        out = RcaOutput(
            summary=("Insufficient or unavailable analysis to determine a confident root cause. "
                     "Human investigation is required."),
            ranked_causes=[RootCause(
                statement="Root cause undetermined by automated analysis.",
                confidence=ConfidenceGrade.speculative, evidence_refs=[],
                rationale="RCA synthesis did not yield a grounded result.")],
            alternatives=[],
            overall_confidence=ConfidenceGrade.speculative,
            metadata={"degraded": True, "prompt_version": self._config.prompt_version,
                      "warnings": [detail]},
        )
        return out

    async def _synthesize(self, request: RcaInput, request_id: str) -> _LLMRcaResult:
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
                return _LLMRcaResult.model_validate(extract_json(resp.text))
            except (ValueError, ValidationError) as exc:
                last_exc = exc
                logger.warning("rca parse failed (attempt %d): %s", attempt, exc)
        raise RcaUnavailableError(f"could not parse RCA output: {last_exc}")

    async def _safe_audit(self, request: RcaInput, request_id: str, out: RcaOutput) -> None:
        try:
            await self._audit.record(
                category="agent_output", action="rca.completed", actor_id=AGENT_NAME,
                investigation_id=request.investigation_id, request_id=request_id,
                model_id=self._last_model_id, model_version=self._last_model_version,
                result_summary=(f"causes={len(out.ranked_causes)} "
                                f"confidence={out.overall_confidence.value}"),
                metadata={"degraded": out.metadata.get("degraded", False),
                          "prompt_version": self._config.prompt_version})
        except Exception:
            logger.warning("rca audit failed", exc_info=True)
