"""Tests for libs/llm BedrockLLMClient (fake runtime; no boto3, no network).

Run with: pytest -q  (requires pytest, pytest-asyncio, pydantic v2). Syntax-checked here.
"""
from __future__ import annotations

import json

import pytest

from contracts.enums import ModelTier
from libs.llm.client import BedrockLLMClient
from libs.llm.config import LLMConfig
from libs.llm.errors import LLMConfigError, LLMUnavailableError
from libs.redaction.redactor import DefaultRedactor


class _Body:
    def __init__(self, data: dict):
        self._raw = json.dumps(data).encode()

    def read(self) -> bytes:
        return self._raw


class _ClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeRuntime:
    """Records the last invoke and returns a canned Anthropic Messages response."""

    def __init__(self, *, raise_seq=None, text="root cause: thread pool exhaustion"):
        self.calls = 0
        self.last_model_id = None
        self.last_body = None
        self._raise_seq = list(raise_seq or [])
        self._text = text

    def invoke_model(self, *, modelId, body, accept, contentType):
        self.calls += 1
        self.last_model_id = modelId
        self.last_body = json.loads(body)
        if self._raise_seq:
            exc = self._raise_seq.pop(0)
            if exc is not None:
                raise exc
        return {"body": _Body({
            "content": [{"type": "text", "text": self._text}],
            "model": modelId, "stop_reason": "end_turn",
            "usage": {"input_tokens": 123, "output_tokens": 45},
        })}


def _config():
    return LLMConfig(
        region="us-east-1",
        model_ids={"fast": "m-fast", "mid": "m-mid", "top": "m-top"},
        backoff_base_s=0.0, max_retries=2,
    )


@pytest.mark.asyncio
async def test_tier_routes_to_configured_model():
    rt = FakeRuntime()
    client = BedrockLLMClient(config=_config(), runtime=rt)
    await client.complete(system="s", user="u", model_tier=ModelTier.top,
                          max_tokens=256, temperature=0.0, request_id="r", timeout_s=5)
    assert rt.last_model_id == "m-top"


@pytest.mark.asyncio
async def test_redaction_applied_before_send():
    rt = FakeRuntime()
    client = BedrockLLMClient(config=_config(), redactor=DefaultRedactor(), runtime=rt)
    await client.complete(
        system="You are an SRE assistant.",
        user="incident from alice@example.com on 10.2.3.4 key AKIAABCDEFGHIJKLMNOP",
        model_tier=ModelTier.mid, max_tokens=256, temperature=0.0, request_id="r", timeout_s=5)
    sent = rt.last_body["messages"][0]["content"][0]["text"]
    assert "alice@example.com" not in sent
    assert "10.2.3.4" not in sent
    assert "AKIAABCDEFGHIJKLMNOP" not in sent
    assert "<EMAIL_1>" in sent and "<IP_1>" in sent


@pytest.mark.asyncio
async def test_parses_text_and_usage():
    rt = FakeRuntime(text="hello")
    client = BedrockLLMClient(config=_config(), runtime=rt)
    resp = await client.complete(system="s", user="u", model_tier=ModelTier.fast,
                                 max_tokens=256, temperature=0.0, request_id="r", timeout_s=5)
    assert resp.text == "hello"
    assert resp.input_tokens == 123
    assert resp.output_tokens == 45
    assert resp.model_id == "m-fast"
    assert resp.latency_ms >= 0


@pytest.mark.asyncio
async def test_throttling_is_retried_then_succeeds():
    rt = FakeRuntime(raise_seq=[_ClientError("ThrottlingException"), None])
    client = BedrockLLMClient(config=_config(), runtime=rt)
    resp = await client.complete(system="s", user="u", model_tier=ModelTier.mid,
                                 max_tokens=256, temperature=0.0, request_id="r", timeout_s=5)
    assert rt.calls == 2
    assert resp.text


@pytest.mark.asyncio
async def test_terminal_failure_maps_to_unavailable():
    rt = FakeRuntime(raise_seq=[ValueError("boom"), ValueError("boom"), ValueError("boom")])
    client = BedrockLLMClient(config=_config(), runtime=rt)
    with pytest.raises(LLMUnavailableError):
        await client.complete(system="s", user="u", model_tier=ModelTier.mid,
                              max_tokens=256, temperature=0.0, request_id="r", timeout_s=5)


@pytest.mark.asyncio
async def test_unmapped_tier_raises_config_error():
    client = BedrockLLMClient(config=LLMConfig(region="us-east-1", model_ids={"fast": "m-fast"}),
                              runtime=FakeRuntime())
    with pytest.raises(LLMConfigError):
        await client.complete(system="s", user="u", model_tier=ModelTier.top,
                              max_tokens=256, temperature=0.0, request_id="r", timeout_s=5)
