# RCA Platform — Architecture Baseline & Deltas
## Phase 1: Architecture + PoC/MVP (not production rollout)

This ADR records the locked assumptions for the architecture/PoC phase, maps them against what is
already designed and built (Phases 1–3 + Agents 1–2, three connectors, the API), and calls out the
deltas and remaining open decisions. It supersedes the corresponding "open decisions" in the
Phase 1 doc where a choice is now made.

### Scope boundary
This phase is **architecture + PoC/MVP**. The following are explicitly **deferred to the
production-readiness gate** and are out of scope here: data retention schedules, legal hold,
GDPR/CCPA erasure, audit-evidence/compliance reporting, provisioned LLM throughput / TPM limits,
embedding vector dimension tuning, Aurora sizing, exact concurrency and cost caps, and
FedRAMP/PCI/HIPAA assessments. Where we already built a sensible default (e.g. the hash-chain
audit log), we keep it but do not expand it now.

---

## 1. Locked assumptions (decisions)

| # | Decision | Implication |
|---|---|---|
| D-1 | **Claude via AWS Bedrock.** No company data used for training; all incident/Confluence/ServiceNow/log/RCA data stays in company-controlled AWS. | Resolves Phase 1 open decision #2. Inference is in-account/in-region (VPC endpoint); external egress to a model API is **not** required. |
| D-2 | **PII may exist in tickets and logs → redact/mask before the model.** | New first-class component (see §3). |
| D-3 | **Advisory-only.** May *produce* Slack messages, ServiceNow work-note drafts, and RCA reports. Must not close incidents/ECOs/COs, execute remediation, or trigger production changes. All output requires human review. | Confirms the governing principle; clarifies the draft boundary (see §4). |
| D-4 | **Read-only observability access**; design connector interfaces for CloudWatch, Datadog, Splunk, Prometheus, Grafana; platform selection finalized later. | New connector abstraction (see §3). |
| D-5 | **Read-only AWS** via a cross-account role — metadata + monitoring only; no write/delete/infra-modify and no data-plane object reads. | Confirms R-10; answers the data-plane question (control-plane + monitoring only). |
| D-6 | **Historical incidents in ServiceNow; knowledge in Confluence + runbooks; ingestion supports multiple years; outcomes may be unlabeled.** | Refines Agent 2/RAG confidence model (see §5). |
| D-7 | **ECO/CO remain the system of record.** Platform may *read* ECO/CO for RCA correlation; never create/approve/modify/close/replace. Human approval mandatory. | Already supported read-only by the ServiceNow change tools (see §5). |
| D-8 | **Enterprise SSO + RBAC; design for future multi-team with team-based access.** | Confirms the OIDC + RBAC + ABAC/RLS we built. |

---

## 2. Delta against the current design

| Assumption | Status vs current build |
|---|---|
| Bedrock (D-1) | **Confirm** — was the preferred default (R-11); now firm. Simplifies egress (no external model API). |
| Redaction/masking (D-2) | **Add** — not previously in the data flow. New component. |
| Advisory-only / drafts (D-3) | **Confirm** — connectors are read-only (4 layers); comms already draft-only; the API never writes to ServiceNow/Slack. One nuance to ratify (§4). |
| Observability connectors (D-4) | **Add** — the RCA agent had no evidence source; this fills it. |
| Read-only AWS (D-5) | **Confirm + scope** — confirms R-10; locks "control-plane + monitoring only." |
| Multi-year history, unlabeled outcomes (D-6) | **Revise** — Agent 2 must not rely on outcome labels for confidence. |
| ECO/CO read for correlation (D-7) | **Confirm** — ServiceNow `get_change_request` / `list_change_requests` already provide this read-only. |
| SSO + RBAC + multi-team (D-8) | **Confirm** — ABAC/RLS with `team_id` scoping is built. |

---

## 3. New components

**Redaction & masking layer** — the most consequential delta. A `Redactor` port with two
enforcement points, so sensitive data is removed *by construction*:
- **Ingestion-time** (corpus hygiene): redact before chunks are embedded, so the RAG store never
  persists raw PII/secrets.
- **Prompt-time** (defense-in-depth, at the LLM boundary in `libs/llm`): every outbound prompt —
  retrieved chunks, alert text, logs, ticket notes, change records — passes through redaction
  before reaching Bedrock.

Design notes: it must **classify-and-mask sensitive tokens (PII, secrets, credentials, customer
identifiers) while preserving operational signal** (error codes, service names, stack frames) —
blanket masking would gut RCA quality. Use **consistent pseudonymization within an investigation**
(the same value maps to the same placeholder) so correlation still works. Because Bedrock keeps
data in-account, redaction here is defense-in-depth, not the sole control.

