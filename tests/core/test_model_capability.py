"""Tests for static model capability heuristics."""

from free_claude_code.core.model_capability import known_context_window


def test_known_context_window_by_family():
    assert known_context_window("gemini-flash-latest") == 1_048_576
    assert known_context_window("models/gemini-3.1-flash-lite") == 1_048_576
    assert known_context_window("deepseek/deepseek-v4-flash") == 131_072
    assert known_context_window("moonshotai/kimi-k2.5") == 262_144
    assert known_context_window("llama-3.3-70b-versatile") == 131_072
    assert known_context_window("gpt-oss-120b") == 131_072


def test_known_context_window_specific_token_wins():
    assert known_context_window("command-a-plus-05-2026") == 262_144
    assert known_context_window("command-r7b") == 131_072
    assert known_context_window("nvidia/nemotron-3-ultra-550b-a55b:free") == 1_000_000


def test_known_context_window_unknown_model_returns_none():
    assert known_context_window("somebrand-newmodel-9000") is None


def test_known_context_window_does_not_match_provider_lookalikes():
    # ``gemma`` is not ``gemini``: without the provider prefix (stripped by
    # callers) the model name alone must not claim a family window.
    assert known_context_window("gemma-3-27b-it") is None
    assert known_context_window("codestral-latest") is None
