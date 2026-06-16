"""Knowledge Retrieval Agent — typed errors.

Failure philosophy: missing knowledge is survivable, so this agent degrades rather than failing.
  * Retrieval unavailable -> return an explicitly-degraded "retrieval unavailable" result.
  * Synthesis (LLM) unavailable/unparseable -> return retrieved citations WITHOUT synthesis.
Only malformed input raises; the node also catches everything so the graph never crashes.
"""
from __future__ import annotations

from typing import Optional


class KnowledgeError(Exception):
    retryable: bool = False

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.detail = detail


class KnowledgeInputError(KnowledgeError):
    """The state/input handed to the agent was malformed."""


class RetrievalUnavailableError(KnowledgeError):
    """The retriever could not be reached. Caught internally; the agent degrades."""
    retryable = True


class SynthesisUnavailableError(KnowledgeError):
    """The synthesis model could not be reached/parsed. Caught internally; citations-only result."""
    retryable = True


class OutputParsingError(KnowledgeError):
    """The model's response could not be parsed into the expected schema."""


class GuardrailViolation(KnowledgeError):
    """A grounding/safety guardrail could not be satisfied."""
