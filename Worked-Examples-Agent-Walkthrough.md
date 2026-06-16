# Worked Examples — End-to-End Agent Walkthrough
## How the six agents execute two real incidents

This traces both reference incidents through the full pipeline (see the flow diagram in chat). For each agent it shows **Input → Output → Decision**, then where the **RCA is generated** and where a **human approves**. Two invariants hold throughout: every external call is **read-only**, and the platform's only write is to **its own RCA document / investigation state / audit** — it never closes an incident/CO/ECO and never executes a remediation.

**Shared skeleton.** `intake → triage gate → (parallel: knowledge + architecture) → RCA → confidence gate → recommendation → communication → human review (pause)`. Model tiers: intake = fast, knowledge/architecture/recommendation/communication = mid, RCA = top. The graph pauses (`interrupt_before`) at human review; a recorded decision resumes it.

> **Built vs designed:** Agents 1–2, the read-only connectors, `libs/llm` + redaction, the API, and the orchestrator graph/client are implemented. Agents 3–6 are specified and pending. This walkthrough shows the *designed* end-to-end behavior so the seams are concrete.

---

# Example 1 — High CPU on a production server

**Trigger.** A CloudWatch/Datadog alarm ("CPU > 95% for 10m on `prod-app-07`") opens a PagerDuty incident → webhook to `webhook-ingress` → HMAC verified → `NormalizedIncident` minted → investigation created (status `running`) → graph started.

### 1 · Incident intake  (fast)
- **Input:** `NormalizedIncident` { source=pagerduty, title="High CPU utilization on prod-app-07", provider_severity=high, pagerduty_id, raw_payload=alarm details }. Read tools available: `pagerduty.get_incident`, `pagerduty.list_alerts`, `servicenow.get_incident` (to correlate an existing ticket).
- **Processing:** parse alert; classify severity against the provider floor (never under-rate → `high`); identify impacted systems and **validate them against the service catalog/topology** (so `prod-app-07` resolves to service `order-api`; ungrounded names are dropped); form a grounded preliminary hypothesis. Untrusted alert text is fenced (prompt-injection defense).
- **Output (state):** `classification` { suggested_severity: high, impacted_systems: [order-api / prod-app-07], categories: [resource_exhaustion] }, `initial_hypothesis` { "sustained CPU saturation on prod-app-07; candidates: runaway process, traffic surge, or post-deploy regression — unconfirmed" }, `recommended_triage: investigate`.
- **Decision:** severity `high` is serious → triage clamp forbids "drop" → `investigate`. Not ambiguous enough to escalate at intake.

### Triage gate  (auto)
`recommended_triage = investigate` → **fan out** to Knowledge + Architecture in parallel. (A "drop" here is impossible for a serious incident.)

### 2 · Knowledge retrieval  (mid, parallel)
- **Input:** classification + hypothesis + incident; `RetrieverPort` over the RAG corpus (Confluence runbooks, historical incidents, past RCAs, ServiceNow KB). Optional read-only freshness probe (`confluence.get_page` / `servicenow.get_knowledge`).
- **Processing:** hybrid retrieval + rerank → grounded synthesis with **mandatory citations validated against the retrieved set** (hallucinated cites dropped); surface conflicts; read outcome/freshness from the index (a confirmed-correct precedent outranks a refuted one).
- **Output:** `knowledge_summary`; `similar_incidents` [ INC-04821 "high CPU on order-api after a deploy — leak in a new code path", outcome=confirmed; RCA-2025-113 "CPU saturation from an N+1 query post-release" ]; `citations[]`; `knowledge_findings[]`; `knowledge_coverage: high`; `knowledge_degraded: false`.
- **Decision / approval:** none — read and analyze only.

### 3 · Architecture discovery  (mid, parallel)
- **Input:** impacted service (`order-api` / `prod-app-07`) + scope; reads the pre-built topology/CMDB (`servicenow.get_cmdb_ci`, `get_ci_relationships`) and read-only AWS/observability metadata (instance type, ASG, scaling events).
- **Processing:** build the dependency subgraph and correlate recent changes (reads `servicenow.get_change_request` / `list_change_requests`).
- **Output:** `architecture_context` { node: prod-app-07 in `order-api` ASG (3 instances); dependencies: [orders-db (RDS), payments-svc, redis]; upstream: api-gateway; **recent_changes: [CO-12345 — order-api v2.3.1 deployed ~6h ago]**; topology_freshness: fresh }.
- **Decision / approval:** none — read-only.

