"""Incident Intake Agent — typed errors.

The agent's failure philosophy (Phase 1 NFR2 graceful degradation):
  * Enrichment failures are NON-fatal -> warn and continue with the alert payload.
  * Unparseable model output -> repair, then deterministic fallback (degraded, not failed).
  * The LLM being entirely unavailable IS fatal for the node -> the node escalates to a human.
"""
from __future__ import annotations

from typing import Optional


class IntakeError(Exception):
    """Base class for all intake errors."""
    retryable: bool = False

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


class IntakeInputError(IntakeError):
    """The state/input handed to the agent was malformed."""


class IncidentEnrichmentError(IntakeError):
    """A read-only enrichment call failed. Non-fatal; the agent degrades gracefully."""
    retryable = True


class LLMUnavailableError(IntakeError):
    """The model could not be reached/parsed at all. The node routes to a human."""
    retryable = True


class OutputParsingError(IntakeError):
    """The model's response could not be parsed into the expected schema."""


class GuardrailViolation(IntakeError):
    """A safety guardrail could not be satisfied and the result is unsafe to emit."""
