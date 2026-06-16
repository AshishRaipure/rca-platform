"""Knowledge Retrieval Agent — core implementation.

Pipeline:

    validate input
      -> concurrent retrieval (corpus hybrid+rerank  ||  episodic confirmed incidents)
      -> assemble citations (authoritative outcome/freshness from index metadata)
      -> [optional, default-off] read-only freshness probe of the top source
      -> if no sources: return an explicit "nothing found / unavailable" result (no LLM)
      -> grounded synthesis (mid tier; escalate to a stronger tier only on parse failure)
      -> guardrails: drop findings whose citations are not in the retrieved set; cap confidence
         for refuted/stale sources; keep conflicts; compute conservative coverage
      -> assemble KnowledgeOutput (+ metadata)

The agent is grounding-bound: it can only assert what a retrieved, in-scope source supports, and
it surfaces disagreements rather than resolving them. It degrades (never hard-fails) when
retrieval or synthesis is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

from contracts.enums import ConfidenceGrade, SourceSystem
from contracts.retrieval import DocumentOutcome, RetrievalFilters, SourceType

from agents.knowledge_retrieval._interfaces import (
    AuditSink,
    Clock,
    LLMClient,
    LLMResponse,
    MCPGateway,
    RetrieverPort,
)
from agents.knowledge_retrieval.config import KnowledgeConfig
from agents.knowledge_retrieval.errors import KnowledgeInputError, SynthesisUnavailableError
from agents.knowledge_retrieval.prompts import (
    PROMPT_VERSION,
    REPAIR_SUFFIX,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from agents.knowledge_retrieval.schemas import (
    Citation,
    KnowledgeConflict,
    KnowledgeCoverage,
    KnowledgeFinding,
    KnowledgeInput,
    KnowledgeMetadata,
    KnowledgeOutput,
    SimilarIncident,
    _LLMKnowledgeResult,
)
from agents.knowledge_retrieval.tools import KnowledgeTools

logger = logging.getLogger("agents.knowledge_retrieval")

_CONF_RANK = {
    ConfidenceGrade.speculative: 0,
    ConfidenceGrade.low: 1,
    ConfidenceGrade.medium: 2,
    ConfidenceGrade.high: 3,
}


def _min_conf(a: ConfidenceGrade, b: ConfidenceGrade) -> ConfidenceGrade:
    """The more conservative (lower) of two confidence grades."""
    return a if _CONF_RANK[a] <= _CONF_RANK[b] else b


def _similarity_grade(s: float) -> ConfidenceGrade:
    if s >= 0.85:
        return ConfidenceGrade.high
    if s >= 0.70:
        return ConfidenceGrade.medium
    if s >= 0.50:
        return ConfidenceGrade.low
    return ConfidenceGrade.speculative


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class KnowledgeRetrievalAgent:
    """Agent 2 — retrieves and synthesizes relevant organizational knowledge, fully cited."""

    AGENT_NAME = "knowledge_retrieval"

    def __init__(
        self,
        *,
        llm: LLMClient,
        retriever: RetrieverPort,
        audit: AuditSink,
        config: Optional[KnowledgeConfig] = None,
        gateway: Optional[MCPGateway] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._llm = llm
        self._retriever = retriever
        self._audit = audit
        self._config = config or KnowledgeConfig()
        self._gateway = gateway
        self._clock = clock or _SystemClock()

    # ------------------------------------------------------------------ public API

    async def run(
        self, request: KnowledgeInput, *, request_id: str, scope: Optional[dict[str, Any]] = None,
    ) -> KnowledgeOutput:
        scope = scope or {}
        if not isinstance(request, KnowledgeInput):
            raise KnowledgeInputError("request must be a KnowledgeInput")

        started = self._clock.now()
        warnings: list[str] = []
        await self._audit_event(
            "agent_output", "knowledge.started", request, request_id,
            metadata={"prompt_version": PROMPT_VERSION},
        )

        tools = KnowledgeTools(
            self._retriever, scope=scope, request_id=request_id,
            retrieval_timeout_s=self._config.retrieval_timeout_s,
            gateway=self._gateway, tool_timeout_s=self._config.tool_timeout_s,
        )

        try:
            chunks, episodic, retrieval_failed = await self._retrieve(tools, request, scope, warnings)
            citations, similar = self._build_citations(chunks, episodic)

            freshness_checked = False
            if self._gateway is not None and self._config.enable_freshness_check and citations:
                freshness_checked = await self._maybe_verify_freshness(tools, citations, warnings)

            coverage = self._compute_coverage(citations, similar)
            await self._audit_event(
                "agent_output", "knowledge.retrieved", request, request_id,
                result_summary=(
                    f"sources={len(citations)} episodic={len(similar)} failed={retrieval_failed}"
                ),
                metadata={
                    "document_ids": [c.document_id for c in citations],
                    "mcp_calls": tools.mcp_calls,
                },
            )

            # ---- no sources: do not invent; skip the LLM ----
            if not citations:
                summary = (
                    "Knowledge retrieval was unavailable for this incident."
                    if retrieval_failed
                    else "No relevant existing knowledge was found for this incident."
                )
                return await self._finish(
                    request, request_id, started, summary, [], [], similar, [], coverage,
                    resp=None, tier=None, num_retrieved=len(chunks), num_episodic=len(episodic),
                    freshness_checked=freshness_checked, degraded=retrieval_failed, warnings=warnings,
                )

            # ---- grounded synthesis ----
            user = build_user_prompt(request, citations)
            parsed, resp, tier, _escalated = await self._synthesize(
                SYSTEM_PROMPT, user, request_id, warnings,
            )

            if parsed is None:
                # retrieval-only fallback: keep the citations as raw evidence for the RCA agent
                summary = (
                    f"Synthesis was unavailable; returning {len(citations)} retrieved source(s) "
                    "as evidence."
                )
                return await self._finish(
                    request, request_id, started, summary, [], citations, similar, [], coverage,
                    resp=resp, tier=None, num_retrieved=len(chunks), num_episodic=len(episodic),
                    freshness_checked=freshness_checked, degraded=True, warnings=warnings,
                )

            findings, conflicts = self._apply_guardrails(parsed, citations, warnings)
            heuristic = coverage.retrieval_confidence
            llm_conf = parsed.retrieval_confidence or heuristic
            coverage.retrieval_confidence = _min_conf(heuristic, llm_conf)
            coverage.gaps = self._merge_gaps(parsed.gaps, coverage)
            summary = parsed.summary or "See findings."

            return await self._finish(
                request, request_id, started, summary, findings, citations, similar, conflicts,
                coverage, resp=resp, tier=tier, num_retrieved=len(chunks),
                num_episodic=len(episodic), freshness_checked=freshness_checked,
                degraded=retrieval_failed, warnings=warnings,
            )
        except Exception as exc:  # audit unexpected failures; the node converts to a soft signal
            await self._audit_event(
                "agent_output", "knowledge.failed", request, request_id,
                result_summary=f"{type(exc).__name__}: {exc}", metadata={"warnings": warnings},
            )
            raise

    # ------------------------------------------------------------- step 1: retrieval

    async def _retrieve(self, tools, request, scope, warnings):
        filters = RetrievalFilters(
            scope=scope, source_types=self._config.source_types,
            prefer_current=self._config.prefer_current,
        )
        chunks: list = []
        episodic: list = []
        retrieval_failed = False

        corpus_task = asyncio.create_task(
            tools.search_corpus(
                query=self._corpus_query(request), filters=filters,
                k=self._config.corpus_k, expand=self._config.enable_query_expansion,
            )
        )
        episodic_task = None
        if self._config.enable_episodic and self._config.episodic_k > 0:
            episodic_task = asyncio.create_task(
                tools.search_episodic(query=self._episodic_query(request), k=self._config.episodic_k)
            )

        try:
            chunks = list(await corpus_task)
        except Exception as exc:
            logger.warning("knowledge corpus retrieval failed: %s", exc)
            warnings.append("knowledge corpus retrieval unavailable")
            retrieval_failed = True

        if episodic_task is not None:
            try:
                episodic = list(await episodic_task)
            except Exception as exc:
                logger.warning("episodic retrieval failed: %s", exc)
                warnings.append("episodic retrieval unavailable")

        return chunks, episodic, retrieval_failed

    @staticmethod
    def _corpus_query(request: KnowledgeInput) -> str:
        parts = [request.incident.title]
        if request.incident.description:
            parts.append(request.incident.description)
        if request.affected_systems:
            parts.append("affected systems: " + ", ".join(request.affected_systems))
        if request.initial_hypothesis:
            parts.append("hypothesis: " + request.initial_hypothesis)
        return ". ".join(parts)

    @staticmethod
    def _episodic_query(request: KnowledgeInput) -> str:
        parts = [request.incident.title]
        if request.incident.description:
            parts.append(request.incident.description)
        if request.affected_systems:
            parts.append("systems: " + ", ".join(request.affected_systems))
        return ". ".join(parts)

    # ------------------------------------------------------ step 2: citation assembly

    def _build_citations(self, chunks, episodic) -> tuple[list[Citation], list[SimilarIncident]]:
        citations: list[Citation] = []
        similar: list[SimilarIncident] = []
        ranked = sorted(
            chunks,
            key=lambda c: (c.rerank_score if c.rerank_score is not None else c.score),
            reverse=True,
        )[: self._config.max_context_chunks]

        n = 0
        for ch in ranked:
            n += 1
            citations.append(
                Citation(
                    citation_id=f"c{n}", document_id=ch.document_id, chunk_id=ch.chunk_id,
                    source_system=ch.source_system, source_type=ch.source_type, title=ch.title,
                    uri=ch.uri, snippet=(ch.text or "")[: self._config.snippet_max_chars],
                    is_current=ch.is_current, outcome=ch.outcome,
                )
            )
        for ep in episodic:
            n += 1
            cid = f"c{n}"
            parts = [p for p in [ep.confirmed_root_cause, ep.confirmed_resolution] if p]
            citations.append(
                Citation(
                    citation_id=cid, document_id=ep.incident_id, chunk_id=None,
                    source_system=SourceSystem.servicenow, source_type=SourceType.incident,
                    title=ep.title, uri=ep.uri,
                    snippet=(" — ".join(parts))[: self._config.snippet_max_chars],
                    is_current=ep.is_current, outcome=ep.outcome,
                )
            )
            similar.append(
                SimilarIncident(
                    incident_id=ep.incident_id, title=ep.title,
                    similarity=_similarity_grade(ep.similarity),
                    confirmed_root_cause=ep.confirmed_root_cause,
                    confirmed_resolution=ep.confirmed_resolution,
                    outcome=ep.outcome, citation_id=cid,
                )
            )
        return citations, similar

    def _compute_coverage(self, citations, similar) -> KnowledgeCoverage:
        num = len(citations)
        has_runbook = any(c.source_type == SourceType.runbook for c in citations)
        has_confirmed_rca = any(
            c.source_type in (SourceType.rca, SourceType.incident)
            and c.outcome == DocumentOutcome.confirmed
            for c in citations
        )
        stale = sum(1 for c in citations if not c.is_current)
        if num == 0:
            conf = ConfidenceGrade.speculative
        elif has_confirmed_rca:
            conf = ConfidenceGrade.high
        elif num >= self._config.min_sources_for_confident:
            conf = ConfidenceGrade.medium
        else:
            conf = ConfidenceGrade.low
        return KnowledgeCoverage(
            retrieval_confidence=conf, num_sources=num, num_similar_incidents=len(similar),
            has_runbook=has_runbook, has_confirmed_rca=has_confirmed_rca, stale_sources=stale,
            gaps=[],
        )

    # ----------------------------------------------------- optional freshness probe

    async def _maybe_verify_freshness(self, tools, citations, warnings) -> bool:
        """Best-effort, read-only verification that the top source is current. Default-off."""
        tool, params = self._freshness_probe(citations[0])
        if not tool:
            return False
        try:
            res = await tools.verify_current(tool=tool, params=params)
            return bool(res is not None and getattr(res, "ok", False))
        except Exception as exc:
            logger.warning("freshness check failed: %s", exc)
            warnings.append("freshness check failed")
            return False

    @staticmethod
    def _freshness_probe(c: Citation) -> tuple[Optional[str], dict[str, Any]]:
        if c.source_system == SourceSystem.confluence:
            return "confluence.get_page", {"id": c.document_id}
        if c.source_system == SourceSystem.servicenow:
            return "servicenow.get_knowledge", {"id": c.document_id}
        return None, {}

    # ------------------------------------------------------------ step 3: synthesis

    async def _synthesize(self, system, user, request_id, warnings):
        tiers = [self._config.primary_tier]
        if (self._config.allow_escalation
                and self._config.escalation_tier != self._config.primary_tier):
            tiers.append(self._config.escalation_tier)

        last_resp: Optional[LLMResponse] = None
        last_error: Optional[Exception] = None

        for idx, tier in enumerate(tiers):
            try:
                resp = await self._call_llm(system, user, tier, request_id)
            except SynthesisUnavailableError as exc:
                last_error = exc
                continue
            last_resp = resp
            parsed = self._parse(resp.text)
            if parsed is None:
                try:
                    resp = await self._call_llm(system, user + REPAIR_SUFFIX, tier, request_id)
                    last_resp = resp
                    parsed = self._parse(resp.text)
                except SynthesisUnavailableError as exc:
                    last_error = exc
            if parsed is not None:
                return parsed, resp, tier, idx > 0
            # parse failed on this tier -> escalate to the next (stronger) tier, if any

        if last_resp is None:
            warnings.append("synthesis model unavailable; returning retrieved sources only")
        else:
            warnings.append("synthesis output unparseable; returning retrieved sources only")
        if last_error is not None:
            logger.warning("synthesis unavailable: %s", last_error)
        return None, last_resp, None, False

    async def _call_llm(self, system, user, tier, request_id) -> LLMResponse:
        last_exc: Optional[Exception] = None
        for _ in range(self._config.llm_max_attempts):
            try:
                return await self._llm.complete(
                    system=system, user=user, model_tier=tier,
                    max_tokens=self._config.llm_max_tokens,
                    temperature=self._config.llm_temperature,
                    request_id=request_id, timeout_s=self._config.llm_timeout_s,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning("knowledge LLM call failed (tier=%s): %s", tier.value, exc)
        raise SynthesisUnavailableError(
            f"LLM tier {tier.value} failed after {self._config.llm_max_attempts} attempts",
            detail=str(last_exc),
        )

    @staticmethod
    def _parse(text: str) -> Optional[_LLMKnowledgeResult]:
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
            return _LLMKnowledgeResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            return None

    # ------------------------------------------------------------- step 4: guardrails

    def _apply_guardrails(
        self, parsed: _LLMKnowledgeResult, citations: list[Citation], warnings: list[str],
    ) -> tuple[list[KnowledgeFinding], list[KnowledgeConflict]]:
        valid_ids = {c.citation_id for c in citations}
        weak_ids = {
            c.citation_id for c in citations
            if c.outcome == DocumentOutcome.refuted or not c.is_current
        }

        findings: list[KnowledgeFinding] = []
        for f in parsed.findings:
            ids = [i for i in f.citation_ids if i in valid_ids]
            if not ids:
                warnings.append(f"dropped finding with no valid citation: {f.statement[:60]!r}")
                continue
            conf = f.confidence or ConfidenceGrade.medium
            caveat = f.caveat
            if any(i in weak_ids for i in ids):
                conf = _min_conf(conf, ConfidenceGrade.low)
                caveat = (f"{caveat}; " if caveat else "") + "relies on a refuted or stale source"
            findings.append(
                KnowledgeFinding(statement=f.statement, citation_ids=ids, confidence=conf,
                                 caveat=caveat)
            )

        conflicts: list[KnowledgeConflict] = []
        for cf in parsed.conflicts:
            ids = [i for i in cf.citation_ids if i in valid_ids]
            if len(ids) < 2:
                warnings.append("dropped conflict with fewer than two valid citations")
                continue
            conflicts.append(
                KnowledgeConflict(description=cf.description, citation_ids=ids,
                                  kind=cf.kind or "guidance")
            )

        # if confirmed and refuted analyses coexist but no conflict was reported, flag for review
        if not conflicts:
            outcomes = {
                c.outcome for c in citations
                if c.source_type in (SourceType.rca, SourceType.incident) and c.outcome
            }
            if DocumentOutcome.refuted in outcomes and DocumentOutcome.confirmed in outcomes:
                warnings.append(
                    "sources include both confirmed and refuted analyses; review for conflicts"
                )

        return findings, conflicts

    def _merge_gaps(self, llm_gaps, coverage: KnowledgeCoverage) -> list[str]:
        gaps = list(llm_gaps or [])
        if not coverage.has_runbook:
            gaps.append("no runbook found for the affected systems")
        seen: set[str] = set()
        deduped: list[str] = []
        for g in gaps:
            if g not in seen:
                seen.add(g)
                deduped.append(g)
        return deduped

    # ------------------------------------------------------------------ assembly + audit

    async def _finish(
        self, request, request_id, started, summary, findings, citations, similar, conflicts,
        coverage, *, resp, tier, num_retrieved, num_episodic, freshness_checked, degraded, warnings,
    ) -> KnowledgeOutput:
        out = KnowledgeOutput(
            investigation_id=request.investigation_id,
            summary=summary,
            findings=findings,
            citations=citations,
            similar_incidents=similar,
            conflicts=conflicts,
            coverage=coverage,
            metadata=KnowledgeMetadata(
                model_id=resp.model_id if resp else "fallback:retrieval-only",
                model_version=resp.model_version if resp else None,
                prompt_version=PROMPT_VERSION,
                model_tier_used=tier.value if tier else "none",
                corpus_k=self._config.corpus_k, episodic_k=self._config.episodic_k,
                num_retrieved=num_retrieved, num_episodic=num_episodic,
                expanded=self._config.enable_query_expansion, freshness_checked=freshness_checked,
                input_tokens=resp.input_tokens if resp else 0,
                output_tokens=resp.output_tokens if resp else 0,
                latency_ms=int((self._clock.now() - started).total_seconds() * 1000),
                degraded=degraded, warnings=warnings,
            ),
        )
        await self._audit_event(
            "agent_output", "knowledge.completed", request, request_id,
            model_id=out.metadata.model_id, model_version=out.metadata.model_version,
            result_summary=(
                f"findings={len(out.findings)} conflicts={len(out.conflicts)} "
                f"retrieval_confidence={out.coverage.retrieval_confidence.value} "
                f"degraded={out.metadata.degraded}"
            ),
            metadata={"warnings": out.metadata.warnings},
        )
        return out

    async def _audit_event(self, category, action, request, request_id, **kwargs) -> None:
        try:
            await self._audit.record(
                category=category, action=action, actor_id=self.AGENT_NAME,
                investigation_id=request.investigation_id, request_id=request_id, **kwargs,
            )
        except Exception as exc:
            logger.error("knowledge audit write failed for %s: %s", action, exc)
