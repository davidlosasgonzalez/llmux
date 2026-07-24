"""Shared Claude Code environment policy for LLMux client surfaces."""

from collections.abc import Mapping
from typing import Protocol

from llmux.cli.proxy_auth import proxy_auth_token
from llmux.config.model_refs import (
    parse_context_window_overrides,
    parse_model_fallbacks,
    parse_model_name,
)
from llmux.core.claude_compact import (
    resolve_auto_compact_window,
    resolve_autocompact_pct,
)

CLAUDE_CODE_AUTO_COMPACT_WINDOW = "190000"
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
CLAUDE_BINARY_NAME = "claude"

# Subscription credentials that would let Claude Code bypass the proxy.
_STRIPPED_CLAUDE_ENV_KEYS = frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})


class CompactSettings(Protocol):
    model: str
    model_fable: str | None
    model_opus: str | None
    model_sonnet: str | None
    model_haiku: str | None
    model_fallbacks: str
    model_long_context: str | None
    context_window_overrides: str


def _model_id(model_ref: str) -> str:
    return parse_model_name(model_ref) if "/" in model_ref else model_ref


def _primary_coding_model_id(settings: CompactSettings) -> str | None:
    ref = settings.model_sonnet if settings.model_sonnet else settings.model
    if not ref:
        return None
    return _model_id(ref)


def _chain_model_ids(settings: CompactSettings) -> list[str]:
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
    for ref in refs:
        mid = _model_id(ref)
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def build_claude_proxy_env(
    *,
    proxy_root_url: str,
    auth_token: str,
    base_env: Mapping[str, str],
    settings: CompactSettings | None = None,
) -> dict[str, str]:
    """Return the canonical environment for Claude Code proxy sessions.

    When ``settings`` is provided, autocompact env vars are derived from the
    primary coding model window (``MODEL_SONNET`` or ``MODEL``) with an
    *earlier* PCT on narrower LLMux backends — Claude Code is built for ~200k
    Claude windows; riding to the wall then dumping once loses too much.
    """

    env = {
        key: value
        for key, value in base_env.items()
        if not key.startswith("ANTHROPIC_") and key not in _STRIPPED_CLAUDE_ENV_KEYS
    }
    env["ANTHROPIC_BASE_URL"] = proxy_root_url
    env["ANTHROPIC_AUTH_TOKEN"] = proxy_auth_token(auth_token)
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = (
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
    )

    if settings is not None:
        overrides = parse_context_window_overrides(settings.context_window_overrides)
        expanded = dict(overrides)
        for key, value in overrides.items():
            if "/" in key:
                expanded.setdefault(parse_model_name(key), value)
        primary_id = _primary_coding_model_id(settings)
        window = resolve_auto_compact_window(
            primary_model_id=primary_id,
            overrides=expanded,
        )
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(window)
        pct = resolve_autocompact_pct(
            primary_model_id=primary_id,
            chain_model_ids=_chain_model_ids(settings),
            overrides=expanded,
            has_long_context=settings.model_long_context is not None,
        )
        if pct is not None:
            env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(pct)
        else:
            env.pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None)
    else:
        env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = CLAUDE_CODE_AUTO_COMPACT_WINDOW

    return env
