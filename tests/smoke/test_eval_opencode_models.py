"""Unit tests for C1 candidate selection (no live proxy / OpenCode)."""

from smoke.scripts.eval_opencode_models import (
    provider_model_refs,
    select_candidates,
    update_managed_model,
)


def test_provider_model_refs_strips_anthropic_prefix():
    refs = provider_model_refs(
        [
            "anthropic/groq/llama-3.3-70b-versatile",
            "claude-3-freecc-no-thinking/groq/llama-3.3-70b-versatile",
            "cerebras/gpt-oss-120b",
        ]
    )
    assert "groq/llama-3.3-70b-versatile" in refs
    assert "cerebras/gpt-oss-120b" in refs
    assert all("no-thinking" not in ref for ref in refs)


def test_select_candidates_excludes_github_and_prefers_providers():
    refs = [
        "github_models/deepseek/deepseek-v3-0324",
        "open_router/openai/gpt-oss-120b",
        "cerebras/gpt-oss-120b",
        "nvidia_nim/moonshotai/kimi-k2.6",
        "groq/llama-3.3-70b-versatile",
        "gemini/models/gemini-2.5-flash",
        "nvidia_nim/deepseek-ai/deepseek-v4-flash",
        "groq/qwen/qwen3.6-27b",
    ]
    chosen = select_candidates(refs, max_models=6)
    assert "github_models/deepseek/deepseek-v3-0324" not in chosen
    assert "cerebras/gpt-oss-120b" in chosen  # preferred over open_router
    assert any("kimi-k2" in ref for ref in chosen)
    assert len(chosen) <= 6
    assert len(chosen) >= 4


def test_update_managed_model_replaces_or_appends(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=1\nMODEL=old/model\n", encoding="utf-8")
    update_managed_model(env_path, "cerebras/gpt-oss-120b")
    text = env_path.read_text(encoding="utf-8")
    assert "MODEL=cerebras/gpt-oss-120b" in text
    assert "MODEL=old/model" not in text
