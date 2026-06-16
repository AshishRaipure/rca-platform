"""libs/llm response type.

Structurally satisfies the ``LLMResponse`` Protocol the agents declare (text, model_id,
model_version, input_tokens, output_tokens, latency_ms).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model_id: str
    model_version: Optional[str]
    input_tokens: int
    output_tokens: int
    latency_ms: int
