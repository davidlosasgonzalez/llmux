"""Tests for advisory model configuration lint."""

from dataclasses import dataclass

from llmux.application.model_lint import (
    client_config_recommendation,
    lint_model_config,
)


@dataclass
class FakeModelConfig:
    model: str = "nvidia_nim/nvidia/nemotron-3-super-120b-a12b"
    model_fable: str | None = None
    model_opus: str | None = None
    model_sonnet: str | None = None
    model_haiku: str | None = None
    model_fallbacks: str = ""
    # Default carries a large-window rescue tier so tests unrelated to the
    # context-ceiling lint stay isolated from it; that lint has its own tests
    # below with this explicitly set back to None.
    model_long_context: str | None = "gemini/models/gemini-3.5-flash"
    context_window_overrides: str = ""
    model_classifier: str | None = None


def test_clean_default_config_has_no_warnings():
    assert lint_model_config(FakeModelConfig()) == []


def test_flash_model_in_sonnet_slot_warns():
    config = FakeModelConfig(model_sonnet="open_router/deepseek/deepseek-v4-flash")
    warnings = lint_model_config(config)
    assert any("MODEL_SONNET" in w and "cheap" in w for w in warnings)


def test_weak_model_in_sonnet_slot_warns():
    config = FakeModelConfig(model_sonnet="cerebras/gpt-oss-120b")
    warnings = lint_model_config(config)
    assert any("MODEL_SONNET" in w and "weak for agent coding" in w for w in warnings)


def test_strong_model_in_sonnet_slot_does_not_warn():
    config = FakeModelConfig(model_sonnet="open_router/moonshotai/kimi-k2.6")
    assert lint_model_config(config) == []


def test_gemini_pro_in_sonnet_slot_does_not_warn_as_cheap():
    config = FakeModelConfig(model_sonnet="gemini/models/gemini-3.1-pro-preview")
    warnings = lint_model_config(config)
    assert not any("cheap" in w or "weak for agent coding" in w for w in warnings)


def test_cheap_default_model_warns_when_sonnet_unset():
    config = FakeModelConfig(model="open_router/deepseek/deepseek-v4-flash")
    warnings = lint_model_config(config)
    assert any("MODEL=" in w and "MODEL_SONNET is unset" in w for w in warnings)


def test_small_hint_model_in_opus_slot_warns():
    config = FakeModelConfig(model_opus="gemini/models/gemini-3.1-flash-lite")
    warnings = lint_model_config(config)
    assert len(warnings) == 1
    assert "MODEL_OPUS" in warnings[0]
    assert "small model" in warnings[0]


def test_small_sized_model_in_fable_slot_warns():
    config = FakeModelConfig(model_fable="groq/llama-3.1-8b-instant")
    warnings = lint_model_config(config)
    assert len(warnings) == 1
    assert "MODEL_FABLE" in warnings[0]


def test_large_model_in_opus_slot_does_not_warn():
    config = FakeModelConfig(model_opus="kimi/kimi-k2.6")
    assert lint_model_config(config) == []


def test_unset_high_tier_slots_do_not_warn():
    assert lint_model_config(FakeModelConfig(model_opus=None, model_fable=None)) == []


def test_single_provider_fallback_chain_warns():
    config = FakeModelConfig(
        model="open_router/moonshotai/kimi-k2.6",
        model_fallbacks=(
            "open_router/nvidia/nemotron-3-ultra-550b-a55b:free,"
            "open_router/deepseek/deepseek-v4-flash"
        ),
    )
    warnings = lint_model_config(config)
    assert len(warnings) == 1
    assert "open_router" in warnings[0]
    assert "outage" in warnings[0]


def test_multi_provider_fallback_chain_does_not_warn():
    config = FakeModelConfig(
        model="open_router/moonshotai/kimi-k2.6",
        model_fallbacks="gemini/models/gemini-3.5-flash,cohere/command-a-plus-05-2026",
    )
    assert lint_model_config(config) == []


def test_empty_fallbacks_never_warn_about_providers():
    config = FakeModelConfig(model="zai/glm-5.2", model_fallbacks="")
    assert lint_model_config(config) == []


