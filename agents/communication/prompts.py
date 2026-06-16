"""Communication Agent — prompts."""
from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "communication-v1"

SYSTEM_PROMPT = (
    "You draft incident communications for human review. Everything you produce is a DRAFT for a "
    "human to review and post; nothing is sent automatically, and no action has been taken. Do "
    "not claim the incident is resolved or that any change was made.\n"
    "Produce: a concise Slack incident-channel update; a ServiceNow work-note draft; a short "
    "executive summary; and a structured RCA report (symptoms, leading root cause with confidence, "
    "alternatives, recommended next steps marked as pending human approval).\n"
    "Output STRICT JSON only:\n"
    '{"slack": str, "worknote": str, "exec_summary": str, "rca_report": str}'
)

REPAIR_SUFFIX = (
    "\n\nYour previous output was not valid JSON in the required schema. "
    "Reply with ONLY the JSON object, no code fences."
)


def build_user_prompt(request: Any) -> str:
    payload = {
        "incident": {"title": request.incident.title, "severity": request.severity.value},
        "classification": request.classification,
        "rca": request.rca,
        "recommendations": request.recommendations,
    }
    return (
        "Draft the communications for the analysis below. These are drafts for human review.\n"
        "<analysis>\n" + json.dumps(payload, default=str, indent=2) + "\n</analysis>"
    )
