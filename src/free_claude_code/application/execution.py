"""Provider execution shared by inbound API adapters."""

import sys
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Literal

from loguru import logger

from free_claude_code.application.fallback import (
    fallback_candidates,
    stream_with_precommit_fallback,
)
from free_claude_code.application.routing import ModelRouter, RoutedMessagesRequest
from free_claude_code.config.model_refs import parse_model_fallbacks
from free_claude_code.core.anthropic import (
    Message,
    SystemContent,
    Tool,
    anthropic_request_snapshot,
    get_token_count,
)
from free_claude_code.core.quota import DailyExhaustionStore, QuotaTracker
from free_claude_code.core.trace import (
    close_stream_input,
    trace_event,
    traced_async_stream,
)

from .ports import ProviderResolver

TokenCounter = Callable[
    [list[Message], str | list[SystemContent] | None, list[Tool] | None],
    int,
]
WireApi = Literal["messages", "responses"]


class ProviderExecutor:
    """Resolve a provider and execute one routed Anthropic Messages stream."""

    def __init__(
        self,
        provider_resolver: ProviderResolver,
        *,
        token_counter: TokenCounter = get_token_count,
        generation_id: int | None = None,
        log_raw_payloads: bool = False,
        model_router: ModelRouter | None = None,
        model_fallbacks: Sequence[str] | str = (),
        quota: QuotaTracker | None = None,
        exhaustion: DailyExhaustionStore | None = None,
    ) -> None:
        self._provider_resolver = provider_resolver
        self._token_counter = token_counter
        self._generation_id = generation_id
        self._log_raw_payloads = log_raw_payloads
        self._model_router = model_router
        if isinstance(model_fallbacks, str):
            self._model_fallbacks = parse_model_fallbacks(model_fallbacks)
        else:
            self._model_fallbacks = [
                item.strip() for item in model_fallbacks if item.strip()
            ]
        self._quota = quota
        self._exhaustion = exhaustion

    def stream(
        self,
        routed: RoutedMessagesRequest,
        *,
        wire_api: WireApi,
        raw_log_label: str,
        raw_log_payload: object,
        request_id: str,
    ) -> AsyncIterator[str]:
        """Preflight synchronously, then return the traced provider stream."""

        self._trace_route(routed, wire_api=wire_api, request_id=request_id)
        self._trace_ingress(routed, wire_api=wire_api, request_id=request_id)

        if self._log_raw_payloads:
            logger.debug(f"{raw_log_label} [{{}}]: {{}}", request_id, raw_log_payload)

        candidates = fallback_candidates(
            routed.resolved.provider_model_ref,
            self._model_fallbacks,
        )
        use_fallback = self._model_router is not None and len(candidates) > 1

        if use_fallback:
            assert self._model_router is not None
            input_tokens = self._token_counter(
                routed.request.messages,
                routed.request.system,
                routed.request.tools,
            )

            def open_stream(candidate: RoutedMessagesRequest) -> AsyncIterator[str]:
                return self._open_provider_stream(
                    candidate,
                    input_tokens=input_tokens,
                    request_id=request_id,
                    preflight=True,
                )

            body: AsyncIterator[str] = stream_with_precommit_fallback(
                template=routed,
                candidates=candidates,
                router=self._model_router,
                open_stream=open_stream,
                quota=self._quota,
                exhaustion=self._exhaustion,
                request_id=request_id,
                input_tokens=input_tokens,
            )
        else:
            # Single-candidate path: keep historical preflight-before-tokens behaviour.
            provider = self._provider_resolver(routed.resolved.provider_id)
            provider.preflight_stream(
                routed.request,
                thinking_enabled=routed.resolved.thinking_enabled,
            )
            input_tokens = self._token_counter(
                routed.request.messages,
                routed.request.system,
                routed.request.tools,
            )
            body = self._open_provider_stream(
                routed,
                input_tokens=input_tokens,
                request_id=request_id,
                preflight=False,
            )

        stream_trace: dict[str, object] = {
            "request_id": request_id,
            "provider_id": routed.resolved.provider_id,
            "gateway_model": routed.request.model,
        }
        if self._generation_id is not None:
            stream_trace["generation_id"] = self._generation_id
        if use_fallback:
            stream_trace["fallback_candidates"] = list(candidates)

        return traced_async_stream(
            body,
            stage="egress",
            source="api",
            complete_event=(
                "free_claude_code.api.responses.stream_completed"
                if wire_api == "responses"
                else "free_claude_code.api.response.stream_completed"
            ),
            interrupted_event=(
                "free_claude_code.api.responses.stream_interrupted"
                if wire_api == "responses"
                else "free_claude_code.api.response.stream_interrupted"
            ),
            chunk_event=None,
            extra=stream_trace,
        )

    def _open_provider_stream(
        self,
        routed: RoutedMessagesRequest,
        *,
        input_tokens: int,
        request_id: str,
        preflight: bool = True,
    ) -> AsyncIterator[str]:
        provider = self._provider_resolver(routed.resolved.provider_id)
        if preflight:
            provider.preflight_stream(
                routed.request,
                thinking_enabled=routed.resolved.thinking_enabled,
            )

        async def provider_body() -> AsyncIterator[str]:
            provider_stream: AsyncIterator[str] | None = None
            try:
                provider_stream = provider.stream_response(
                    routed.request,
                    input_tokens=input_tokens,
                    request_id=request_id,
                    thinking_enabled=routed.resolved.thinking_enabled,
                )
                async for chunk in provider_stream:
                    yield chunk
            finally:
                if provider_stream is not None:
                    await close_stream_input(
                        provider_stream,
                        owner="provider_executor",
                        source="api",
                        preserved_error=sys.exception(),
                    )

        return provider_body()

    def _trace_route(
        self,
        routed: RoutedMessagesRequest,
        *,
        wire_api: WireApi,
        request_id: str,
    ) -> None:
        route_trace: dict[str, object] = {
            "stage": "routing",
            "event": "free_claude_code.api.route.resolved",
            "source": "api",
            "request_id": request_id,
            "provider_id": routed.resolved.provider_id,
            "provider_model": routed.resolved.provider_model,
            "provider_model_ref": routed.resolved.provider_model_ref,
            "gateway_model": routed.request.model,
            "thinking_enabled": routed.resolved.thinking_enabled,
        }
        if wire_api == "responses":
            route_trace["wire_api"] = "responses"
        if self._generation_id is not None:
            route_trace["generation_id"] = self._generation_id
        trace_event(**route_trace)

    def _trace_ingress(
        self,
        routed: RoutedMessagesRequest,
        *,
        wire_api: WireApi,
        request_id: str,
    ) -> None:
        trace_event(
            stage="ingress",
            event=(
                "free_claude_code.api.responses.request.received"
                if wire_api == "responses"
                else "free_claude_code.api.request.received"
            ),
            source="api",
            message_count=len(routed.request.messages),
            snapshot=anthropic_request_snapshot(routed.request),
            request_id=request_id,
        )
