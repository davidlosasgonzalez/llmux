"""Tests for static model capability heuristics."""

from free_claude_code.core.model_capability import known_context_window


def test_known_context_window_by_family():
    assert known_context_window("gemini/gemini-flash-latest") == 1_048_576
    assert known_context_window("open_router/deepseek/deepseek-v4-flash") == 131_072
    assert known_context_window("open_router/moonshotai/kimi-k2.5") == 262_144
    assert known_context_window("groq/llama-3.3-70b-versatile") == 131_072
    assert known_context_window("cerebras/gpt-oss-120b") == 131_072


def test_known_context_window_specific_token_wins():
    assert known_context_window("cohere/command-a-plus-05-2026") == 262_144
    assert known_context_window("cohere/command-r7b") == 131_072
    assert (
        known_context_window("open_router/nvidia/nemotron-3-ultra-550b-a55b:free")
        == 1_000_000
    )


def test_known_context_window_unknown_model_returns_none():
    assert known_context_window("somebrand-newmodel-9000") is None
