"""libs/llm errors.

These are independent of the agents' error types. The agents wrap ``complete()`` in a broad
except and re-raise their own ``LLMUnavailableError`` (escalating the node to a human), so this
module owns its own hierarchy without coupling.
"""
from __future__ import annotations


class LLMError(Exception):
    retryable: bool = False


class LLMConfigError(LLMError):
    """Missing/invalid configuration (region, model mapping)."""


class LLMThrottledError(LLMError):
    """Provider throttling / transient capacity error (internal; drives retry)."""
    retryable = True


class LLMUnavailableError(LLMError):
    """The model could not be reached or its response could not be parsed."""
    retryable = True
