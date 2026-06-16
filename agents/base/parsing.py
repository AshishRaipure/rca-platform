"""Shared helpers for the analytical agents: JSON extraction, a clock, confidence ordering."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from contracts.enums import ConfidenceGrade


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


def extract_json(text: str) -> dict[str, Any]:
    """Strip code fences and parse the outermost JSON object. Raises ValueError on failure."""
    if not text or not text.strip():
        raise ValueError("empty model output")
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in model output")
    return json.loads(t[start:end + 1])


CONF_RANK = {
    ConfidenceGrade.speculative: 0,
    ConfidenceGrade.low: 1,
    ConfidenceGrade.medium: 2,
    ConfidenceGrade.high: 3,
}


def min_conf(a: ConfidenceGrade, b: ConfidenceGrade) -> ConfidenceGrade:
    return a if CONF_RANK[a] <= CONF_RANK[b] else b


def grade_from_str(value: Any, default: ConfidenceGrade = ConfidenceGrade.low) -> ConfidenceGrade:
    try:
        return ConfidenceGrade(value)
    except (ValueError, TypeError):
        return default
