"""Model-list response construction for Claude-compatible clients.

Advertises ``max_input_tokens`` from the mapped LLMux backend window so Claude
Code's autocompact cap matches reality (WINDOW env is capped at the model's
reported context window — without this field it assumes Claude's ~200k).
"""

from typing import Literal

from pydantic import BaseModel, Field

from llmux.application.ports import RequestRuntimePort
from llmux.config.model_refs import (
    configured_chat_model_refs,
    parse_context_window_overrides,
    parse_model_name,
)
from llmux.config.settings import Settings
from llmux.core.gateway_model_ids import (
    gateway_model_id,
    no_thinking_gateway_model_id,
)
from llmux.core.model_capability import known_context_window

DISCOVERED_MODEL_CREATED_AT = "1970-01-01T00:00:00Z"


class ModelResponse(BaseModel):
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "llmux"
    created_at: str
    display_name: str
    id: str
    type: Literal["model"] = "model"
    # Anthropic Models API field — Claude Code uses this as the autocompact cap.
    max_input_tokens: int | None = Field(default=None)


class ModelsListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelResponse]
    first_id: str | None
    has_more: bool
    last_id: str | None


def _claude_alias_specs() -> tuple[tuple[str, str, str, str], ...]:
    """``(id, display_name, created_at, tier)`` for Claude Code tier aliases."""

    return (
        ("claude-fable-5", "Claude Fable 5", "2026-06-09T00:00:00Z", "fable"),
        ("claude-opus-4-20250514", "Claude Opus 4", "2025-05-14T00:00:00Z", "opus"),
        (
            "claude-sonnet-4-20250514",
            "Claude Sonnet 4",
            "2025-05-14T00:00:00Z",
            "sonnet",
        ),
        (
            "claude-haiku-4-20250514",
            "Claude Haiku 4",
            "2025-05-14T00:00:00Z",
            "haiku",
        ),
        ("claude-3-opus-20240229", "Claude 3 Opus", "2024-02-29T00:00:00Z", "opus"),
        (
            "claude-3-5-sonnet-20241022",
            "Claude 3.5 Sonnet",
            "2024-10-22T00:00:00Z",
            "sonnet",
        ),
        (
            "claude-3-haiku-20240307",
            "Claude 3 Haiku",
            "2024-03-07T00:00:00Z",
            "haiku",
        ),
        (
            "claude-3-5-haiku-20241022",
            "Claude 3.5 Haiku",
            "2024-10-22T00:00:00Z",
            "haiku",
        ),
    )


# Kept for importers/tests that reference the static alias ids.
SUPPORTED_CLAUDE_MODELS = [
    ModelResponse(
        id=model_id,
        display_name=display_name,
        created_at=created_at,
    )
    for model_id, display_name, created_at, _tier in _claude_alias_specs()
]


def build_models_list_response(
    settings: Settings, runtime: RequestRuntimePort
) -> ModelsListResponse:
    """Return configured, cached, and compatibility model ids."""
    models: list[ModelResponse] = []
    seen: set[str] = set()
    overrides = _expanded_overrides(settings.context_window_overrides)

    for ref in configured_chat_model_refs(settings):
        supports_thinking = runtime.cached_model_supports_thinking(
            ref.provider_id, ref.model_id
        )
        window = _window_for_ref(ref.model_ref, overrides)
        _append_provider_model_variants(
            models,
            seen,
            ref.model_ref,
            supports_thinking=supports_thinking,
            max_input_tokens=window,
        )

    for model_info in runtime.cached_prefixed_model_infos():
        window = _window_for_ref(model_info.model_id, overrides)
        _append_provider_model_variants(
            models,
            seen,
            model_info.model_id,
            supports_thinking=model_info.supports_thinking,
            max_input_tokens=window,
        )

    for model_id, display_name, created_at, tier in _claude_alias_specs():
        window = _tier_window(settings, tier, overrides)
        _append_unique_model(
            models,
            seen,
            ModelResponse(
                id=model_id,
                display_name=display_name,
                created_at=created_at,
                max_input_tokens=window,
            ),
        )

    return ModelsListResponse(
        data=models,
        first_id=models[0].id if models else None,
        has_more=False,
        last_id=models[-1].id if models else None,
    )


def _expanded_overrides(raw: str) -> dict[str, int]:
    overrides = parse_context_window_overrides(raw)
    expanded = dict(overrides)
    for key, value in overrides.items():
        if "/" in key:
            expanded.setdefault(parse_model_name(key), value)
    return expanded


def _window_for_ref(model_ref: str, overrides: dict[str, int]) -> int | None:
    model_id = parse_model_name(model_ref) if "/" in model_ref else model_ref
    return (
        overrides.get(model_ref)
        or overrides.get(model_id)
        or known_context_window(model_id)
    )


def _tier_window(
    settings: Settings, tier: str, overrides: dict[str, int]
) -> int | None:
    """Context window for a Claude Code tier alias (fable/opus/sonnet/haiku)."""

    by_tier = {
        "fable": settings.model_fable,
        "opus": settings.model_opus,
        "sonnet": settings.model_sonnet,
        "haiku": settings.model_haiku,
    }
    ref = by_tier.get(tier) or settings.model
    if not ref:
        return None
    return _window_for_ref(ref, overrides)


def _discovered_model_response(
    model_id: str, *, display_name: str, max_input_tokens: int | None
) -> ModelResponse:
    return ModelResponse(
        id=model_id,
        display_name=display_name,
        created_at=DISCOVERED_MODEL_CREATED_AT,
        max_input_tokens=max_input_tokens,
    )


def _append_unique_model(
    models: list[ModelResponse], seen: set[str], model: ModelResponse
) -> None:
    if model.id in seen:
        return
    seen.add(model.id)
    models.append(model)


def _append_provider_model_variants(
    models: list[ModelResponse],
    seen: set[str],
    provider_model_ref: str,
    *,
    supports_thinking: bool | None = None,
    max_input_tokens: int | None = None,
) -> None:
    if supports_thinking is not False:
        _append_unique_model(
            models,
            seen,
            _discovered_model_response(
                gateway_model_id(provider_model_ref),
                display_name=provider_model_ref,
                max_input_tokens=max_input_tokens,
            ),
        )
    _append_unique_model(
        models,
        seen,
        _discovered_model_response(
            no_thinking_gateway_model_id(provider_model_ref),
            display_name=f"{provider_model_ref} (no thinking)",
            max_input_tokens=max_input_tokens,
        ),
    )
