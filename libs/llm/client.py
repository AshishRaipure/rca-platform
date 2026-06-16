"""Bedrock-backed, tier-routed LLM client (ADR D-1).

Implements the agents' ``LLMClient.complete`` contract. Two things are true by construction here:
  * **Redaction is applied at this boundary** — every ``system``/``user`` prompt passes through the
    redactor before the request is built, so no un-redacted content can reach the model. Disabling
    it requires explicitly injecting ``NoOpRedactor``.
  * **Inference stays in-account** — Bedrock runs in the platform's AWS account/region (no external
    model-API egress), satisfying the data-residency requirement.

boto3 is synchronous, so the call runs on a worker thread via ``asyncio.to_thread`` and is bounded
by ``timeout_s``. boto3/botocore are imported lazily (absent in this validation environment).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Optional

from contracts.enums import ModelTier
from libs.llm.config import LLMConfig
from libs.llm.errors import LLMConfigError, LLMThrottledError, LLMUnavailableError
from libs.llm.types import LLMResponse
from libs.redaction.redactor import DefaultRedactor, Redactor

logger = logging.getLogger("libs.llm.bedrock")

# Bedrock/botocore error codes treated as transient throttling/capacity issues.
_THROTTLE_CODES = frozenset({
    "ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException",
    "ModelTimeoutException", "InternalServerException", "ServiceQuotaExceededException",
})


def _error_code(exc: Exception) -> Optional[str]:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return (response.get("Error") or {}).get("Code")
    return None


class BedrockLLMClient:
    def __init__(
        self,
        *,
        config: Optional[LLMConfig] = None,
        redactor: Optional[Redactor] = None,
        runtime: Optional[Any] = None,
    ) -> None:
        self._config = config or LLMConfig()
        # redaction on by default — passing NoOpRedactor is the only way to disable it
        self._redactor: Redactor = redactor if redactor is not None else DefaultRedactor()
        self._runtime = runtime  # inject a bedrock-runtime client (tests); else built lazily

    def _client(self) -> Any:
        if self._runtime is None:
            import boto3  # lazy
            from botocore.config import Config as BotoConfig

            region = self._config.region or os.environ.get(self._config.region_env)
            if not region:
                raise LLMConfigError("AWS region not configured for Bedrock")
            self._runtime = boto3.client(
                "bedrock-runtime", region_name=region,
                config=BotoConfig(
                    connect_timeout=self._config.connect_timeout_s,
                    read_timeout=self._config.read_timeout_s,
                    retries={"max_attempts": 0},  # we own retry/backoff
                ),
            )
        return self._runtime

    async def complete(
        self, *, system: str, user: str, model_tier: ModelTier, max_tokens: int,
        temperature: float, request_id: str, timeout_s: float,
    ) -> LLMResponse:
        model_id = self._config.model_for(model_tier)
        if not model_id:
            raise LLMConfigError(f"no model mapped for tier {model_tier.value}")

        # --- redaction boundary (by construction, before anything leaves the process) ---
        red_system = self._redactor.redact(system)
        red_user = self._redactor.redact(user)
        redacted = red_system.total + red_user.total
        if redacted:
            logger.debug("redacted %d sensitive item(s) before model call (req=%s)",
                         redacted, request_id)

        payload = json.dumps({
            "anthropic_version": self._config.anthropic_version,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": red_system.text,
            "messages": [{"role": "user", "content": [{"type": "text", "text": red_user.text}]}],
        })

        attempt = 0
        start = time.monotonic()
        while True:
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(self._invoke, model_id, payload), timeout=timeout_s)
                break
            except LLMThrottledError as exc:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise LLMUnavailableError(f"bedrock throttled after retries: {exc}") from exc
            except asyncio.TimeoutError as exc:
                raise LLMUnavailableError("bedrock call timed out") from exc
            except LLMConfigError:
                raise
            except Exception as exc:
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise LLMUnavailableError(f"bedrock invocation failed: {exc}") from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        return self._parse(raw, model_id, latency_ms)

    def _invoke(self, model_id: str, payload: str) -> dict[str, Any]:
        client = self._client()
        try:
            resp = client.invoke_model(
                modelId=model_id, body=payload,
                accept="application/json", contentType="application/json")
        except Exception as exc:
            if _error_code(exc) in _THROTTLE_CODES:
                raise LLMThrottledError(str(exc)) from exc
            raise
        body = resp.get("body") if isinstance(resp, dict) else None
        data = body.read() if hasattr(body, "read") else body
        try:
            return json.loads(data)
        except Exception as exc:
            raise LLMUnavailableError(f"unparseable bedrock response: {exc}") from exc

    def _parse(self, data: dict[str, Any], model_id: str, latency_ms: int) -> LLMResponse:
        content = data.get("content") or []
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model_id=data.get("model") or model_id,
            model_version=self._config.model_version_label,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            latency_ms=latency_ms,
        )

    def _backoff(self, attempt: int) -> float:
        return min(self._config.backoff_max_s, self._config.backoff_base_s * (2 ** attempt))


def make_bedrock_llm_client(
    config: Optional[LLMConfig] = None, *, redactor: Optional[Redactor] = None,
) -> BedrockLLMClient:
    """Composition root. Resolves config from env when not supplied; redaction on by default."""
    return BedrockLLMClient(config=config or LLMConfig.from_env(), redactor=redactor)
