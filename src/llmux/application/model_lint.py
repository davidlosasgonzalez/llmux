"""Advisory lint for questionable model configuration combinations.

Users pick any provider/model combination they like; LLMux never blocks a
choice. This module derives non-blocking warnings from the same static name
heuristics the auto-router uses (:mod:`llmux.core.model_capability`) so
foot-guns surface at startup and in the Admin UI instead of as silent
quality or availability problems.
"""

from typing import Protocol

from llmux.config.model_refs import (
    parse_context_window_overrides,
    parse_model_fallbacks,
    parse_model_name,
    parse_provider_type,
)
from llmux.core.model_capability import (
    has_cheap_coding_hint,
    has_small_hint,
    known_context_window,
    size_billions,
)

_SMALL_SIZE_BILLIONS = 25.0
_CLASSIFIER_HEAVY_BILLIONS = 130.0
# Below this, a chain has no room for a Claude Code conversation that has
# grown for a while; anything smaller needs a MODEL_LONG_CONTEXT rescue tier.
_LONG_CONTEXT_FLOOR = 200_000
# Context window Claude Code assumes for Sonnet/Opus absent other information;
# the client should compact before a narrower chain's ceiling, not after.
_CLIENT_ASSUMED_WINDOW = 200_000
_RECOMMENDATION_MARGIN_PCT = 5
_MIN_RECOMMENDED_PCT = 30


class LintableModelConfig(Protocol):
    model: str
    model_fable: str | None
    model_opus: str | None
    model_sonnet: str | None
    model_haiku: str | None
    model_fallbacks: str
    model_long_context: str | None
    context_window_overrides: str
    model_classifier: str | None


def lint_model_config(settings: LintableModelConfig) -> list[str]:
    """Return advisory warnings for the configured model combination."""

    warnings: list[str] = []
    warnings.extend(_small_model_in_high_tier(settings))
    warnings.extend(_cheap_model_in_coding_tier(settings))
    warnings.extend(_fallback_chain_warnings(settings))
    warnings.extend(_heavy_classifier(settings))
    warnings.extend(_context_ceiling_warning(settings))
    return warnings


def _looks_small(model_ref: str) -> bool:
    model_id = parse_model_name(model_ref)
    if has_small_hint(model_id):
        return True
    size = size_billions(model_id)
    return size is not None and size < _SMALL_SIZE_BILLIONS


def _looks_cheap_for_coding(model_ref: str) -> bool:
    return has_cheap_coding_hint(parse_model_name(model_ref))


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


def _cheap_model_in_coding_tier(settings: LintableModelConfig) -> list[str]:
    """Warn when Sonnet (or default MODEL) is a flash/haiku-class model."""

    warnings: list[str] = []
    sonnet = settings.model_sonnet
    if sonnet and "/" in sonnet and _looks_cheap_for_coding(sonnet):
        warnings.append(
            f"MODEL_SONNET={sonnet} looks like a cheap/high-throughput tier. "
            "Claude Code routes most coding agent turns here; flash/haiku-class "
            "models thrash on Edit/Bash loops. Prefer a stronger coding model "
            "(e.g. kimi-k2.6, deepseek-v4-pro, glm-5.x) and keep flash on "
            "MODEL_HAIKU."
        )
    # If Sonnet is unset, Claude's sonnet alias falls through to MODEL.
    if (sonnet is None or sonnet == "") and _looks_cheap_for_coding(settings.model):
        warnings.append(
            f"MODEL={settings.model} looks cheap and MODEL_SONNET is unset, so "
            "coding turns inherit it. Set MODEL_SONNET to a stronger coding "
            "model or raise MODEL."
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


def _chain_windows(settings: LintableModelConfig) -> list[int]:
    """Known context windows for MODEL + MODEL_FALLBACKS, overrides applied."""
    overrides = parse_context_window_overrides(settings.context_window_overrides)
    fallbacks = parse_model_fallbacks(settings.model_fallbacks)
    chain = [settings.model, *fallbacks]
    windows: list[int] = []
    for model_ref in chain:
        model_id = parse_model_name(model_ref) if "/" in model_ref else model_ref
        window = (
            overrides.get(model_ref)
            or overrides.get(model_id)
            or known_context_window(model_id)
        )
        if window is not None:
            windows.append(window)
    return windows


def _context_ceiling_warning(settings: LintableModelConfig) -> list[str]:
    if settings.model_long_context is not None:
        return []

    # An unknown-window model in the chain might already cover long prompts;
    # only warn when every known window is below the floor.
    windows = _chain_windows(settings)
    if not windows or max(windows) >= _LONG_CONTEXT_FLOOR:
        return []

    return [
        f"Every model in MODEL/MODEL_FALLBACKS has a context window <= "
        f"~{max(windows)} tokens; a long-running conversation will exhaust "
        "the whole chain at once with no fallback left. Set "
        "MODEL_LONG_CONTEXT to a large-window model (e.g. gemini, minimax, "
        "kimi) as a rescue tier."
    ]


def client_config_recommendation(settings: LintableModelConfig) -> str | None:
    """Recommended ``CLAUDE_AUTOCOMPACT_PCT_OVERRIDE``, or None if not needed.

    Claude Code's default autocompact threshold is calibrated to Claude's own
    (much larger) context window, not to a narrower configured model chain;
    left alone, a long conversation hits this chain's ceiling before the
    client ever compacts. Not needed once a rescue tier (MODEL_LONG_CONTEXT)
    or a large-enough chain ceiling removes the risk.
    """
    if settings.model_long_context is not None:
        return None
    windows = _chain_windows(settings)
    if not windows or max(windows) >= _LONG_CONTEXT_FLOOR:
        return None
    ceiling = max(windows)
    pct = max(
        _MIN_RECOMMENDED_PCT,
        int(ceiling / _CLIENT_ASSUMED_WINDOW * 100) - _RECOMMENDATION_MARGIN_PCT,
    )
    return (
        f"Configured model chain's context ceiling (~{ceiling} tokens) is "
        f"below what Claude Code assumes (~{_CLIENT_ASSUMED_WINDOW} tokens); "
        f"set CLAUDE_AUTOCOMPACT_PCT_OVERRIDE={pct} in Claude Code's "
        "settings.json env block so it compacts before reaching this "
        "chain's ceiling."
    )


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
