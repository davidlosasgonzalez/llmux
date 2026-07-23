"""Model routing for Claude-compatible requests."""

from dataclasses import dataclass

from loguru import logger

from llmux.application.auto_router import (
    choose_auto_model,
    extract_prompt_context,
)
from llmux.application.errors import UnknownProviderError
from llmux.application.ports import ProviderResolver
from llmux.config.model_refs import parse_model_name, parse_provider_type
from llmux.config.provider_catalog import (
    PROVIDER_CATALOG,
    SUPPORTED_PROVIDER_IDS,
)
from llmux.config.settings import Settings
from llmux.core.anthropic import MessagesRequest, TokenCountRequest
from llmux.core.gateway_model_ids import decode_gateway_model_id


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    original_model: str
    provider_id: str
    provider_model: str
    provider_model_ref: str
    thinking_enabled: bool


@dataclass(frozen=True, slots=True)
class RoutedMessagesRequest:
    request: MessagesRequest
    resolved: ResolvedModel


@dataclass(frozen=True, slots=True)
class RoutedTokenCountRequest:
    request: TokenCountRequest
    resolved: ResolvedModel


class ModelRouter:
    """Resolve incoming Claude model names to configured provider/model pairs."""

    def __init__(
        self, settings: Settings, *, provider_resolver: ProviderResolver | None = None
    ):
        self._settings = settings
        # Only used by aresolve_messages_request() when MODEL_ROUTING_MODE=auto;
        # unset in the (default) static path and in call sites that never route
        # dynamically (e.g. token counting).
        self._provider_resolver = provider_resolver

    def resolve(self, claude_model_name: str) -> ResolvedModel:
        direct = self._resolve_direct(claude_model_name)
        if direct is not None:
            return direct
        provider_model_ref = self._resolve_model_ref(claude_model_name)
        return self._finalize(claude_model_name, provider_model_ref)

    async def aresolve_messages_request(
        self, request: MessagesRequest, *, request_id: str
    ) -> RoutedMessagesRequest:
        """Like :meth:`resolve_messages_request`, but tries auto-routing first.

        A direct ``provider/model`` request always wins outright (the caller
        already named an exact target). Otherwise, when
        ``MODEL_ROUTING_MODE=auto`` and a provider resolver was supplied, a
        classifier model picks among the operator's configured chat models.
        Any auto-routing failure falls back to the exact static resolution —
        this must never be able to break message routing.
        """
        direct = self._resolve_direct(request.model)
        if (
            direct is not None
            or self._settings.model_routing_mode != "auto"
            or self._provider_resolver is None
        ):
            return self.resolve_messages_request(request)

        chosen_ref = await choose_auto_model(
            self._settings,
            self._provider_resolver,
            prompt_context=extract_prompt_context(request),
            request_id=request_id,
        )
        if chosen_ref is None:
            return self.resolve_messages_request(request)

        resolved = self._finalize(request.model, chosen_ref)
        routed = request.model_copy(deep=True)
        routed.model = resolved.provider_model
        return RoutedMessagesRequest(request=routed, resolved=resolved)

    def _resolve_direct(self, claude_model_name: str) -> ResolvedModel | None:
        (
            direct_provider_id,
            direct_provider_model,
            force_thinking_enabled,
        ) = self._direct_provider_model(claude_model_name)
        if direct_provider_id is None or direct_provider_model is None:
            return None

        thinking_enabled = (
            force_thinking_enabled
            if force_thinking_enabled is not None
            else self._resolve_thinking(direct_provider_model)
        )
        logger.debug(
            "MODEL DIRECT: '{}' -> provider='{}' model='{}' thinking={}",
            claude_model_name,
            direct_provider_id,
            direct_provider_model,
            thinking_enabled,
        )
        return ResolvedModel(
            original_model=claude_model_name,
            provider_id=direct_provider_id,
            provider_model=direct_provider_model,
            provider_model_ref=claude_model_name,
            thinking_enabled=thinking_enabled,
        )

    def _finalize(
        self, claude_model_name: str, provider_model_ref: str
    ) -> ResolvedModel:
        thinking_enabled = self._resolve_thinking(claude_model_name)
        provider_id = parse_provider_type(provider_model_ref)
        self._validate_provider_id(provider_id)
        provider_model = parse_model_name(provider_model_ref)
        if provider_model != claude_model_name:
            logger.debug(
                "MODEL MAPPING: '{}' -> '{}'", claude_model_name, provider_model
            )
        return ResolvedModel(
            original_model=claude_model_name,
            provider_id=provider_id,
            provider_model=provider_model,
            provider_model_ref=provider_model_ref,
            thinking_enabled=thinking_enabled,
        )

    @staticmethod
    def _validate_provider_id(provider_id: str) -> None:
        if provider_id not in PROVIDER_CATALOG:
            raise UnknownProviderError.for_provider(provider_id, PROVIDER_CATALOG)

    def _direct_provider_model(
        self, model_name: str
    ) -> tuple[str | None, str | None, bool | None]:
        decoded = decode_gateway_model_id(model_name)
        if decoded is not None:
            if decoded.provider_id not in SUPPORTED_PROVIDER_IDS:
                return None, None, None
            return (
                decoded.provider_id,
                decoded.provider_model,
                decoded.force_thinking_enabled,
            )

        provider_id, separator, provider_model = model_name.partition("/")
        if not separator:
            return None, None, None
        if provider_id not in SUPPORTED_PROVIDER_IDS:
            return None, None, None
        if not provider_model:
            return None, None, None
        return provider_id, provider_model, None

    def _resolve_model_ref(self, claude_model_name: str) -> str:
        """Resolve a Claude model name to the configured provider/model ref."""

        name_lower = claude_model_name.lower()
        if "fable" in name_lower and self._settings.model_fable is not None:
            return self._settings.model_fable
        if "opus" in name_lower and self._settings.model_opus is not None:
            return self._settings.model_opus
        if "haiku" in name_lower and self._settings.model_haiku is not None:
            return self._settings.model_haiku
        if "sonnet" in name_lower and self._settings.model_sonnet is not None:
            return self._settings.model_sonnet
        return self._settings.model

    def _resolve_thinking(self, claude_model_name: str) -> bool:
        """Resolve whether thinking is enabled for an incoming Claude model name."""

        name_lower = claude_model_name.lower()
        if "fable" in name_lower and self._settings.enable_fable_thinking is not None:
            return self._settings.enable_fable_thinking
        if "opus" in name_lower and self._settings.enable_opus_thinking is not None:
            return self._settings.enable_opus_thinking
        if "haiku" in name_lower and self._settings.enable_haiku_thinking is not None:
            return self._settings.enable_haiku_thinking
        if "sonnet" in name_lower and self._settings.enable_sonnet_thinking is not None:
            return self._settings.enable_sonnet_thinking
        return self._settings.enable_model_thinking

    def resolve_messages_request(
        self, request: MessagesRequest
    ) -> RoutedMessagesRequest:
        """Return an internal routed request context."""
        resolved = self.resolve(request.model)
        routed = request.model_copy(deep=True)
        routed.model = resolved.provider_model
        return RoutedMessagesRequest(request=routed, resolved=resolved)

    def resolve_token_count_request(
        self, request: TokenCountRequest
    ) -> RoutedTokenCountRequest:
        """Return an internal token-count request context."""
        resolved = self.resolve(request.model)
        routed = request.model_copy(
            update={"model": resolved.provider_model}, deep=True
        )
        return RoutedTokenCountRequest(request=routed, resolved=resolved)
