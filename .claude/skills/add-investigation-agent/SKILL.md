---
name: add-investigation-agent
description: >
  Use when adding a new agent to this RCA platform's LangGraph investigation workflow, or when
  modifying an existing agent's files or wiring. Covers the required package layout, the
  InvestigationState contract, the graph + orchestrator wiring points, the read-only/advisory
  safety guardrails every agent must preserve, and how to validate offline. Trigger for requests
  like "add an agent", "add a step to the investigation graph", "wire a new agent node", or
  "create another agent like the RCA one".
---

# Add an investigation agent

This repo has six agents that all follow one shape. Adding (or changing) one means touching a fixed
set of files and preserving the platform's safety invariant. Follow these steps in order. Read
`CLAUDE.md` first for the project-wide rules.

## 0. Pick the shape

- **Deterministic / tool-driven** (no LLM) → template: `agents/architecture_discovery/agent.py`.
- **LLM-based** (synthesis) → templates: `agents/rca/agent.py` (grounding + graded confidence),
  `agents/recommendation/agent.py` (approval guardrail), `agents/communication/agent.py`
  (draft-only + deterministic fallback).

Decide the model tier (`fast` / `mid` / `top`) and whether it reads external data (read-only tools
via the gateway only).

## 1. Create the package `agents/<name>/`

- `config.py` — a Pydantic `BaseModel`: tier(s), timeouts, `llm_max_tokens`, `llm_temperature`,
  `llm_max_attempts`, any caps, and a `prompt_version` string (e.g. `"<name>-v1"`).
- `errors.py` — a base `Exception` + `<Name>InputError` (+ `<Name>UnavailableError` if it uses the
  LLM).
- `schemas.py` — a Pydantic `Input` (built from `InvestigationState`), an `Output`, and (LLM only)
  an `_LLM<Name>Result` parse target with `ConfigDict(extra="ignore")`.
- `prompts.py` *(LLM only)* — `SYSTEM_PROMPT` (state it is advisory/read-only, demand grounding,
  specify a STRICT JSON output schema), `build_user_prompt(request)` that wraps all incident/tool
  data in an untrusted-data block, and a `REPAIR_SUFFIX`.
- `agent.py` — the agent class:
  - `__init__` injects dependencies as Protocols from `agents.base.interfaces`
    (`LLMClient`, `MCPGateway`, `AuditSink`, `Clock`) — never concrete services.
  - `async def run(self, request, *, request_id, scope=None) -> Output`.
  - LLM agents: `extract_json` (from `agents.base.parsing`) → validate → retry once with
    `REPAIR_SUFFIX` → on failure, a deterministic **fallback** result. Never let the model decide
    safety-critical fields unchecked.
  - Best-effort `_safe_audit(...)` wrapped in try/except.
- `tests/test_<name>_agent.py` — fakes for the injected ports; assert the happy path, each
  **guardrail**, and the degrade/fallback path (e.g. LLM raises).
- Add `__init__.py` to the package and its `tests/`.

## 2. Add the graph node

Create `services/orchestrator/graph/nodes/<name>.py` with `make_<name>_node(agent)` returning an
`async def <name>_node(state) -> dict`. It must:

- adapt `state` → the typed `Input` (read primitives defensively; tolerate missing keys);
- call `await agent.run(...)`;
- map `Output` → a JSON-serializable state delta;
- **never raise** — wrap input-adaptation and `agent.run` in try/except and return a `_degraded(...)`
  delta (with an `errors` entry) on any failure.

## 3. Extend the state contract

Add the new output keys to `InvestigationState` in `contracts/models.py` (dict/list/str/bool only,
so they round-trip through the checkpointer).

## 4. Wire it

- `services/orchestrator/graph/build.py` — add the node as an optional slot param of
  `build_investigation_graph(...)` and add its edges. Respect the existing flow (parallel
  knowledge/architecture → rca → **confidence gate** → recommendation → communication →
  human_review). If you add a stage that can be low-confidence, route it like `_after_rca`.
- `services/api/main.py` — in `build_orchestrator(...)`, construct the node (lazy-import the factory)
  when its agent is provided, and pass it into `build_investigation_graph`.

## 5. Safety guardrails (MUST hold)

- **Read-only:** if the agent calls tools, only read-only ones through the gateway. Never register
  or call a mutating tool, and never write to an external system.
- **Advisory:** outputs are analysis / advice / drafts. Recommendation steps must carry an approval
  requirement (prod-impacting ⇒ at least `human_approval`); communications must be `status="draft"`.
- **Grounding:** reference only provided evidence/citations; drop unknown references; cap/lower
  confidence when ungrounded. Confidence is a grade, never a percentage.
- **Prompt-injection:** treat all incident and tool data as untrusted input, framed as data, not
  instructions.
- **Degrade, don't crash:** the node returns a degraded delta on any error.
- **Model strings only in `libs/llm`;** pass a `ModelTier`.

## 6. Validate

```bash
for d in $(find agents contracts db libs mcp_connectors services -type d -not -path '*/__pycache__*'); do
  [ -f "$d/__init__.py" ] || touch "$d/__init__.py"; done
python -m compileall -q agents contracts db libs mcp_connectors services
python tools/check_imports.py
pytest        # runs in CI; if this environment has no network, py_compile + check_imports is the offline gate
```

## 7. Document

Add `Agent<N>-<Name>-Implementation.md` (one doc per agent, matching Agents 1–2) covering its role,
I/O schemas, node design, and safety guardrails. Update the "Status" sections in `README.md` and
`CLAUDE.md`. If the change alters the graph flow, also update
`docs/diagrams/investigation-workflow.svg` (and `system-architecture.svg` if components changed) so
the embedded diagrams stay accurate.
