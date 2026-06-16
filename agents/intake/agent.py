"""Incident Intake Agent — core implementation.

Pipeline (all steps audited; see Workflow in the implementation guide):

    validate input
      -> read-only enrichment (bounded; failures are non-fatal)
      -> LLM classification (fast tier, escalate to a stronger tier on ambiguity)
      -> parse + repair (deterministic fallback if still unparseable)
      -> guardrails (severity floor, no invented systems, grounded hypothesis, triage clamp)
      -> assemble IntakeOutput (+ metadata)

Safety is enforced in code, not merely requested in the prompt: the agent can only call
read-only tools, it never lowers the provider's severity, it never recommends dropping a serious
incident, and it drops affected systems it cannot ground in evidence or the service catalog.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

from contracts.enums import ConfidenceGrade, ModelTier, TriageDecision, is_serious, more_severe
from contracts.models import Provenance

from agents.intake._interfaces import (
    AuditSink,
    Clock,
    LLMClient,
    LLMResponse,
    MCPGateway,
    ServiceCatalogPort,
)
from agents.intake.config import IntakeConfig
from agents.intake.errors import IntakeError, IntakeInputError, LLMUnavailableError
from agents.intake.prompts import (
    PROMPT_VERSION,
    REPAIR_SUFFIX,
    SYSTEM_PROMPT,
    build_enrichment_context,
    build_user_prompt,
)
from agents.intake.schemas import (
    AffectedSystem,
    IncidentClassification,
    InitialHypothesis,
    IntakeInput,
    IntakeMetadata,
    IntakeOutput,
    _LLMIntakeResult,
)
from agents.intake.tools import IntakeTools

logger = logging.getLogger("agents.intake")


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class IncidentIntakeAgent:
    """Agent 1 — turns a raw incident into a conservative, advisory first-pass classification."""

    AGENT_NAME = "incident_intake"

    def __init__(
        self,
        *,
        llm: LLMClient,
        gateway: MCPGateway,
        audit: AuditSink,
        config: Optional[IntakeConfig] = None,
        catalog: Optional[ServiceCatalogPort] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._llm = llm
        self._gateway = gateway
        self._audit = audit
        self._config = config or IntakeConfig()
        self._catalog = catalog
        self._clock = clock or _SystemClock()

    # ------------------------------------------------------------------ public API

    async def run(
        self,
        request: IntakeInput,
        *,
        request_id: str,
        scope: Optional[dict[str, Any]] = None,
    ) -> IntakeOutput:
        scope = scope or {}
        if not isinstance(request, IntakeInput):
            raise IntakeInputError("request must be an IntakeInput")

        started = self._clock.now()
        warnings: list[str] = []
        await self._audit_event(
            "agent_output", "intake.started", request, request_id,
            metadata={"prompt_version": PROMPT_VERSION},
        )

        tools = IntakeTools(
            self._gateway, scope=scope, request_id=request_id,
            timeout_s=self._config.tool_timeout_s,
        )

        try:
            enrichment = await self._enrich(request, tools, warnings)
            llm_result, resp, tier_used, escalated = await self._classify(
                request, enrichment, request_id, warnings,
            )
            output = await self._apply_guardrails(request, llm_result, scope, warnings)
        except IntakeError as exc:
            await self._audit_event(
                "agent_output", "intake.failed", request, request_id,
                result_summary=f"{type(exc).__name__}: {exc}",
                metadata={"warnings": warnings, "tool_calls": tools.calls},
            )
            raise

        latency_ms = int((self._clock.now() - started).total_seconds() * 1000)
        output.metadata = IntakeMetadata(
            model_id=resp.model_id if resp else "fallback:deterministic",
            model_version=resp.model_version if resp else None,
            prompt_version=PROMPT_VERSION,
            model_tier_used=tier_used.value if tier_used else "none",
            escalated=escalated,
            input_tokens=resp.input_tokens if resp else 0,
            output_tokens=resp.output_tokens if resp else 0,
            latency_ms=latency_ms,
            tool_calls=list(tools.calls),
            degraded=resp is None,
            warnings=warnings,
        )
        await self._audit_event(
            "agent_output", "intake.completed", request, request_id,
            model_id=output.metadata.model_id, model_version=output.metadata.model_version,
            result_summary=(
                f"severity={output.classification.suggested_severity.value} "
                f"systems={len(output.affected_systems)} "
                f"triage={output.recommended_triage.value} "
                f"degraded={output.metadata.degraded}"
            ),
            metadata={"warnings": warnings, "tool_calls": tools.calls},
        )
        return output

    # ----------------------------------------------------------- step 1: enrichment

    async def _enrich(
        self, request: IntakeInput, tools: IntakeTools, warnings: list[str],
    ) -> dict[str, Any]:
        """Optionally pull fuller, read-only detail. Any failure is non-fatal."""
        if self._config.max_tool_calls <= 0:
            return {}
        enrichment: dict[str, Any] = {}
        inc = request.incident
        try:
            if inc.pagerduty_id and len(tools.calls) < self._config.max_tool_calls:
                r = await tools.pagerduty_incident(inc.pagerduty_id)
                if getattr(r, "ok", False):
                    enrichment["pagerduty"] = r.data
                else:
                    warnings.append("pagerduty enrichment unavailable")
            if inc.servicenow_id and len(tools.calls) < self._config.max_tool_calls:
                r = await tools.servicenow_incident(inc.servicenow_id)
                if getattr(r, "ok", False):
                    enrichment["servicenow"] = r.data
                else:
                    warnings.append("servicenow enrichment unavailable")
        except Exception as exc:  # tool/gateway failure must never fail intake
            logger.warning("intake enrichment failed: %s", exc)
            warnings.append("enrichment failed; proceeding with the alert payload only")
        return enrichment

    # -------------------------------------------------------- step 2: classification

    async def _classify(
        self, request: IntakeInput, enrichment: dict[str, Any], request_id: str,
        warnings: list[str],
    ) -> tuple[Optional[_LLMIntakeResult], Optional[LLMResponse], Optional[ModelTier], bool]:
        system = SYSTEM_PROMPT
        user = build_user_prompt(request) + build_enrichment_context(enrichment)

        tiers = [self._config.primary_tier]
        if (self._config.allow_escalation
                and self._config.escalation_tier != self._config.primary_tier):
            tiers.append(self._config.escalation_tier)

        candidate: Optional[_LLMIntakeResult] = None
        candidate_resp: Optional[LLMResponse] = None
        candidate_tier: Optional[ModelTier] = None
        last_resp: Optional[LLMResponse] = None
        last_error: Optional[Exception] = None

        for idx, tier in enumerate(tiers):
            try:
                resp = await self._call_llm(system, user, tier, request_id)
            except LLMUnavailableError as exc:
                last_error = exc
                continue
            last_resp = resp
            parsed = self._parse(resp.text)
            if parsed is None:
                # one in-tier repair attempt
                try:
                    resp = await self._call_llm(system, user + REPAIR_SUFFIX, tier, request_id)
                    last_resp = resp
                    parsed = self._parse(resp.text)
                except LLMUnavailableError as exc:
                    last_error = exc
            if parsed is None:
                continue
            if not parsed.ambiguous:
                return parsed, resp, tier, idx > 0
            # ambiguous: keep as a fallback candidate and try the next (stronger) tier
            candidate, candidate_resp, candidate_tier = parsed, resp, tier

        if candidate is not None:
            warnings.append("intake classification was ambiguous; lower confidence applied")
            return candidate, candidate_resp, candidate_tier, candidate_tier != tiers[0]

        if last_resp is None:
            # the model could not be reached at all -> the node escalates to a human
            raise LLMUnavailableError(
                "intake LLM unavailable",
                detail=str(last_error) if last_error else None,
            )
        warnings.append("intake output unparseable; deterministic fallback applied")
        return None, None, None, False

    async def _call_llm(
        self, system: str, user: str, tier: ModelTier, request_id: str,
    ) -> LLMResponse:
        last_exc: Optional[Exception] = None
        for _ in range(self._config.llm_max_attempts):
            try:
                return await self._llm.complete(
                    system=system, user=user, model_tier=tier,
                    max_tokens=self._config.llm_max_tokens,
                    temperature=self._config.llm_temperature,
                    request_id=request_id, timeout_s=self._config.llm_timeout_s,
                )
            except Exception as exc:  # retry transient failures, then surface a typed error
                last_exc = exc
                logger.warning("intake LLM call failed (tier=%s): %s", tier.value, exc)
        raise LLMUnavailableError(
            f"LLM tier {tier.value} failed after {self._config.llm_max_attempts} attempts",
            detail=str(last_exc),
        )

    @staticmethod
    def _parse(text: str) -> Optional[_LLMIntakeResult]:
        """Tolerant parse: strip code fences, extract the outermost JSON object, validate shape."""
        raw = (text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw[:4].lower() == "json":
                raw = raw[4:]
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
            return _LLMIntakeResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            return None

    # ------------------------------------------------------------- step 3: guardrails

    async def _apply_guardrails(
        self, request: IntakeInput, llm_result: Optional[_LLMIntakeResult],
        scope: dict[str, Any], warnings: list[str],
    ) -> IntakeOutput:
        inc = request.incident
        if llm_result is None:
            return self._deterministic_fallback(request, warnings)

        # --- severity: provider is a floor; never under-rate; default if unknown ---
        provider = inc.provider_severity
        model_sev = llm_result.severity_guess
        if model_sev is not None and llm_result.severity_certain:
            suggested = more_severe(provider, model_sev)
            source = "derived" if (provider is None or suggested == model_sev) else "provider"
            conf = ConfidenceGrade.high
        elif provider is not None:
            suggested = provider
            source = "provider"
            conf = ConfidenceGrade.medium
        else:
            suggested = self._config.default_severity
            source = "default"
            conf = ConfidenceGrade.low
            warnings.append("severity defaulted conservatively (no provider/derived severity)")
        # hard floor: never below the provider's severity
        if provider is not None and more_severe(provider, suggested) != suggested:
            suggested = provider
            warnings.append("severity raised back to provider floor (never under-rate)")

        classification = IncidentClassification(
            suggested_severity=suggested,
            severity_rationale=(llm_result.severity_rationale or "derived from incident data"),
            severity_confidence=conf,
            severity_source=source,
            is_advisory=True,
        )

        # --- affected systems: must be grounded; validate against catalog; drop invented ---
        affected = await self._validate_systems(inc, llm_result, scope, warnings)

        # --- hypothesis: ground it and keep it preliminary ---
        hyp_evidence: list[Provenance] = []
        if llm_result.hypothesis_evidence_quote:
            hyp_evidence.append(
                Provenance(source="incident_data", detail=llm_result.hypothesis_evidence_quote)
            )
        hypothesis = InitialHypothesis(
            statement=(
                llm_result.hypothesis_statement
                or "Insufficient information for a preliminary hypothesis."
            ),
            confidence=ConfidenceGrade.medium if hyp_evidence else ConfidenceGrade.low,
            evidence=hyp_evidence,
            is_preliminary=True,
        )

        # --- triage: clamp; never drop a serious incident ---
        triage = llm_result.recommended_triage or TriageDecision.full
        if triage == TriageDecision.drop and is_serious(suggested):
            triage = self._config.min_triage_for_serious
            warnings.append("triage upgraded from 'drop': incident is serious")

        return IntakeOutput(
            investigation_id=request.investigation_id,
            classification=classification,
            affected_systems=affected,
            initial_hypothesis=hypothesis,
            recommended_triage=triage,
            metadata=self._placeholder_metadata(),  # replaced by run()
        )

    async def _validate_systems(
        self, inc: Any, llm_result: _LLMIntakeResult, scope: dict[str, Any],
        warnings: list[str],
    ) -> list[AffectedSystem]:
        out: list[AffectedSystem] = []
        haystack = " ".join(
            filter(None, [inc.title, inc.description or "",
                          json.dumps(inc.raw_payload, default=str)])
        ).lower()
        for s in llm_result.affected_systems:
            name = s.name.strip()
            if not name:
                continue
            grounded = (
                (bool(s.evidence_quote) and s.evidence_quote.lower() in haystack)
                or (name.lower() in haystack)
            )
            service_id = None
            confirmed = False
            if self._config.enable_catalog_validation and self._catalog is not None:
                try:
                    service_id = await self._catalog.resolve(name, scope)
                    confirmed = service_id is not None
                except Exception as exc:  # catalog issues never break intake
                    logger.warning("catalog validation failed for %s: %s", name, exc)
            if not grounded and not confirmed:
                warnings.append(f"dropped ungrounded affected system: {name!r}")
                continue
            evidence = [
                Provenance(
                    source="incident_data",
                    detail=s.evidence_quote or f"name {name!r} present in incident text",
                )
            ]
            if confirmed:
                evidence.append(Provenance(source="service_catalog", detail="matched known service"))
            if confirmed and grounded:
                conf = ConfidenceGrade.high
            elif confirmed or grounded:
                conf = ConfidenceGrade.medium
            else:
                conf = ConfidenceGrade.low
            out.append(
                AffectedSystem(
                    name=name, service_id=service_id, evidence=evidence,
                    confirmed_in_catalog=confirmed, confidence=conf,
                )
            )
        return out

    def _deterministic_fallback(
        self, request: IntakeInput, warnings: list[str],
    ) -> IntakeOutput:
        """Safe, model-free classification used when the LLM is unparseable."""
        inc = request.incident
        suggested = inc.provider_severity or self._config.default_severity
        source = "provider" if inc.provider_severity else "default"
        classification = IncidentClassification(
            suggested_severity=suggested,
            severity_rationale="LLM unavailable/unparseable; deterministic fallback applied.",
            severity_confidence=ConfidenceGrade.low,
            severity_source=source,
            is_advisory=True,
        )
        hypothesis = InitialHypothesis(
            statement="Automated classification was degraded; manual triage recommended.",
            confidence=ConfidenceGrade.speculative,
            evidence=[],
            is_preliminary=True,
        )
        triage = TriageDecision.full if is_serious(suggested) else TriageDecision.lite
        return IntakeOutput(
            investigation_id=request.investigation_id,
            classification=classification,
            affected_systems=[],
            initial_hypothesis=hypothesis,
            recommended_triage=triage,
            metadata=self._placeholder_metadata(),
        )

    @staticmethod
    def _placeholder_metadata() -> IntakeMetadata:
        return IntakeMetadata(model_id="", prompt_version=PROMPT_VERSION, model_tier_used="")

    # ----------------------------------------------------------------- audit helper

    async def _audit_event(
        self, category: str, action: str, request: IntakeInput, request_id: str, **kwargs: Any,
    ) -> None:
        try:
            await self._audit.record(
                category=category, action=action, actor_id=self.AGENT_NAME,
                investigation_id=request.investigation_id, request_id=request_id, **kwargs,
            )
        except Exception as exc:  # audit must not break the agent, but log loudly
            logger.error("intake audit write failed for %s: %s", action, exc)