### 4 · Root cause analysis  (top)  ← RCA generation
- **Input:** classification + hypothesis + `knowledge_findings`/citations + `architecture_context` + **evidence** pulled read-only from observability (per-process CPU, heap/GC, thread counts, request rate, error rates).
- **Processing & correlation:** CPU climbs steadily starting ~T-6h, coinciding with **CO-12345 (v2.3.1)**; heap and thread counts grow monotonically (leak/thread-exhaustion signature); request rate is flat (rules out a traffic surge); downstream DB/cache metrics are normal; the pattern matches confirmed precedent INC-04821.
- **Output — the RCA (graded, with alternatives):**
  - **Primary — confidence: high.** A regression in `order-api v2.3.1` (CO-12345) introduced a resource/thread leak driving CPU saturation. Evidence: temporal correlation with the deploy; monotonic heap/thread growth; flat traffic; matching confirmed prior incident (cited).
  - **Alternative — medium.** Downstream slowness causing thread pile-up — partially plausible but DB/cache metrics are clean → lower.
  - **Alternative — low/speculative.** Host-level noisy-neighbor on the instance — weakly supported.
  - This output **populates the platform's RCA document** (the living artifact it owns and will keep updating).
- **Decision (confidence gate):** overall confidence `high` → proceed to Recommendation. (Had it been low/speculative, the gate would escalate straight to human review as "insufficient evidence — needs human investigation.")

### 5 · Recommendation  (mid)
- **Input:** the RCA + architecture context + runbook steps from Knowledge.
- **Output — steps, each tagged Risk + Approval requirement; none executed:**
  1. *Diagnostic (low, read-only):* capture a thread/heap dump from prod-app-07 to confirm the leak. — no prod change.
  2. *Mitigation (medium, prod-impacting):* **roll back order-api to v2.3.0** via the standard deploy pipeline. — **requires human approval + ECO/CO.**
  3. *Mitigation (medium):* temporarily scale out the ASG to relieve saturation during rollback. — requires human approval.
  4. *Preventive (low):* add a regression test + heap/thread-growth alert. — backlog.
- **Approval points:** every prod-impacting step is explicitly flagged "requires human approval"; the platform does not act on any of them.

