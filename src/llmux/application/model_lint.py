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
from llmux.core.claude_compact import (
    client_assumed_window,
    early_autocompact_pct,
    resolve_auto_compact_window,
    resolve_autocompact_pct,
    window_for_model_id,
)
from llmux.core.model_capability import (
    has_cheap_coding_hint,
    has_small_hint,
    has_weak_coding_hint,
    size_billions,
)

_SMALL_SIZE_BILLIONS = 25.0
_CLASSIFIER_HEAVY_BILLIONS = 130.0
# Below this, a chain has no room for a Claude Code conversation that has
# grown for a while; anything smaller needs a MODEL_LONG_CONTEXT rescue tier.
_LONG_CONTEXT_FLOOR = 200_000


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


def _model_id(model_ref: str) -> str:
    return parse_model_name(model_ref) if "/" in model_ref else model_ref


def _expanded_overrides(raw: str) -> dict[str, int]:
    overrides = parse_context_window_overrides(raw)
    expanded = dict(overrides)
    for key, value in overrides.items():
        if "/" in key:
            expanded.setdefault(parse_model_name(key), value)
    return expanded


def _window_for_ref(model_ref: str, overrides: dict[str, int]) -> int | None:
    model_id = _model_id(model_ref)
    return overrides.get(model_ref) or window_for_model_id(model_id, overrides)


def primary_coding_context_window(settings: LintableModelConfig) -> int | None:
    """Context window for Claude Code's coding workhorse (SONNET, else MODEL)."""

    overrides = _expanded_overrides(settings.context_window_overrides)
    ref = settings.model_sonnet if settings.model_sonnet else settings.model
    if not ref:
        return None
    return _window_for_ref(ref, overrides)


def claude_auto_compact_window(settings: LintableModelConfig) -> int:
    """Token capacity Claude Code should use for autocompact math."""

    overrides = _expanded_overrides(settings.context_window_overrides)
    ref = settings.model_sonnet if settings.model_sonnet else settings.model
    primary_id = _model_id(ref) if ref else None
    return resolve_auto_compact_window(
        primary_model_id=primary_id,
        overrides=overrides,
    )


def claude_autocompact_pct_override(settings: LintableModelConfig) -> int | None:
    """Percent of ``CLAUDE_CODE_AUTO_COMPACT_WINDOW`` at which to compact."""

    overrides = _expanded_overrides(settings.context_window_overrides)
    ref = settings.model_sonnet if settings.model_sonnet else settings.model
    primary_id = _model_id(ref) if ref else None
    chain_ids = _chain_model_ids(settings)
    return resolve_autocompact_pct(
        primary_model_id=primary_id,
        chain_model_ids=chain_ids,
        overrides=overrides,
        has_long_context=settings.model_long_context is not None,
    )


def _chain_model_ids(settings: LintableModelConfig) -> list[str]:
    refs: list[str] = [settings.model, *parse_model_fallbacks(settings.model_fallbacks)]
    refs.extend(
        tier
        for tier in (
            settings.model_fable,
            settings.model_opus,
            settings.model_sonnet,
            settings.model_haiku,
        )
        if tier
    )
    out: list[str] = []
    seen: set[str] = set()
    for model_ref in refs:
        mid = _model_id(model_ref)
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


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


def _looks_weak_for_coding(model_ref: str) -> bool:
    return has_weak_coding_hint(parse_model_name(model_ref))


def _cheap_model_in_coding_tier(settings: LintableModelConfig) -> list[str]:
    """Warn when Sonnet (or default MODEL) is flash-class or weak for agents."""

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
    elif sonnet and "/" in sonnet and _looks_weak_for_coding(sonnet):
        warnings.append(
            f"MODEL_SONNET={sonnet} looks weak for agent coding (chat/general "
            "tier, not a strong Edit/Bash model). Prefer a coding-tuned model "
            "(e.g. kimi-k2.6, deepseek-v4-pro, glm-5.x); keep lighter models "
            "on MODEL_HAIKU or fallbacks."
        )
    # If Sonnet is unset, Claude's sonnet alias falls through to MODEL.
    if (sonnet is None or sonnet == "") and _looks_cheap_for_coding(settings.model):
        warnings.append(
            f"MODEL={settings.model} looks cheap and MODEL_SONNET is unset, so "
            "coding turns inherit it. Set MODEL_SONNET to a stronger coding "
            "model or raise MODEL."
        )
    elif (sonnet is None or sonnet == "") and _looks_weak_for_coding(settings.model):
        warnings.append(
            f"MODEL={settings.model} looks weak for agent coding and "
            "MODEL_SONNET is unset, so coding turns inherit it. Set "
            "MODEL_SONNET to a stronger coding model."
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
    """Known context windows for MODEL + Claude tiers + FALLBACKS."""

    overrides = _expanded_overrides(settings.context_window_overrides)
    windows: list[int] = []
    for model_id in _chain_model_ids(settings):
        window = window_for_model_id(model_id, overrides)
        if window is not None:
            windows.append(window)
    return windows


def _context_ceiling_warning(settings: LintableModelConfig) -> list[str]:
    if settings.model_long_context is not None:
        return []

    windows = _chain_windows(settings)
    if not windows or max(windows) >= _LONG_CONTEXT_FLOOR:
        return []

    return [
        f"Every model in MODEL/MODEL_* tiers/MODEL_FALLBACKS has a context "
        f"window <= ~{max(windows)} tokens; a long-running conversation will "
        "exhaust the whole chain at once with no fallback left. Set "
        "MODEL_LONG_CONTEXT to a large-window model (e.g. gemini, minimax, "
        "kimi) as a rescue tier."
    ]


def client_config_recommendation(settings: LintableModelConfig) -> str | None:
    """Describe how ``llmux-claude`` aligns Claude Code autocompact, if relevant.

    Injection is automatic via :func:`llmux.cli.claude_env.build_claude_proxy_env`.
    This string remains for Admin UI / startup logs (VS Code/JetBrains users who
    do not use the launcher still need the values).
    """

    window = claude_auto_compact_window(settings)
    pct = claude_autocompact_pct_override(settings)
    primary = primary_coding_context_window(settings)
    if primary is None and pct is None:
        return None
    assumed = client_assumed_window()
    parts = [
        f"llmux-claude injects CLAUDE_CODE_AUTO_COMPACT_WINDOW={window}",
    ]
    if pct is not None:
        parts.append(f"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE={pct}")
    parts.append(
        "so Claude Code compacts earlier against the primary coding model "
        f"window (MODEL_SONNET or MODEL), not Claude's default ~{assumed} — "
        "narrower LLMux backends get a lower PCT so summaries stay smaller "
        "and more useful."
    )
    return " ".join(parts)


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


# Re-export for tests / callers that used the local helper name.
__all__ = [
    "LintableModelConfig",
    "claude_auto_compact_window",
    "claude_autocompact_pct_override",
    "client_config_recommendation",
    "early_autocompact_pct",
    "lint_model_config",
    "primary_coding_context_window",
]
