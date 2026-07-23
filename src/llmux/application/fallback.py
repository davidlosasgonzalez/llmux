"""Pre-commit model fallback for provider streams (Phase 6 / C2).

Only switches candidates **before** the first SSE chunk is yielded. Once any
chunk has been emitted, failures propagate — mid-stream recovery stays on the
same provider.
"""

from collections.abc import AsyncIterator, Callable, Sequence
from datetime import UTC, datetime

from loguru import logger

from llmux.application.routing import ModelRouter, RoutedMessagesRequest
from llmux.config.model_refs import parse_model_name, parse_provider_type
from llmux.core.model_capability import known_context_window
from llmux.core.quota import (
    DailyExhaustionStore,
    FailureKind,
    QuotaTracker,
    classify_failure,
    retry_after_seconds,
)

OpenStream = Callable[[RoutedMessagesRequest], AsyncIterator[str]]

# Room kept for the completion when judging whether a prompt fits a model's
# context window; a prompt that fills the window exactly leaves no output space.
_OUTPUT_RESERVE_TOKENS = 8_192

_FALLBACK_KINDS = frozenset(
    {
        FailureKind.RATE_LIMITED,
        FailureKind.QUOTA_EXHAUSTED,
        FailureKind.PROVIDER_FAILURE,
        FailureKind.MODEL_UNAVAILABLE,
        FailureKind.AUTHENTICATION,
    }
)


def fallback_candidates(primary: str, fallbacks: Sequence[str]) -> list[str]:
    """Return unique ordered candidates starting with ``primary``."""

    seen: set[str] = set()
    ordered: list[str] = []
    for name in [primary, *fallbacks]:
        text = name.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def route_for_model(
    router: ModelRouter,
    template: RoutedMessagesRequest,
    model_ref: str,
) -> RoutedMessagesRequest:
    """Rebuild a routed request targeting ``model_ref`` (provider/model)."""

    resolved = router.resolve(model_ref)
    request = template.request.model_copy(deep=True)
    request.model = resolved.provider_model
    return RoutedMessagesRequest(request=request, resolved=resolved)


async def stream_with_precommit_fallback(
    *,
    template: RoutedMessagesRequest,
    candidates: Sequence[str],
    router: ModelRouter,
    open_stream: OpenStream,
    quota: QuotaTracker | None = None,
    exhaustion: DailyExhaustionStore | None = None,
    request_id: str = "",
    input_tokens: int | None = None,
) -> AsyncIterator[str]:
    """Yield from the first candidate that emits a chunk; never switch mid-stream."""

    tracker = quota or QuotaTracker()
    errors: list[str] = []
    last_error: BaseException | None = None

    for model_ref in candidates:
        provider = parse_provider_type(model_ref)
        if tracker.is_blocked(provider):
            errors.append(f"{model_ref}: blocked ({tracker.block_reason(provider)})")
            continue
        # Match on the model name alone: the provider prefix must not decide
        # the window (e.g. ``gemini/gemma-...`` is not a 1M-context model).
        model_name = parse_model_name(model_ref) if "/" in model_ref else model_ref
        window = known_context_window(model_name)
        if (
            input_tokens is not None
            and window is not None
            and input_tokens + _OUTPUT_RESERVE_TOKENS > window
        ):
            errors.append(
                f"{model_ref}: context window ~{window} too small for "
                f"{input_tokens} input tokens"
            )
            logger.warning(
                "precommit_fallback.skip_context request_id={} model={} "
                "window={} input_tokens={}",
                request_id,
                model_ref,
                window,
                input_tokens,
            )
            continue
        if exhaustion is not None:
            day = datetime.now(UTC).strftime("%Y-%m-%d")
            if model_ref in exhaustion.exhausted_keys(day):
                errors.append(f"{model_ref}: exhausted today")
                continue

        routed = route_for_model(router, template, model_ref)
        stream = open_stream(routed)
        committed = False
        try:
            async for chunk in stream:
                if not committed:
                    committed = True
                    logger.info(
                        "precommit_fallback.serving request_id={} model={} provider={}",
                        request_id,
                        routed.resolved.provider_model_ref,
                        routed.resolved.provider_id,
                    )
                    tracker.note_success(provider)
                yield chunk
            return
        except BaseException as exc:
            last_error = exc
            if committed:
                raise
            kind = classify_failure(exc)
            tracker.note_failure(provider, kind, retry_after=retry_after_seconds(exc))
            # Only true quota exhaustion is remembered for the rest of the UTC
            # day; transient rate limits already get the tracker's short
            # cool-off and must not veto a candidate until midnight.
            if kind is FailureKind.QUOTA_EXHAUSTED and exhaustion is not None:
                day = datetime.now(UTC).strftime("%Y-%m-%d")
                exhaustion.record_exhaustion(model_ref, provider, day)
            if kind not in _FALLBACK_KINDS:
                errors.append(f"{model_ref}: {exc} (non-retryable {kind.value})")
                logger.warning(
                    "precommit_fallback.stop request_id={} model={} kind={}",
                    request_id,
                    model_ref,
                    kind.value,
                )
                raise
            errors.append(f"{model_ref}: {exc}")
            logger.warning(
                "precommit_fallback.advance request_id={} failed_model={} kind={} err={}",
                request_id,
                model_ref,
                kind.value,
                exc,
            )
            continue

    detail = "; ".join(errors) if errors else "no candidates"
    if last_error is not None:
        raise RuntimeError(
            f"pre-commit fallback exhausted all candidates: {detail}"
        ) from last_error
    raise RuntimeError(f"pre-commit fallback exhausted all candidates: {detail}")
