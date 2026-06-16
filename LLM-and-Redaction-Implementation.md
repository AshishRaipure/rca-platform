# LLM Serving + Redaction
## Implementation Guide

**Status:** Implemented (Phase 4) · **Modules:** `libs/llm/`, `libs/redaction/`

This is the tiered Claude client every agent depends on, served through **AWS Bedrock** (ADR D-1), with the **redaction/masking boundary baked in** (ADR D-2). It implements the exact `LLMClient.complete` contract the agents declare, so it drops into Agents 1–2 (and 3–6 as they're built) without changes.

> **Validation status:** all 72 files compile on Python 3.12 and pass the cross-module import check. `libs/redaction` has no external dependencies, so it was **executed and verified** against its fixtures (masking, consistent placeholders, and — critically — *preservation* of operational signal like git SHAs and error codes). The `libs/llm` tests are written and syntax-checked but **not executed** (boto3/botocore absent, network off; both imported lazily). Run `pytest -q` where boto3 + pytest-asyncio exist.

---

## 1. The contract it satisfies

Both agents declare the same Protocol; this client matches it exactly:

```
async def complete(*, system: str, user: str, model_tier: ModelTier,
                    max_tokens: int, temperature: float,
                    request_id: str, timeout_s: float) -> LLMResponse
```

`LLMResponse` exposes `text, model_id, model_version, input_tokens, output_tokens, latency_ms`. The concrete `LLMResponse` is a frozen dataclass (`libs/llm/types.py`) that structurally satisfies the agents' `LLMResponse` Protocol.

---

## 2. Tier routing

Agents pass a logical `ModelTier` (`fast | mid | top`) and **never see a model string** — the tier→model mapping lives only in `LLMConfig.model_ids` (`libs/llm/config.py`):

| Tier | Class | Default placeholder (override per deployment) |
|---|---|---|
| `fast` | Haiku-class | `anthropic.claude-haiku-4-5` |
| `mid` | Sonnet-class | `anthropic.claude-sonnet-4-6` |
| `top` | Opus-class | `anthropic.claude-opus-4` |

The defaults are placeholders; real Bedrock model ids or **inference-profile ARNs** are deployment-specific and set via `BEDROCK_MODEL_FAST/MID/TOP`. An unmapped tier raises `LLMConfigError` rather than guessing.

---

## 3. Bedrock invocation

`BedrockLLMClient` (`libs/llm/client.py`):
- Builds the **Bedrock Anthropic Messages** request (`anthropic_version = "bedrock-2023-05-31"`, `system`, a single user message, `max_tokens`, `temperature`).
- boto3 is synchronous, so the call runs on a worker thread via `asyncio.to_thread`, hard-bounded by `timeout_s` (`asyncio.wait_for`). boto3/botocore are imported lazily and a `bedrock-runtime` client can be **injected** (tests do this).
- **Retry/backoff** is owned here (botocore retries disabled): throttling/capacity codes (`ThrottlingException`, `TooManyRequestsException`, `ServiceUnavailableException`, `ModelTimeoutException`, `InternalServerException`, `ServiceQuotaExceededException`) are classified as transient and retried with capped exponential backoff; everything else fails fast.
- Parses the response: concatenates `content[].text`, reads `usage.input_tokens/output_tokens`, measures `latency_ms`.

---

## 4. The redaction boundary (verified)

Redaction is applied **by construction** inside `complete()` — both `system` and `user` pass through the redactor *before* the request body is built, so no un-redacted content can reach Bedrock. The only way to disable it is to explicitly inject `NoOpRedactor`.

`libs/redaction/redactor.py` provides a reusable `Redactor` port and a conservative, dependency-free `DefaultRedactor`:
- **Masks** emails, IPv4, AWS access keys (`AKIA/ASIA…`), JWTs, bearer tokens, private-key blocks, card numbers, SSNs, and phone numbers — each to a typed placeholder (`<EMAIL_1>`, `<IP_1>`, …).
- **Consistent placeholders:** identical values map to the same placeholder within a call, so correlation survives.
- **Preserves operational signal:** service names, error codes, stack frames, and git SHAs are deliberately *not* masked — blanket masking would gut RCA quality. (This is the behavior verified by execution.)

Two enforcement points are intended (ADR §3): this prompt-time boundary, and an ingestion-time pass (the same `DefaultRedactor` is reused before chunks are embedded, so the corpus never stores raw PII). Because Bedrock keeps inference **in-account**, redaction here is defense-in-depth rather than the sole control.

Production would swap the detector set for a dedicated PII engine (Amazon Comprehend / Microsoft Presidio) behind the same `Redactor` port — an open decision in the ADR.

---

## 5. Errors & failure behavior

`libs/llm/errors.py`: `LLMError` → `LLMConfigError`, `LLMThrottledError` (internal, drives retry), `LLMUnavailableError` (terminal). These are independent of the agents' error types: the agents wrap `complete()` in a broad `except`, retry per their own policy, and on exhaustion raise *their* `LLMUnavailableError`, which routes the node to a human (graceful degradation, never a crash). So a Bedrock outage surfaces as an escalation, not a failure.

---

## 6. Security & residency controls

| Control | Mechanism |
|---|---|
| **Data residency (D-1)** | Bedrock runs in-account/in-region; no external model-API egress. |
| **PII/secret redaction (D-2)** | Applied by construction at the prompt boundary; reusable at ingestion. |
| **No training on data** | Bedrock + the account's model-invocation terms (no customer-data training). |
| **Secret hygiene** | No credentials in code; region/model ids by env; the runtime client is constructed at the composition root. |
| **Bounded resource use** | Per-call timeout, capped retries/backoff, botocore connect/read timeouts. |
| **No raw values logged** | Only redaction *counts* are logged (debug), never the masked content. |
| **Model provenance** | `model_id` (+ optional `model_version_label`) returned for the audit trail. |

---

## File manifest

| File | Role |
|---|---|
| `libs/redaction/redactor.py` | `Redactor` port, `RedactionResult`, `DefaultRedactor`, `NoOpRedactor`. |
| `libs/redaction/tests/test_redaction.py` | Executed/verified: masking, consistency, signal preservation. |
| `libs/llm/config.py` | `LLMConfig` — tier→model map (only home for model strings), region, timeouts, retries; `from_env`. |
| `libs/llm/types.py` | `LLMResponse` dataclass. |
| `libs/llm/errors.py` | `LLMError` hierarchy. |
| `libs/llm/client.py` | `BedrockLLMClient` (`complete`, redaction baked in, Bedrock invoke, retry/parse) + factory. |
| `libs/llm/tests/test_llm.py` | Tier routing, redaction-before-send, parsing, throttle retry, terminal→`LLMUnavailableError`. |

## Open decisions / not-yet-built

- **Real Bedrock model ids / inference-profile ARNs** per deployment (env-driven; placeholders today).
- **Redaction tooling** — in-house detectors (now) vs Comprehend/Presidio (production).
- **Investigation-scoped redaction sessions** — placeholders are currently consistent *within a call*; cross-call consistency within one investigation would need an investigation-keyed session (the agents' `complete` signature carries `request_id`, not `investigation_id`, so this is a deliberate later enhancement).
- **Prompt caching + tracing** (Langfuse/OTel) — referenced in the agents' interface comment; not yet wired here. Bedrock prompt-cache points and a tracing hook are a clean follow-up.

*This unblocks the agents to actually call a model. With `libs/llm` in place, `build_orchestrator` can be wired (it needs this client, the MCP gateway, and the RAG retriever to construct the agent nodes). Natural next pieces: the **MCP gateway + policy layer** (the read-only enforcement spine), or **Agent 3 (Architecture Discovery)**.*
