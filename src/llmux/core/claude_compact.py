"""Claude Code autocompact math aligned to LLMux coding-model windows.

Claude Code defaults assume ~200k (Claude Sonnet). LLMux backends are often
narrower. Symbiosis: tell the client the *real* coding-model window, then
compact *earlier* (lower PCT) on narrow backends so summaries stay smaller
and more useful instead of one late dump at the provider wall.

Dependency-free of ``config`` / ``application`` so ``cli`` can import it.
Callers pass already-parsed model ids and override maps.
"""

from llmux.core.model_capability import known_context_window

_LONG_CONTEXT_FLOOR = 200_000
_CLIENT_ASSUMED_WINDOW = 200_000
_MIN_RECOMMENDED_PCT = 30
_MAX_AUTOCOMPACT_PCT = 85
_FALLBACK_AUTO_COMPACT_WINDOW = 190_000


def window_for_model_id(model_id: str, overrides: dict[str, int]) -> int | None:
    """Resolve a context window for a bare model id (no provider prefix)."""

    return overrides.get(model_id) or known_context_window(model_id)


def early_autocompact_pct(window_tokens: int) -> int:
    """Compact earlier on narrower LLMux backends than Claude's default.

    Scales with window size relative to Claude's ~200k assumption:
    - ~131k → ~52%
    - ~262k → ~85% (capped)
    - ≥200k → 85%
    """

    return max(
        _MIN_RECOMMENDED_PCT,
        min(
            _MAX_AUTOCOMPACT_PCT,
            int(window_tokens / _CLIENT_ASSUMED_WINDOW * 80),
        ),
    )


def resolve_auto_compact_window(
    *,
    primary_model_id: str | None,
    overrides: dict[str, int],
) -> int:
    """``CLAUDE_CODE_AUTO_COMPACT_WINDOW`` from the primary coding model id."""

    if primary_model_id:
        window = window_for_model_id(primary_model_id, overrides)
        if window is not None:
            return window
    return _FALLBACK_AUTO_COMPACT_WINDOW


def resolve_autocompact_pct(
    *,
    primary_model_id: str | None,
    chain_model_ids: list[str],
    overrides: dict[str, int],
    has_long_context: bool,
) -> int | None:
    """``CLAUDE_AUTOCOMPACT_PCT_OVERRIDE``, or None when not useful."""

    if primary_model_id:
        window = window_for_model_id(primary_model_id, overrides)
        if window is not None:
            return early_autocompact_pct(window)
    if has_long_context:
        return None
    windows = [
        w
        for mid in chain_model_ids
        if (w := window_for_model_id(mid, overrides)) is not None
    ]
    if not windows or max(windows) >= _LONG_CONTEXT_FLOOR:
        return None
    return early_autocompact_pct(max(windows))


def client_assumed_window() -> int:
    """Claude Code's default assumed window (for messages / docs)."""

    return _CLIENT_ASSUMED_WINDOW