### 6 · Communication  (mid)
- **Input:** RCA + recommendations + incident metadata.
- **Output — drafts only, stored in-platform, nothing posted:** a Slack incident-channel update draft; a **ServiceNow work-note draft** (text for a human to paste — the platform does not write it); an exec summary; and the finalized **RCA report** (the platform's own document).

### Human review & approval  (the human approval point)
- The workflow **pauses** at `human_review`. An approver opens the investigation via the API/UI and reviews the RCA (ranked causes, evidence, alternatives, confidence), the risk-tagged recommendations, and the drafts.
- They submit a decision (`POST /v1/investigations/{id}/approvals`): **approve / reject / needs_changes**. The decision + audit row commit atomically; *then* the workflow resumes.
- On **approve**: status → completed. The engineer performs the actual rollback **through the ECO/CO process** (outside the platform). As steps are taken, the **RCA document is updated** with the "steps taken" log and the confirmed resolution.
- **What the platform never did:** it never closed the PagerDuty incident, never closed CO-12345, never executed the rollback or the scale-out. Every action was a human's, through the system of record.

**Decision recap (Ex.1):** intake → high/investigate; triage → investigate (parallel); RCA confidence → high (proceed); rollback → requires approval + ECO/CO; human → approve → human acts; platform records the decision only.

---

# Example 2 — Exchange dataset not ingested

**Trigger.** A data-pipeline SLA monitor flags that the daily "Exchange" dataset is missing past its 06:00 UTC load → PagerDuty incident → webhook → `NormalizedIncident` → investigation started.

### 1 · Incident intake  (fast)
- **Input:** `NormalizedIncident` { title="Exchange dataset not ingested", description="daily Exchange load missing from the warehouse; expected 06:00 UTC, absent at 07:30", provider_severity=high (SLA breach) }.
- **Processing:** classify (category: `data_pipeline` / ingestion_failure); impacted system validated to the `exchange-ingestion` pipeline; grounded preliminary hypothesis.
- **Output:** `classification` { suggested_severity: high, impacted_systems: [exchange-ingestion pipeline, warehouse.exchange table], categories: [ingestion_failure] }, `initial_hypothesis` { "expected S3 object did not land, or the S3→SNS→Lambda ingestion did not fire — unconfirmed" }, `recommended_triage: investigate`.
- **Decision:** high + serious → `investigate`, not drop, not escalate.

### Triage gate  (auto)
`investigate` → fan out to Knowledge + Architecture.

### 2 · Knowledge retrieval  (mid, parallel)
- **Input:** classification + hypothesis; RAG corpus.
- **Processing & output:** retrieves a **strong confirmed precedent** — INC-03377 / "RCA: Exchange dataset not ingested — the S3 PutObject event did not trigger the ingestion Lambda; **resolution: re-put (re-upload) the S3 object to retrigger S3→SNS→Lambda**", outcome=confirmed — plus the runbook "Re-trigger dataset ingestion by re-putting the S3 object". `knowledge_coverage: high`; citations attached.
- **Decision / approval:** none.

### 3 · Architecture discovery  (mid, parallel)
- **Input:** the `exchange-ingestion` pipeline + scope; topology/CMDB + read-only AWS metadata.
- **Processing:** map the pipeline `S3 (bucket/prefix) → S3 event notification → SNS topic → Lambda (exchange-ingest) → warehouse`; pull read-only signals (Lambda invocation count in the window, S3 object listing, SNS delivery metrics); check for recent infra changes.
- **Output:** `architecture_context` { pipeline as above; **evidence: the expected object key is present in S3 (landed in-window) but the ingest Lambda has 0 invocations and SNS shows 0 deliveries; no recent infra change** }.
- **Decision / approval:** none — read-only.

### 4 · Root cause analysis  (top)  ← RCA generation
- **Input:** classification + hypothesis + knowledge (the confirmed precedent + runbook) + `architecture_context` + evidence.
- **Processing & correlation:** the object **is** in S3 with an in-window timestamp, yet the Lambda never fired and SNS shows no delivery → the S3→SNS→Lambda notification did not trigger ingestion. Zero invocations (not errors) argues against a Lambda crash/throttle; the present object contradicts a "file never arrived" theory. The signature matches confirmed precedent INC-03377.
- **Output — the RCA:**
  - **Primary — confidence: high.** The S3 object arrived but the S3→SNS→Lambda event did not fire, so ingestion never ran — matching confirmed precedent INC-03377. Evidence: object present at key X in-window; Lambda invocations = 0; SNS deliveries = 0; no infra change (all cited).
  - **Alternative — medium.** Transient Lambda failure/throttle — weakened by invocations = 0 rather than errors > 0.
  - **Alternative — low.** Upstream file genuinely missing — contradicted by the S3 listing.
  - This **populates/updates the RCA document**.
- **Decision (confidence gate):** confidence `high` → proceed.

### 5 · Recommendation  (mid)
- **Output — steps, tagged; none executed:**
  1. *Diagnostic (low, read-only):* confirm the object at the expected key; inspect S3-event/SNS/Lambda metrics.
  2. *Remediation (medium, prod data action):* **re-put / re-upload the S3 object (copy-in-place) to regenerate the S3 event and retrigger SNS→Lambda ingestion.** — **requires human approval.** *(This is precisely the reference incident's real fix — the platform recommends it; a human performs it.)*
  3. *Verification (low):* after re-trigger, confirm the Lambda invoked and the dataset landed.
  4. *Preventive (low–medium):* alert on "Lambda not invoked within N minutes of the expected window"; review the S3 notification config; consider an idempotent reconciliation/re-drive.
- **Approval points:** the re-put is flagged "requires human approval"; the platform never performs it.

### 6 · Communication  (mid)
- **Output — drafts only:** a Slack update ("Exchange ingestion failed; root cause: the S3 event didn't trigger the ingest Lambda; recommended fix: re-put the S3 object — pending approval"); a ServiceNow work-note draft; and the RCA report (the platform's document).

### Human review & approval  (the human approval point)
- The workflow **pauses**. An approver reviews the high-confidence RCA (strong precedent + clean evidence) and the recommended re-put, and submits a decision.
- On **approve**: status → completed; a human performs the **re-put** (the actual remediation) outside the platform, via whatever change path applies; the **RCA document is updated** with steps taken and the confirmed resolution once the dataset lands.
- **What the platform never did:** it never re-put the S3 object, never invoked the Lambda, never closed the incident. It diagnosed and recommended; a human acted.

**Decision recap (Ex.2):** intake → high/investigate; triage → investigate; RCA confidence → high; re-put → requires approval (prod data action), platform does not execute; human → approve → human re-puts; platform records the decision and updates its RCA document.

---

## Approval points & safety recap

| Checkpoint | Who | What it does |
|---|---|---|
| Triage gate | automated | Routes investigate vs drop; a serious incident can never be dropped. |
| Confidence gate | automated | Low/speculative RCA escalates straight to human review instead of recommending. |
| **Human review** | **human (approver)** | The workflow **pauses**; the approver approves/rejects/asks-for-changes. The decision is recorded; nothing is executed by the platform. |
| Acting on a recommendation | **human, via ECO/CO** | Each prod-impacting step is tagged "requires approval"; the human performs it through the system of record (rollback, re-put). |

Across both incidents the platform **investigated, correlated, explained, recommended, and drafted** — and **wrote only its own RCA document, investigation state, and audit log**. It closed nothing, executed nothing, and posted nothing. That is "advisory-only, read-only" holding end to end, by construction.
