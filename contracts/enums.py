"""Shared, controlled-vocabulary enums (contracts layer).

These mirror the Phase 2 database enums so every layer (agents, services, DB) speaks an
identical vocabulary. Nothing in `contracts/` depends on any other internal package.
"""
from __future__ import annotations

from enum import Enum


class SourceSystem(str, Enum):
    pagerduty = "pagerduty"
    servicenow = "servicenow"
    slack = "slack"
    jira = "jira"
    github = "github"
    runbook_repo = "runbook_repo"
    manual = "manual"
    other = "other"


class SeverityLevel(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class ConfidenceGrade(str, Enum):
    """Confidence is a GRADE, never a fabricated numeric percentage (Phase 1 §3.7)."""
    high = "high"
    medium = "medium"
    low = "low"
    speculative = "speculative"


class TriageDecision(str, Enum):
    full = "full"
    lite = "lite"
    drop = "drop"


class ModelTier(str, Enum):
    """Logical model tiers. The tier -> concrete-model mapping lives in `libs/llm`
    (fast=Haiku-class, mid=Sonnet-class, top=Opus-class); confirm exact model strings
    against current docs at build time."""
    fast = "fast"
    mid = "mid"
    top = "top"


# Severity ordering: higher rank == more severe.
_SEVERITY_ORDER: tuple[SeverityLevel, ...] = (
    SeverityLevel.info,
    SeverityLevel.low,
    SeverityLevel.medium,
    SeverityLevel.high,
    SeverityLevel.critical,
)
SEVERITY_RANK: dict[SeverityLevel, int] = {s: i for i, s in enumerate(_SEVERITY_ORDER)}


def more_severe(a: SeverityLevel | None, b: SeverityLevel | None) -> SeverityLevel | None:
    """Return the more severe of two severities, tolerating ``None``."""
    if a is None:
        return b
    if b is None:
        return a
    return a if SEVERITY_RANK[a] >= SEVERITY_RANK[b] else b


def is_serious(sev: SeverityLevel | None) -> bool:
    """True for high/critical — incidents that must never be auto-dropped."""
    return sev is not None and SEVERITY_RANK[sev] >= SEVERITY_RANK[SeverityLevel.high]
