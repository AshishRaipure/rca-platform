# Project structure

Single project root: `rca-platform/` (push the contents to your repo).

```
rca-platform/
├── .claude/
│   └── skills/
│       └── add-investigation-agent/
│           └── SKILL.md
├── .github/
│   └── workflows/
│       └── ci.yml
├── agents/
│   ├── architecture_discovery/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_architecture_agent.py
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   └── schemas.py
│   ├── base/
│   │   ├── __init__.py
│   │   ├── interfaces.py
│   │   └── parsing.py
│   ├── communication/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_communication_agent.py
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── prompts.py
│   │   └── schemas.py
│   ├── intake/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_intake_agent.py
│   │   ├── __init__.py
│   │   ├── _interfaces.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── prompts.py
│   │   ├── schemas.py
│   │   └── tools.py
│   ├── knowledge_retrieval/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_knowledge_agent.py
│   │   ├── __init__.py
│   │   ├── _interfaces.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── prompts.py
│   │   ├── schemas.py
│   │   └── tools.py
│   ├── rca/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_rca_agent.py
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── prompts.py
│   │   └── schemas.py
│   ├── recommendation/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_recommendation_agent.py
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   ├── prompts.py
│   │   └── schemas.py
│   └── __init__.py
├── contracts/
│   ├── __init__.py
│   ├── enums.py
│   ├── models.py
│   └── retrieval.py
├── db/
│   ├── migrations/
│   │   ├── 0001_initial.sql
│   │   └── __init__.py
│   ├── __init__.py
│   ├── engine.py
│   ├── models.py
│   └── repositories.py
├── docs/
│   └── diagrams/
│       ├── investigation-workflow.svg
│       └── system-architecture.svg
├── libs/
│   ├── audit/
│   │   ├── __init__.py
│   │   └── sink.py
│   ├── llm/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_llm.py
│   │   ├── __init__.py
│   │   ├── client.py
│   │   ├── config.py
│   │   ├── errors.py
│   │   └── types.py
│   ├── redaction/
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_redaction.py
│   │   ├── __init__.py
│   │   └── redactor.py
│   └── __init__.py
├── mcp_connectors/
│   ├── servers/
│   │   ├── confluence/
│   │   │   ├── tests/
│   │   │   │   ├── __init__.py
│   │   │   │   └── test_confluence_server.py
│   │   │   ├── __init__.py
│   │   │   ├── client.py
│   │   │   ├── config.py
│   │   │   ├── errors.py
│   │   │   ├── schemas.py
│   │   │   ├── server.py
│   │   │   └── tools.py
│   │   ├── pagerduty/
│   │   │   ├── tests/
│   │   │   │   ├── __init__.py
│   │   │   │   └── test_pagerduty_server.py
│   │   │   ├── __init__.py
│   │   │   ├── client.py
│   │   │   ├── config.py
│   │   │   ├── errors.py
│   │   │   ├── http.py
│   │   │   ├── normalize.py
│   │   │   ├── schemas.py
│   │   │   ├── server.py
│   │   │   └── tools.py
│   │   ├── servicenow/
│   │   │   ├── tests/
│   │   │   │   ├── __init__.py
│   │   │   │   └── test_servicenow_server.py
│   │   │   ├── __init__.py
│   │   │   ├── client.py
│   │   │   ├── config.py
│   │   │   ├── errors.py
│   │   │   ├── normalize.py
│   │   │   ├── schemas.py
│   │   │   ├── server.py
│   │   │   └── tools.py
│   │   └── __init__.py
│   ├── __init__.py
│   ├── contracts.py
│   └── http.py
├── services/
│   ├── api/
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── investigations.py
│   │   │   ├── review.py
│   │   │   └── system.py
│   │   ├── tests/
│   │   │   ├── __init__.py
│   │   │   └── test_api.py
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── auth.py
│   │   ├── config.py
│   │   ├── deps.py
│   │   ├── errors.py
│   │   ├── main.py
│   │   └── schemas.py
│   ├── orchestrator/
│   │   ├── graph/
│   │   │   ├── nodes/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── architecture.py
│   │   │   │   ├── communication.py
│   │   │   │   ├── intake.py
│   │   │   │   ├── knowledge.py
│   │   │   │   ├── rca.py
│   │   │   │   └── recommendation.py
│   │   │   ├── __init__.py
│   │   │   └── build.py
│   │   ├── __init__.py
│   │   └── client.py
│   ├── webhook_ingress/
│   │   ├── providers/
│   │   │   ├── __init__.py
│   │   │   └── pagerduty.py
│   │   └── __init__.py
│   └── __init__.py
├── tools/
│   └── check_imports.py
├── .env.example
├── .gitignore
├── ARCHITECTURE.md
├── Agent1-Incident-Intake-Implementation.md
├── Agent2-Knowledge-Retrieval-Implementation.md
├── Agents-3-6-Implementation.md
├── CLAUDE.md
├── Confluence-MCP-Integration.md
├── FastAPI-Backend-Implementation.md
├── LLM-and-Redaction-Implementation.md
├── PROJECT-STRUCTURE.md
├── PagerDuty-MCP-Integration.md
├── RCA-Platform-Architecture-Baseline-and-Deltas.md
├── README.md
├── ServiceNow-MCP-Integration.md
├── USAGE.md
├── Worked-Examples-Agent-Walkthrough.md
├── pyproject.toml
├── requirements-dev.txt
└── requirements.txt
```
