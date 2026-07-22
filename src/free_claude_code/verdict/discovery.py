"""Phase 2 input: discover free-eligible models across enabled providers.

Discovery is dynamic where possible (it lists a provider's live catalogue) but
always passes every candidate through the free-only cost gate before returning
it. The provider-listing side effect is injected so the logic is unit-testable
without network access.
"""

import time
from collections.abc import Awaitable, Callable

from free_claude_code.config.provider_credentials import provider_has_credential
from free_claude_code.config.settings import Settings
from free_claude_code.core.model_capability import family_of, is_reasoning_model

from .models import (
    CostStatus,
    Health,
    ModelRef,
    QuotaFailure,
    QuotaStatus,
)
from .provider_policy import (
    classify_model_cost,
    is_model_eligible,
    is_provider_eligible,
    policy_for,
)

# provider_id -> async function returning that provider's raw model ids.
ModelLister = Callable[[str], Awaitable[list[str]]]

# Model-name hints that a model can drive tool calls; used only as a soft signal.
_TOOL_HINTS: tuple[str, ...] = (
    "llama",
    "qwen",
    "gpt-oss",
    "nemotron",
    "mistral",
    "devstral",
    "gemini",
    "command",
    "glm",
    "deepseek",
)


# Substrings that mark a model as NOT a general chat/completions model (audio,
# embeddings, reranking, safety guards, OCR/vision-only, image generation). Such
# models cannot participate in a text deliberation, so they are excluded even
# though they are free.
_NON_CHAT_TOKENS: tuple[str, ...] = (
    "embed",
    "bge-",
    "-bge",
    "nemoretriever",
    "rerank",
    "whisper",
    "parakeet",
    "canary",
    "orpheus",
    "tts",
    "riva",
    "-asr",
    "deplot",
    "ocdrnet",
    "paddleocr",
    "nvclip",
    "diffusion",
    "sdxl",
    "stable-diffusion",
    "flux",
    "guard",
    "shieldgemma",
    "fuyu",
    "-vl-ocr",
)


def is_chat_model(model_id: str) -> bool:
    """False for models that cannot take part in a text deliberation."""
    lowered = model_id.lower()
    return not any(token in lowered for token in _NON_CHAT_TOKENS)


def _build_ref(provider: str, model_id: str, cost: CostStatus) -> ModelRef:
    lowered = model_id.lower()
    supports_tools = any(hint in lowered for hint in _TOOL_HINTS)
    supports_reasoning = is_reasoning_model(model_id)
    return ModelRef(
        provider=provider,
        model_id=model_id,
        family=family_of(model_id),
        supports_tools=supports_tools,
        supports_json=True,
        supports_reasoning=supports_reasoning,
        cost_status=cost,
        quota_status=QuotaStatus.UNKNOWN,
        health=Health.HEALTHY,
        last_verified=time.time(),
    )


async def discover_models(
    providers: list[str],
    lister: ModelLister,
    settings: Settings,
    *,
    allow_paid: bool,
    enabled_providers: frozenset[str],
) -> tuple[list[ModelRef], list[QuotaFailure]]:
    """Return (eligible models, per-provider failures) across ``providers``.

    A provider contributes models only when it is enabled, cost-eligible and has
    a credential. Each provider that cannot contribute yields a
    :class:`QuotaFailure` explaining why, so the caller can report it.
    """
    models: list[ModelRef] = []
    failures: list[QuotaFailure] = []
    seen: set[str] = set()

    for provider in providers:
        policy = policy_for(provider)
        if not is_provider_eligible(
            provider, allow_paid=allow_paid, enabled_providers=enabled_providers
        ):
            if provider not in enabled_providers:
                reason = "not enabled in verdict config"
            else:
                reason = "requires payment/card (excluded in free-only mode)"
            failures.append(QuotaFailure(provider=provider, reason=reason))
            continue
        if not provider_has_credential(provider, settings):
            failures.append(
                QuotaFailure(
                    provider=provider,
                    reason=f"no credential configured ({policy.free_daily})",
                )
            )
            continue

        try:
            raw_ids = await lister(provider)
        except Exception as exc:
            failures.append(
                QuotaFailure(provider=provider, reason=f"model listing failed: {exc}")
            )
            continue

        added = 0
        for model_id in raw_ids:
            if not is_chat_model(model_id):
                continue
            if not is_model_eligible(
                provider,
                model_id,
                allow_paid=allow_paid,
                enabled_providers=enabled_providers,
            ):
                continue
            ref = _build_ref(
                provider, model_id, classify_model_cost(provider, model_id)
            )
            if ref.key in seen:
                continue
            seen.add(ref.key)
            models.append(ref)
            added += 1

        if added == 0 and raw_ids:
            failures.append(
                QuotaFailure(
                    provider=provider,
                    reason="no free-eligible models in catalogue",
                )
            )
        elif not raw_ids:
            failures.append(
                QuotaFailure(provider=provider, reason="empty model catalogue")
            )

    return models, failures