def test_duplicate_fallback_entry_warns():
    config = FakeModelConfig(
        model="zai/glm-5.2",
        model_fallbacks="kimi/kimi-k2.6,zai/glm-5.2",
    )
    warnings = lint_model_config(config)
    duplicates = [w for w in warnings if "more than once" in w]
    assert len(duplicates) == 1
    assert "zai/glm-5.2" in duplicates[0]


def test_duplicate_within_fallbacks_warns():
    config = FakeModelConfig(
        model="zai/glm-5.2",
        model_fallbacks="kimi/kimi-k2.6,kimi/kimi-k2.6",
    )
    warnings = lint_model_config(config)
    assert any("kimi/kimi-k2.6" in w and "more than once" in w for w in warnings)


def test_heavy_classifier_warns():
    config = FakeModelConfig(
        model_classifier="nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b"
    )
    warnings = lint_model_config(config)
    assert len(warnings) == 1
    assert "MODEL_CLASSIFIER" in warnings[0]


def test_small_classifier_does_not_warn():
    config = FakeModelConfig(model_classifier="groq/openai/gpt-oss-20b")
    assert lint_model_config(config) == []


def test_unset_classifier_does_not_warn():
    assert lint_model_config(FakeModelConfig(model_classifier=None)) == []


def test_narrow_chain_without_long_context_warns():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="open_router/deepseek/deepseek-v4-flash,cerebras/gpt-oss-120b",
        model_long_context=None,
    )
    warnings = lint_model_config(config)
    assert len(warnings) == 1
    assert "MODEL_LONG_CONTEXT" in warnings[0]


def test_narrow_chain_with_long_context_does_not_warn():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="open_router/deepseek/deepseek-v4-flash,cerebras/gpt-oss-120b",
        model_long_context="gemini/models/gemini-3.5-flash",
    )
    assert lint_model_config(config) == []


def test_chain_with_one_large_window_model_does_not_warn():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="kimi/kimi-k2.6",
        model_long_context=None,
    )
    assert lint_model_config(config) == []


def test_chain_with_unknown_window_model_does_not_warn():
    config = FakeModelConfig(
        model="cohere/aya-vision-32b",
        model_fallbacks="",
        model_long_context=None,
    )
    assert lint_model_config(config) == []


def test_override_lowers_effective_window_and_triggers_warning():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="kimi/kimi-k2.6",
        model_long_context=None,
        context_window_overrides="kimi-k2.6=50000",
    )
    warnings = lint_model_config(config)
    assert len(warnings) == 1
    assert "MODEL_LONG_CONTEXT" in warnings[0]


def test_override_raises_effective_window_and_clears_warning():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="open_router/deepseek/deepseek-v4-flash,cerebras/gpt-oss-120b",
        model_long_context=None,
        context_window_overrides="cerebras/gpt-oss-120b=300000",
    )
    assert lint_model_config(config) == []


def test_client_config_recommendation_for_narrow_default_chain():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="open_router/deepseek/deepseek-v4-flash,cerebras/gpt-oss-120b",
        model_long_context=None,
    )
    recommendation = client_config_recommendation(config)
    assert recommendation is not None
    assert "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60" in recommendation
    assert "131072" in recommendation


def test_client_config_recommendation_none_with_long_context():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="open_router/deepseek/deepseek-v4-flash,cerebras/gpt-oss-120b",
        model_long_context="gemini/models/gemini-3.5-flash",
    )
    assert client_config_recommendation(config) is None


def test_client_config_recommendation_none_with_large_window_in_chain():
    config = FakeModelConfig(
        model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
        model_fallbacks="kimi/kimi-k2.6",
        model_long_context=None,
    )
    assert client_config_recommendation(config) is None


def test_ref_without_provider_prefix_is_ignored():
    config = FakeModelConfig(
        model_opus="not-a-ref",
        model_classifier="not-a-ref",
        model_fallbacks="not-a-ref",
    )
    warnings = lint_model_config(config)
    assert all("small model" not in w for w in warnings)
    assert all("MODEL_CLASSIFIER" not in w for w in warnings)
