"""Redaction / masking layer (ADR D-2).

A reusable component with two intended enforcement points:
  * ingestion-time (corpus hygiene) — redact before chunks are embedded;
  * prompt-time (defense-in-depth) — applied at the LLM boundary in ``libs/llm`` before any prompt
    reaches Bedrock.

Design intent: mask *sensitive* tokens (PII, secrets, credentials, identifiers) while **preserving
operational signal** (service names, error codes, stack frames, git SHAs) — blanket masking would
gut RCA quality. Identical values map to the same placeholder within a single ``redact`` call so
correlation still works.

This is a deliberately conservative, dependency-free PoC detector set. Production would plug in a
dedicated PII engine (Amazon Comprehend / Microsoft Presidio) behind the same ``Redactor`` port —
that tooling choice is an open decision in the ADR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)  # detector -> distinct values masked

    @property
    def total(self) -> int:
        return sum(self.counts.values())


class Redactor(Protocol):
    def redact(self, text: str) -> RedactionResult: ...


# Order matters: longer / more-specific patterns first so they win before broad ones.
# Each entry: (detector_name, placeholder_prefix, compiled_pattern).
_DEFAULT_DETECTORS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("private_key", "PRIVATE_KEY",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("jwt", "JWT",
     re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer", "BEARER",
     re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")),
    ("aws_access_key", "AWS_KEY",
     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("credit_card", "CC",
     re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b")),
    ("ssn", "SSN",
     re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", "EMAIL",
     re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone", "PHONE",
     re.compile(r"\b(?:\+?1[ -]?)?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b")),
    ("ip", "IP",
     re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)


class DefaultRedactor:
    def __init__(self, detectors: tuple[tuple[str, str, re.Pattern], ...] | None = None) -> None:
        self._detectors = detectors if detectors is not None else _DEFAULT_DETECTORS

    def redact(self, text: str) -> RedactionResult:
        if not text:
            return RedactionResult(text=text or "", counts={})

        mapping: dict[str, str] = {}     # matched value -> stable placeholder
        counters: dict[str, int] = {}    # detector -> running index
        counts: dict[str, int] = {}      # detector -> distinct values masked

        def placeholder_for(value: str, name: str, prefix: str) -> str:
            existing = mapping.get(value)
            if existing is not None:
                return existing
            counters[name] = counters.get(name, 0) + 1
            ph = f"<{prefix}_{counters[name]}>"
            mapping[value] = ph
            counts[name] = counts.get(name, 0) + 1
            return ph

        out = text
        for name, prefix, pattern in self._detectors:
            out = pattern.sub(
                lambda m, n=name, p=prefix: placeholder_for(m.group(0), n, p), out)
        return RedactionResult(text=out, counts=counts)


class NoOpRedactor:
    """Explicit pass-through. Selecting this is a conscious decision to disable redaction."""

    def redact(self, text: str) -> RedactionResult:
        return RedactionResult(text=text or "", counts={})
