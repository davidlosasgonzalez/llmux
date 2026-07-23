"""Tests for advisory model configuration lint."""

from dataclasses import dataclass

from llmux.application.model_lint import lint_model_config


@dataclass
class FakeModelConfig:
    model: str = "nvidia_nim/nvidia/nemotron-3-super-120b-a12b"
    model_fable: str | None = None
    model_opus: str | None = None
    model_fallbacks: str = ""
    model_classifier: str | None = None


def test_clean_default_config_has_no_warnings():
    assert lint_model_config(FakeModelConfig()) == []


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
        model="open_router/deepseek/deepseek-v4-flash",
        model_fallbacks=(
            "open_router/nvidia/nemotron-3-ultra-550b-a55b:free,"
            "open_router/moonshotai/kimi-k2.6"
        ),
    )
    warnings = lint_model_config(config)
    assert len(warnings) == 1
    assert "open_router" in warnings[0]
    assert "outage" in warnings[0]


def test_multi_provider_fallback_chain_does_not_warn():
    config = FakeModelConfig(
        model="open_router/deepseek/deepseek-v4-flash",
        model_fallbacks="gemini/models/gemini-3.5-flash,kimi/kimi-k2.6",
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


def test_ref_without_provider_prefix_is_ignored():
    config = FakeModelConfig(
        model_opus="not-a-ref",
        model_classifier="not-a-ref",
        model_fallbacks="not-a-ref",
    )
    warnings = lint_model_config(config)
    assert all("small model" not in w for w in warnings)
    assert all("MODEL_CLASSIFIER" not in w for w in warnings)
