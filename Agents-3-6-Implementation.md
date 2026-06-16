# Agents 3–6 — Implementation Notes

Covers the four analytical agents added to complete the six-agent workflow: **Architecture
Discovery (3)**, **RCA (4)**, **Recommendation (5)**, and **Communication (6)**. They follow the
same conventions as Agents 1–2: dependency-inverted (LLM / MCP gateway / audit injected as
Protocols), parse → repair → deterministic fallback, and a node wrapper that degrades instead of
crashing the graph.

## Validation status (read this first)

This environment has no network, so third-party dependencies (pydantic, langgraph, pytest, boto3)
are not installed and the unit tests **cannot execute here**. What has been verified:

- `python3 -m py_compile` across every module — all compile.
- `tools/check_imports.py` — every internal cross-module import resolves.
- The shared stdlib-only helpers in `agents/base/parsing.py` (`extract_json`, the confidence
  ordering used by RCA) were **executed** and pass.

The four `tests/test_*_agent.py` suites are written and syntax-checked; they run for the first time
in CI. This adds the agents and completes the graph wiring — it does **not** make the platform
end-to-end runnable on its own (see "Still required" at the end).

## How they fit the workflow

The compiled graph now is:

```
intake → triage_gate → (parallel) knowledge ┐
                       (parallel) architecture ┘→ rca → [confidence gate]
                                                          ├─ recommend → recommendation → communication → human_review
                                                          └─ escalate (low/speculative) ─────────────────→ human_review
```

The confidence gate (`_after_rca` in `services/orchestrator/graph/build.py`) routes low- or
speculative-confidence analyses straight to human review, skipping automated recommendations. All
six nodes are optional in `build_investigation_graph(...)`; `build_orchestrator(...)` in
`services/api/main.py` wires whichever agents it is given.

---

## Agent 3 — Architecture Discovery (`agents/architecture_discovery/`)

**Role.** Assemble the dependency/topology context the RCA agent reasons over: which CIs are
impacted, what they depend on, and what changed recently.

**Deterministic and read-only — no LLM.** Architecture is *read* from the CMDB, not generated. The
agent calls read-only ServiceNow tools through the MCP gateway (`servicenow.get_cmdb_ci`,
`servicenow.get_ci_relationships`, `servicenow.list_change_requests`) and assembles the result. It
cannot mutate anything and invents no topology.

- **Input:** `ArchitectureInput` (investigation id + affected system names from intake).
- **Output:** `ArchitectureContext` — `impacted[]`, `dependencies[]`, `recent_changes[]`, a
  templated `summary`, `topology_freshness`, and a `degraded` flag.
- **Degradation:** tolerant parsing of CMDB shapes; any tool failure (or a missing gateway) is
  recorded as a warning and the agent returns a degraded-but-valid context. The node never raises.

## Agent 4 — RCA (`agents/rca/`)

**Role.** Correlate the intake classification, retrieved knowledge, architecture/recent-change
context, and any evidence into a ranked, evidence-referenced set of root causes. Runs on the **top**
model tier.

- **Input:** `RcaInput` (classification, hypothesis, knowledge findings + citations, similar
  incidents, architecture context, evidence).
- **Output:** `RcaOutput` — `ranked_causes[]` and `alternatives[]` (each with a confidence grade,
  `evidence_refs`, and rationale), plus an `overall_confidence` grade.

**Guardrails (enforced in code, not just the prompt):**

- Every cause's `evidence_refs` are filtered to ids that actually exist among the provided
  citations/evidence — ungrounded references are dropped.
- A cause asserted as high/medium confidence with **no** grounded evidence is capped to `low`.
- `overall_confidence` is capped at the strongest individual cause (`min_conf`) — the analysis can
  never claim more confidence than its best-supported cause.
- Confidence is always a **grade** (high/medium/low/speculative), never a fabricated percentage.
- Alternatives are always solicited.
- If the model is unavailable or unparseable, the agent returns an explicit `speculative` result —
  which the confidence gate then escalates to a human.

The node maps `overall_confidence` to `state["rca_confidence"]`, which drives the gate.

## Agent 5 — Recommendation (`agents/recommendation/`)

**Role.** Turn the RCA into a prioritized list of troubleshooting/remediation steps. Runs on the
**mid** tier. **It never executes anything.**

- **Input:** `RecommendationInput` (rca + architecture context + knowledge findings).
- **Output:** `RecommendationOutput` — `steps[]`, each tagged with `category`
  (diagnostic/mitigation/preventive/verification), `risk` (low/medium/high), `prod_impacting`, and
  `approval_requirement` (none / human_approval / human_approval_and_change), plus an
  `advisory_notice`.

**Structural safety guardrail (`_normalize`):** regardless of what the model returns,
- any `prod_impacting` step is forced to require **at least** `human_approval`;
- a high-risk prod-impacting step is forced to `human_approval_and_change`.

The model cannot downgrade an approval requirement. If the model is unavailable, the agent falls
back to a single safe **diagnostic-only** step (no prod impact, no approval needed).

## Agent 6 — Communication (`agents/communication/`)

**Role.** Draft incident communications and the platform's own RCA report. Runs on the **mid** tier.

- **Input:** `CommunicationInput` (incident + classification + rca + recommendations).
- **Output:** `CommunicationOutput` — `drafts[]` for Slack, a ServiceNow work-note, and an exec
  summary, plus a structured `rca_report`.

**Draft-only by construction.** Every artifact is marked `status="draft"`, the top-level output is
`status="draft"`, and the agent has **no capability to post anywhere** — it only returns text. A
human reviews and posts. If the model is unavailable, the agent produces deterministic templated
drafts built from the structured RCA/recommendations (still draft-only, still grounded).

---

## State additions (`contracts/models.py`)

`InvestigationState` gained: `architecture_context` / `architecture_metadata` /
`architecture_degraded`; `rca` / `rca_confidence` / `rca_metadata` / `rca_degraded`;
`recommendations` / `recommendation_metadata` / `recommendation_degraded`; `communications` /
`communication_metadata`; and `human_decision`. All are JSON-serializable so they round-trip
through the durable checkpointer.

## Safety invariants (unchanged, reaffirmed)

- **Read-only / advisory-only by construction.** Agent 3 uses read-only connectors only; Agents
  4–6 only read state and the LLM. Nothing here can close an incident, change a CO/ECO, restart a
  service, deploy, or post a message.
- The only place a human decision enters is the `human_review` interrupt; acting still happens
  outside the platform via the change/ECO process.
- Recommendations carry mandatory, non-downgradable approval tags; communications are drafts only.

## Still required before a meaningful end-to-end QA run

Adding these agents does not remove the remaining blockers:

1. **MCP gateway** — the read-only choke point the agents call tools through (keystone; not built).
2. **RAG retriever + ingestion** — Agent 2's corpus.
3. **Read-only observability connector** — a primary evidence source for Agent 4.
4. **Bedrock access + connector credentials**, and **infra** (container/Helm/Terraform).
5. **First CI run** — nothing has executed yet; expect to fix issues the unit suites surface.