**Observability connector abstraction** — a read-only `ObservabilityPort` (query logs/metrics/
traces over a bounded window) with pluggable adapters for CloudWatch, Datadog, Splunk, Prometheus,
and Grafana. Build the interface + one reference adapter for the PoC; defer platform selection.
This is what turns the RCA agent from "educated guessing" into evidence-based correlation.

---

## 4. The draft boundary (ratify)

Every MCP connector stays **read-only by construction** (read-only credential → GET-only client →
registry import-assertion → gateway allowlist). Drafts the platform "generates" — Slack message
text, ServiceNow work-note text, RCA reports — are produced as **in-platform artifacts**, stored
and surfaced via the API for a human to review and copy/post. The platform does **not** write them
back into ServiceNow or Slack in the PoC/MVP.

The only contemplated write exception is *Slack draft posting*, and even that is deferred: if
later desired, "post this approved draft to Slack" becomes a deliberate, separately-bounded,
audited write path behind the human-review gate — out of scope now. **This is the one item to
thumbs-up**; everything else follows from the read-only principle.

---

## 5. Refinements to existing components

- **Agent 2 / RAG (per D-6):** treat historical outcomes as *often unlabeled*. Confidence derives
  from corroboration across sources, recency/freshness, and source authority — **not** from
  outcome labels. Outcome metadata stays optional/best-effort (we already model it as
  index-authoritative; we now also handle null gracefully and never over-rely on it).
- **ServiceNow connector (per D-7):** ECO/CO correlation is already wired read-only via the change
  tools; the RCA agent will use them to answer "did a recent change cause this?" — no new write.
- **Communication agent / API (per D-3):** confirmed draft-only as in §4.

---

## 6. PoC-vs-production technology split

| Concern | PoC / MVP (now) | Production (later) |
|---|---|---|
| LLM serving | **Bedrock** (locked, D-1) | Bedrock + provisioned throughput |
| Orchestrator (R-1) | LangGraph in-process + persistent checkpointer (built) | Temporal as durable executor |
| Vector store | **pgvector** on Aurora (single store, simpler) | OpenSearch Serverless at scale |
| Embeddings / reranker | Bedrock-hosted (e.g. Titan/Cohere embeddings; Cohere Rerank) — selection open, placeholder fine | finalized + tuned dimension |
| Topology source | ServiceNow CMDB (read-only) | + optional live AWS Config discovery |
| Footprint | Single region; platform account + cross-account read role | Multi-region / DR per tier |
| Audit | Hash-chain WORM (already built; also aids PoC debugging) | + retention/archival + compliance reporting |

---

## 7. READ-ONLY as a hard architectural principle

Elevated to a stated invariant: **the deployed platform has no capability to mutate any external
system.** Enforced structurally — read-only credentials, GET-only clients, an import-time
assertion that no registered tool mutates, and a gateway allowlist — with a structural test that
fails if a mutating tool is ever registered. A wrong or jailbroken LLM still cannot close, delete,
restart, deploy, ack, or reassign anything. The single contemplated exception (Slack draft
posting) is deferred and would be added as an explicit, audited, human-gated path — never as a
silent connector capability.

---

## 8. Remaining open decisions (to call out)

1. **Draft posting (§4):** in-platform drafts only (assumed) vs a future Slack post-back exception.
2. **Orchestrator timing:** LangGraph-only through PoC vs committing to Temporal earlier.
3. **Vector store:** pgvector for PoC vs OpenSearch from the start.
4. **Redaction tooling:** in-house detectors vs Amazon Comprehend (PII) vs Microsoft Presidio.
5. **Embedding + reranker model selection** (placeholder acceptable for PoC).
6. **Observability platform** (explicitly deferred by stakeholder; interface built now).
7. **PoC runtime tenancy:** multi-team-capable architecture (built) run as single-team vs multi-team in the PoC.

---

## 9. Risk register additions

| ID | Risk | Mitigation |
|---|---|---|
| R-14 | RCA has no source of truth without logs/metrics/traces → guessing. | Observability connector abstraction (§3); RCA correlates incident + runbook + logs + metrics + topology. |
| R-15 | PII/secrets/customer data sent to the model or stored in the corpus. | Redaction layer at ingestion + prompt boundary (§3); Bedrock keeps data in-account (D-1). |
| R-16 | Cross-team data leakage via semantic retrieval (RLS doesn't cover vector search). | Enforce ABAC scope at retrieval time in the RAG layer, not only in SQL. |
| R-17 | Automation bias — advisory output treated as action under pressure. | Confidence grades, mandatory alternatives, explicit "verify before acting" framing; human-review gate. |
| R-18 | Availability circular-dependency — platform reads systems that are themselves degraded during an incident. | Degrade gracefully; keep the platform out of the recovery critical path; cache last-known topology. |

*(Production-only risks — retention, erasure, legal hold, throughput quotas — are tracked for the
production-readiness gate, not here.)*
