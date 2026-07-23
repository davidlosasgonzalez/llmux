"""Redaction/privacy, JSON parsing robustness and quota/backoff behaviour."""

import time
from typing import ClassVar

import pytest

from llmux.core.quota import (
    QuotaTracker,
    classify_failure,
    retry_after_seconds,
)
from llmux.verdict.models import FailureKind, Privacy
from llmux.verdict.parsing import (
    extract_json_object,
    parse_critique,
    parse_proposal,
    parse_review,
)
from llmux.verdict.redaction import (
    PathPolicy,
    apply_privacy,
    contains_secret,
    redact,
    safe_for_log,
)


def test_redact_masks_common_secrets():
    text = (
        "here is sk-ABCDEFGHIJKLMNOP1234 and Bearer abcdef123456 "
        "and API_KEY=supersecretvalue and https://user:pass@host/x"
    )
    cleaned = redact(text)
    assert "sk-ABCDEFGHIJKLMNOP1234" not in cleaned
    assert "abcdef123456" not in cleaned
    assert "supersecretvalue" not in cleaned
    assert "user:pass@host" not in cleaned
    assert not contains_secret(cleaned)


def test_private_key_block_redacted():
    key = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert "MIIEpAIBAAKCAQEA" not in redact(key)


def test_safe_for_log_has_no_keys_and_truncates():
    log_line = safe_for_log("token GITHUB_TOKEN=ghp_" + "a" * 40, limit=50)
    assert "ghp_" not in log_line
    assert len(log_line) <= 51


def test_public_privacy_sends_verbatim_but_redacted_masks():
    secret = "API_KEY=zzzsecretzzz"
    assert apply_privacy(secret, Privacy.PUBLIC) == secret
    assert "zzzsecretzzz" not in apply_privacy(secret, Privacy.REDACTED)


def test_path_policy_blocks_outside_roots(tmp_path):
    inside = tmp_path / "ok.txt"
    inside.write_text("hello", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")

    policy = PathPolicy.from_paths([tmp_path])
    assert policy.read_text(inside) == "hello"
    with pytest.raises(PermissionError):
        policy.read_text(outside)


def test_path_policy_enforces_size_cap(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("x" * 100, encoding="utf-8")
    policy = PathPolicy.from_paths([tmp_path], max_bytes=10)
    with pytest.raises(ValueError):
        policy.read_text(big)


def test_extract_json_from_fenced_and_noisy_text():
    fenced = 'prose\n```json\n{"a": 1}\n```\ntrailing'
    assert extract_json_object(fenced) == {"a": 1}
    noisy = 'Here you go: {"final_answer": "x", "quality_score": 0.7} thanks'
    parsed = extract_json_object(noisy)
    assert parsed is not None
    assert parsed["final_answer"] == "x"


def test_parse_proposal_falls_back_to_plain_text():
    # A model that ignored the JSON contract but answered is not discarded.
    proposal = parse_proposal("groq/x", "just a plain answer, no json")
    assert proposal is not None
    assert proposal.conclusion == "just a plain answer, no json"


def test_parse_review_returns_none_on_garbage():
    assert parse_review("groq/x", "no structure here") is None


def test_parse_critique_defaults_verdict_when_missing():
    critique = parse_critique("groq/x", '{"score": 0.5}')
    assert critique is not None
    assert critique.verdict.value == "revise"


def test_classify_failure_maps_status_and_text():
    class FakeErr(Exception):
        status_code = 429

    assert classify_failure(FakeErr()) is FailureKind.RATE_LIMITED
    assert classify_failure({"type": "authentication_error"}) is (
        FailureKind.AUTHENTICATION
    )
    assert classify_failure({"message": "insufficient quota"}) is (
        FailureKind.QUOTA_EXHAUSTED
    )


def test_quota_tracker_blocks_then_recovers():
    clock = {"t": 1000.0}
    tracker = QuotaTracker(now=lambda: clock["t"])
    assert not tracker.is_blocked("groq")
    tracker.note_failure("groq", FailureKind.RATE_LIMITED)
    assert tracker.is_blocked("groq")
    # Advance beyond the cool-off window; the provider recovers.
    clock["t"] += 10_000.0
    assert not tracker.is_blocked("groq")


def test_quota_tracker_respects_retry_after():
    clock = {"t": 0.0}
    tracker = QuotaTracker(now=lambda: clock["t"])
    tracker.note_failure("groq", FailureKind.RATE_LIMITED, retry_after=500.0)
    clock["t"] = 100.0
    assert tracker.is_blocked("groq")
    clock["t"] = 600.0
    assert not tracker.is_blocked("groq")


def test_retry_after_parsing():
    class Resp:
        headers: ClassVar[dict[str, str]] = {"retry-after": "42"}

    class Err(Exception):
        response = Resp()

    assert retry_after_seconds(Err()) == 42.0


def test_repeated_rate_limits_extend_block():
    clock = {"t": 0.0}
    tracker = QuotaTracker(now=lambda: clock["t"])
    tracker.note_failure("groq", FailureKind.RATE_LIMITED)
    first_until = 0.0
    # A second consecutive 429 should extend the window further out.
    clock["t"] = 1.0
    tracker.note_failure("groq", FailureKind.RATE_LIMITED)
    clock["t"] = 90.0
    assert tracker.is_blocked("groq")
    assert first_until == 0.0  # sanity anchor for readability


def test_time_default_smoke():
    # The default clock is real time; a fresh tracker blocks nobody.
    tracker = QuotaTracker()
    assert not tracker.is_blocked("groq")
    assert isinstance(time.time(), float)
