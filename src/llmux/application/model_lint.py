"""Advisory lint for questionable model configuration combinations.

Users pick any provider/model combination they like; LLMux never blocks a
choice. This module derives non-blocking warnings from the same static name
heuristics the auto-router uses (:mod:`llmux.core.model_capability`) so
foot-guns surface at startup and in the Admin UI instead of as silent
quality or availability problems.
"""

from typing import Protocol

from llmux.config.model_refs import (
    parse_model_fallbacks,
    parse_model_name,
    parse_provider_type,
)
from llmux.core.model_capability import has_small_hint, size_billions

_SMALL_SIZE_BILLIONS = 25.0
_CLASSIFIER_HEAVY_BILLIONS = 130.0
_HIGH_TIER_SLOTS = ("MODEL_OPUS", "MODEL_FABLE")


class LintableModelConfig(Protocol):
    model: str
    model_fable: str | None
    model_opus: str | None
    model_fallbacks: str
    model_classifier: str | None


def lint_model_config(settings: LintableModelConfig) -> list[str]:
    """Return advisory warnings for the configured model combination."""

    warnings: list[str] = []
    warnings.extend(_small_model_in_high_tier(settings))
    warnings.extend(_fallback_chain_warnings(settings))
    warnings.extend(_heavy_classifier(settings))
    return warnings


def _looks_small(model_ref: str) -> bool:
    model_id = parse_model_name(model_ref)
    if has_small_hint(model_id):
        return True
    size = size_billions(model_id)
    return size is not None and size < _SMALL_SIZE_BILLIONS


def _small_model_in_high_tier(settings: LintableModelConfig) -> list[str]:
    warnings: list[str] = []
    slots = (("MODEL_OPUS", settings.model_opus), ("MODEL_FABLE", settings.model_fable))
    for slot, model_ref in slots:
        if model_ref is None or "/" not in model_ref:
            continue
        if _looks_small(model_ref):
            warnings.append(
                f"{slot}={model_ref} looks like a small model for the "
                "hardest-tier slot; complex requests may underperform. "
                "Consider a larger or reasoning-tuned model."
            )
    return warnings


def _fallback_chain_warnings(settings: LintableModelConfig) -> list[str]:
    fallbacks = parse_model_fallbacks(settings.model_fallbacks)
    if not fallbacks:
        return []

    warnings: list[str] = []
    chain = [settings.model, *fallbacks]

    seen: set[str] = set()
    for model_ref in chain:
        if model_ref in seen:
            warnings.append(
                f"{model_ref} appears more than once across MODEL and "
                "MODEL_FALLBACKS; duplicate entries never add resilience."
            )
        seen.add(model_ref)

    providers = {parse_provider_type(ref) for ref in chain if "/" in ref}
    if len(providers) == 1:
        provider = next(iter(providers))
        warnings.append(
            f"MODEL and every MODEL_FALLBACKS entry use the {provider} "
            "provider; a provider outage leaves no working fallback. "
            "Mix at least one other provider into the chain."
        )
    return warnings


def _heavy_classifier(settings: LintableModelConfig) -> list[str]:
    model_ref = settings.model_classifier
    if model_ref is None or "/" not in model_ref:
        return []
    size = size_billions(parse_model_name(model_ref))
    if size is None or size < _CLASSIFIER_HEAVY_BILLIONS:
        return []
    return [
        f"MODEL_CLASSIFIER={model_ref} is a large model for a trivial "
        "grading task; a small fast model cuts latency and quota use."
    ]
