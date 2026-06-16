"""Tests for libs/redaction (no external deps; pure regex). Run with: pytest -q."""
from __future__ import annotations

from libs.redaction.redactor import DefaultRedactor, NoOpRedactor


def test_masks_common_pii_and_secrets():
    r = DefaultRedactor()
    text = (
        "User alice@example.com from 10.0.3.17 used key AKIAABCDEFGHIJKLMNOP "
        "with token eyJhbGciOi.AAAAAAAAAA.BBBBBBBBBB and card 4111 1111 1111 1111."
    )
    out = r.redact(text)
    for secret in ("alice@example.com", "10.0.3.17", "AKIAABCDEFGHIJKLMNOP",
                   "4111 1111 1111 1111"):
        assert secret not in out.text
    assert "<EMAIL_1>" in out.text
    assert "<IP_1>" in out.text
    assert "<AWS_KEY_1>" in out.text
    assert out.total >= 4


def test_private_key_block_is_removed():
    r = DefaultRedactor()
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc123\n-----END RSA PRIVATE KEY-----"
    out = r.redact(text)
    assert "PRIVATE KEY" not in out.text
    assert "<PRIVATE_KEY_1>" in out.text


def test_consistent_placeholder_for_repeated_value():
    r = DefaultRedactor()
    out = r.redact("contact bob@corp.com or bob@corp.com again")
    # same email -> same placeholder, counted once
    assert out.text.count("<EMAIL_1>") == 2
    assert out.counts.get("email") == 1


def test_preserves_operational_signal():
    r = DefaultRedactor()
    text = ("service checkout-service raised ERR_TIMEOUT_503 at commit "
            "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0 in pod checkout-7f9c")
    out = r.redact(text)
    # operational identifiers must survive (not PII/secrets)
    assert "checkout-service" in out.text
    assert "ERR_TIMEOUT_503" in out.text
    assert "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0" in out.text  # git SHA preserved
    assert out.total == 0


def test_noop_redactor_passthrough():
    out = NoOpRedactor().redact("alice@example.com 10.0.0.1")
    assert out.text == "alice@example.com 10.0.0.1"
    assert out.total == 0
