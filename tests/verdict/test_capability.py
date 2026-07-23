"""Capability priors and the cold-start selection fix."""

from llmux.verdict.capability import (
    capability_prior,
    category_fit,
    size_billions,
)
from llmux.verdict.config import VerdictConfig
from llmux.verdict.models import CostStatus, ModelRef
from llmux.verdict.selector import select_models
from llmux.verdict.storage import ModelStats


def _model(provider: str, model_id: str, family: str, *, reasoning: bool = False):
    return ModelRef(
        provider=provider,
        model_id=model_id,
        family=family,
        supports_reasoning=reasoning,
        cost_status=CostStatus.FREE_TIER,
    )


def _cold_stats(key: str, category: str) -> ModelStats:
    return ModelStats(model_key=key, category=category)


def test_size_parsing():
    assert size_billions("nemotron-3-ultra-550b-a55b") == 550.0
    assert size_billions("gpt-oss-120b") == 120.0
    assert size_billions("qwen3.5-397b-a17b") == 397.0
    assert size_billions("llama-3.3-70b-versatile") == 70.0
    assert size_billions("models/gemini-2.5-flash") is None


def test_prior_orders_by_size():
    big = capability_prior(_model("nim", "nemotron-3-super-120b-a12b", "nemotron"))
    small = capability_prior(_model("groq", "allam-2-7b", "unknown"))
    assert big > small


def test_reasoning_bonus_and_small_penalty():
    r1 = capability_prior(_model("github", "deepseek-r1", "deepseek", reasoning=True))
    plain = capability_prior(_model("github", "deepseek-chat", "deepseek"))
    assert r1 > plain
    nano = capability_prior(_model("or", "nemotron-3-nano-30b", "nemotron"))
    full = capability_prior(_model("or", "nemotron-3-super-120b", "nemotron"))
    assert nano < full


def test_category_fit_favours_coders_for_code():
    coder = _model("or", "qwen3-coder", "qwen")
    generalist = _model("groq", "llama-3.3-70b", "llama")
    assert category_fit(coder, "software_engineering") > category_fit(
        generalist, "software_engineering"
    )


def test_cold_start_selection_prefers_strong_models():
    # No history at all: the prior must steer selection to the strong models,
    # not the weak 7B/8B ones (the bug observed live).
    candidates = [
        _model("groq", "allam-2-7b", "unknown"),
        _model("groq", "llama-3.1-8b-instant", "llama"),
        _model(
            "open_router",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "nemotron",
            reasoning=True,
        ),
        _model("github_models", "deepseek/deepseek-r1", "deepseek", reasoning=True),
    ]
    chosen = select_models(
        candidates,
        _cold_stats,
        VerdictConfig(),
        category="architecture",
        count=2,
    )
    chosen_ids = {m.model_id for m in chosen}
    assert "allam-2-7b" not in chosen_ids
    assert "llama-3.1-8b-instant" not in chosen_ids
    assert len(chosen) == 2
