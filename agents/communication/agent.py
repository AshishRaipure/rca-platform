"""Communication Agent (Agent 6) — core implementation.

Generates draft communications and the platform's own RCA report. Draft-only by construction:
every artifact is marked status='draft' and the agent has no capability to post anywhere. If the
model is unavailable it falls back to deterministic templated drafts built from the structured
RCA/recommendations.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import ValidationError

from agents.base.interfaces import AuditSink, Clock, LLMClient
from agents.base.parsing import SystemClock, extract_json
from agents.communication.config import CommunicationConfig
from agents.communication.errors import CommunicationInputError
from agents.communication.prompts import REPAIR_SUFFIX, SYSTEM_PROMPT, build_user_prompt
from agents.communication.schemas import (
    CommunicationDraft,
    CommunicationInput,
    CommunicationOutput,
    _LLMCommResult,
)

logger = logging.getLogger("agents.communication")

AGENT_NAME = "communication"


def _top_cause(rca: dict[str, Any]) -> str:
    causes = rca.get("ranked_causes") or []
    if causes and isinstance(causes[0], dict):
        return causes[0].get("statement") or "undetermined"
    return "undetermined"


class CommunicationAgent:
    AGENT_NAME = AGENT_NAME

    def __init__(
        self, *, llm: LLMClient, audit: AuditSink,
        config: Optional[CommunicationConfig] = None, clock: Optional[Clock] = None,
    ) -> None:
        self._llm = llm
        self._audit = audit
        self._config = config or CommunicationConfig()
        self._clock = clock or SystemClock()
        self._last_model_id: Optional[str] = None
        self._last_model_version: Optional[str] = None

    async def run(
        self, request: CommunicationInput, *, request_id: str,
        scope: Optional[dict[str, Any]] = None,
    ) -> CommunicationOutput:
        if not isinstance(request, CommunicationInput):
            raise CommunicationInputError("request must be a CommunicationInput")
        try:
            parsed = await self._synthesize(request, request_id)
            degraded = False
        except Exception as exc:
            logger.warning("communication synthesis unavailable: %s", exc)
            parsed = self._template(request)
            degraded = True

        drafts = [
            CommunicationDraft(channel="slack", audience="incident_channel",
                               content=parsed.slack, status="draft"),
            CommunicationDraft(channel="servicenow_worknote", audience="incident_ticket",
                               content=parsed.worknote, status="draft"),
            CommunicationDraft(channel="exec_summary", audience="leadership",
                               content=parsed.exec_summary, status="draft"),
        ]
        # draft-only by construction: nothing here can be posted
        for d in drafts:
            d.status = "draft"
        out = CommunicationOutput(
            drafts=drafts, rca_report=parsed.rca_report, status="draft",
            metadata={"degraded": degraded, "prompt_version": self._config.prompt_version,
                      "model_id": self._last_model_id})
        await self._safe_audit(request, request_id, out)
        return out

    def _template(self, request: CommunicationInput) -> _LLMCommResult:
        title = request.incident.title
        cause = _top_cause(request.rca)
        conf = request.rca.get("overall_confidence", "unknown")
        steps = request.recommendations.get("steps") or []
        next_step = steps[0]["action"] if steps and isinstance(steps[0], dict) else "pending analysis"
        slack = (f":rotating_light: Incident: {title}. Leading hypothesis ({conf} confidence): "
                 f"{cause}. Recommended next step (pending human approval): {next_step}. "
                 f"Advisory only — under review.")
        worknote = (f"[DRAFT] Investigation summary for {title}. Leading root cause: {cause} "
                    f"(confidence: {conf}). Recommended next step pending approval: {next_step}.")
        exec_summary = (f"{title}: leading cause is {cause} (confidence {conf}). A recommended "
                        f"remediation is pending human approval. No action has been taken.")
        rca_report = (f"# RCA (draft) — {title}\n\nSeverity: {request.severity.value}\n\n"
                      f"Leading root cause ({conf}): {cause}\n\nRecommended next step (pending "
                      f"approval): {next_step}\n\nThis is an advisory draft for human review.")
        return _LLMCommResult(slack=slack, worknote=worknote, exec_summary=exec_summary,
                              rca_report=rca_report)

    async def _synthesize(self, request: CommunicationInput, request_id: str) -> _LLMCommResult:
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
                return _LLMCommResult.model_validate(extract_json(resp.text))
            except (ValueError, ValidationError) as exc:
                last_exc = exc
                logger.warning("communication parse failed (attempt %d): %s", attempt, exc)
        raise CommunicationInputError(f"could not parse communication output: {last_exc}")

    async def _safe_audit(self, request, request_id, out: CommunicationOutput) -> None:
        try:
            await self._audit.record(
                category="agent_output", action="communication.completed", actor_id=AGENT_NAME,
                investigation_id=request.investigation_id, request_id=request_id,
                model_id=self._last_model_id, model_version=self._last_model_version,
                result_summary=f"drafts={len(out.drafts)} status={out.status}",
                metadata={"degraded": out.metadata.get("degraded", False),
                          "prompt_version": self._config.prompt_version})
        except Exception:
            logger.warning("communication audit failed", exc_info=True)
