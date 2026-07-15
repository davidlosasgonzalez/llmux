"""Free-only gating, discovery skipping and diversity selection."""

import pytest

from free_claude_code.council import provider_policy as pp
from free_claude_code.council.config import CouncilConfig
from free_claude_code.council.discovery import discover_models
from free_claude_code.council.models import CostStatus
from free_claude_code.council.selector import select_models
from free_claude_code.council.storage import ModelStats
from tests.council.support import make_model


def _stats(_key: str, _cat: str) -> ModelStats:
    return ModelStats(model_key=_key, category=_cat)


def test_paid_model_never_selected_in_free_mode():
    enabled = frozenset({"deepseek", "kimi", "groq"})
    assert not pp.is_model_eligible(
        "deepseek", "deepseek-chat", allow_paid=False, enabled_providers=enabled
    )
    assert not pp.is_model_eligible(
        "kimi", "kimi-k2", allow_paid=False, enabled_providers=enabled
    )
    # ...but the free provider in the same set is fine.
    assert pp.is_model_eligible(
        "groq", "llama-3.3-70b", allow_paid=False, enabled_providers=enabled
    )


def test_unknown_cost_model_never_selected():
    # An OpenRouter model without the ':free' suffix is treated as paid/unknown.
    enabled = frozenset({"open_router"})
    assert pp.classify_model_cost("open_router", "meta/llama-3") == CostStatus.PAID
    assert not pp.is_model_eligible(
        "open_router", "meta/llama-3", allow_paid=False, enabled_providers=enabled
    )
    # The ':free' variant is allowed.
    assert pp.classify_model_cost("open_router", "meta/llama-3:free") == (
        CostStatus.VERIFIED_FREE
    )
    assert pp.is_model_eligible(
        "open_router", "meta/llama-3:free", allow_paid=False, enabled_providers=enabled
    )


def test_unclassified_provider_defaults_to_paid():
    # A provider with no policy entry must be treated as paid (safe default).
    assert pp.policy_for("some_new_provider").requires_card is True
    assert not pp.is_model_eligible(
        "some_new_provider",
        "x",
        allow_paid=False,
        enabled_providers=frozenset({"some_new_provider"}),
    )


def test_enabling_paid_provider_still_excluded_in_free_mode():
    # Enabling a card-required provider in config must NOT bypass the cost gate.
    enabled = frozenset({"deepseek"})
    assert not pp.is_provider_eligible(
        "deepseek", allow_paid=False, enabled_providers=enabled
    )
    assert pp.is_provider_eligible(
        "deepseek", allow_paid=True, enabled_providers=enabled
    )


def test_selector_prefers_distinct_providers_and_families():
    candidates = [
        make_model("groq", "gpt-oss-120b", family="gpt-oss"),
        make_model("cerebras", "gpt-oss-120b", family="gpt-oss"),
        make_model("gemini", "models/gemini-flash", family="gemini"),
        make_model("nvidia_nim", "nvidia/nemotron", family="nemotron"),
    ]
    config = CouncilConfig()
    chosen = select_models(
        candidates, _stats, config, category="general_reasoning", count=3
    )
    providers = [m.provider for m in chosen]
    families = [m.family for m in chosen]
    assert len(set(providers)) == len(providers)  # no provider twice
    # The two gpt-oss twins should not both be picked before diverse families.
    assert families.count("gpt-oss") <= 1


@pytest.mark.asyncio
async def test_discovery_skips_unauthenticated_and_paid(monkeypatch):
    from free_claude_code.config.settings import Settings

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.setenv("MODEL", "groq/llama-3.3-70b-versatile")
    settings = Settings()

    async def lister(provider: str) -> list[str]:
        return {"groq": ["llama-3.3-70b-versatile", "qwen-3-32b"]}.get(provider, [])

    models, failures = await discover_models(
        ["groq", "nvidia_nim", "deepseek"],
        lister,
        settings,
        allow_paid=False,
        enabled_providers=frozenset({"groq", "nvidia_nim", "deepseek"}),
    )
    providers = {m.provider for m in models}
    assert providers == {"groq"}  # nvidia has no key, deepseek is paid
    failure_providers = {f.provider for f in failures}
    assert "nvidia_nim" in failure_providers
    assert "deepseek" in failure_providers
