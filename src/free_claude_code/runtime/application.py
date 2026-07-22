"""Single owner for application startup, shutdown, and runtime operations."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from loguru import logger

from free_claude_code.application.errors import ApplicationUnavailableError
from free_claude_code.config.admin.persistence import (
    PreparedAdminUpdate,
    commit_prepared_admin_update,
    prepare_admin_update,
)
from free_claude_code.config.admin.status import provider_config_status
from free_claude_code.config.admin.values import load_value_state
from free_claude_code.config.env_files import (
    ANTHROPIC_AUTH_TOKEN_ENV,
    process_env_key_is_effective,
)
from free_claude_code.config.model_refs import parse_provider_type
from free_claude_code.config.server_urls import local_admin_url
from free_claude_code.config.settings import Settings, get_settings

from .provider_manager import ProviderRuntimeManager

RestartCallback = Callable[[], Awaitable[None] | None]


async def best_effort(
    name: str,
    awaitable: Awaitable[Any],
    *,
    log_verbose_errors: bool = False,
) -> bool:
    """Run one cleanup step and report whether it completed.

    The lifecycle owner intentionally applies no generic timeout here. Cancelling
    an arbitrary cleanup at a deadline can abandon a half-closed SDK, thread, or
    provider resource; resource-specific cleanup or the process supervisor owns
    any force-termination deadline.
    """
    try:
        await awaitable
    except Exception as exc:
        if log_verbose_errors:
            logger.warning(
                "Shutdown step failed: {}: {}: {}",
                name,
                type(exc).__name__,
                exc,
            )
        else:
            logger.warning(
                "Shutdown step failed: {}: exc_type={}",
                name,
                type(exc).__name__,
            )
        return False
    return True


def warn_if_process_auth_token(settings: Settings) -> None:
    """Warn when server auth was implicitly inherited from the shell."""
    model_config = getattr(settings, "model_config", Settings.model_config)
    if process_env_key_is_effective(model_config, ANTHROPIC_AUTH_TOKEN_ENV):
        logger.warning(
            "ANTHROPIC_AUTH_TOKEN is set in the process environment but not in "
            "a configured .env file. The proxy will require that token. Add "
            "ANTHROPIC_AUTH_TOKEN= to .env to disable proxy auth, or set the "
            "same token in .env to make server auth explicit."
        )


def startup_failure_message(settings: Settings, exc: Exception) -> str:
    """Return the existing concise ASGI startup failure message."""
    if isinstance(exc, ApplicationUnavailableError):
        return exc.message.strip() or "Server startup failed."
    if settings.log_api_error_tracebacks:
        return f"{type(exc).__name__}: {exc}"
    return f"Server startup failed: exc_type={type(exc).__name__}"


class ApplicationRuntime:
    """Own every process-lifetime resource used by one server instance."""

    def __init__(
        self,
        provider_manager: ProviderRuntimeManager,
        *,
        restart_callback: RestartCallback | None = None,
    ) -> None:
        self.provider_manager = provider_manager
        self._restart_callback = restart_callback
        self._config_lock = asyncio.Lock()
        self._pending_fields: list[str] = []
        self._started = False
        self._closed = False
        self._provider_manager_closed = False
        self._close_lock = asyncio.Lock()

    @property
    def settings(self) -> Settings:
        return self.provider_manager.current_settings()

    @property
    def is_closed(self) -> bool:
        """Whether this runtime released its complete ownership graph."""
        return self._closed

    async def start(self) -> None:
        if self._started:
            return
        logger.info("Starting Claude Code Proxy...")
        try:
            warn_if_process_auth_token(self.settings)
            await self._validate_configured_models_best_effort()
            self.provider_manager.start_model_list_refresh()
            logging.getLogger("uvicorn.error").info(
                "Admin UI: %s (local-only)",
                local_admin_url(self.settings),
            )
            self._started = True
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as exc:
            logger.error(
                "Startup failed:\n{}",
                startup_failure_message(self.settings, exc),
            )
            await self.close()
            raise

    async def close(self) -> bool:
        async with self._close_lock:
            if self._closed:
                return True
            logger.info("Shutdown requested, cleaning up...")
            self._closed = await self._close_owned_resources()
            if self._closed:
                self._started = False
                logger.info("Server shut down cleanly")
            else:
                logger.warning(
                    "Server shutdown incomplete; owned resources remain for retry"
                )
            return self._closed

    async def apply_admin_config(
        self,
        updates: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply one validated config update without splitting runtime ownership."""
        async with self._config_lock:
            prepared = prepare_admin_update(updates)
            if not prepared.valid:
                return prepared.applied_response()
            assert prepared.settings is not None

            if prepared.pending_fields:
                result = self._commit_admin_update(prepared)
                restart = self._restart_metadata(
                    prepared.pending_fields,
                    prepared.settings,
                )
                result["restart"] = restart
                self._pending_fields = (
                    [] if restart["automatic"] else list(prepared.pending_fields)
                )
                return result

            result: dict[str, Any] = {}

            def commit() -> None:
                result.update(self._commit_admin_update(prepared))

            await self.provider_manager.replace(
                prepared.settings,
                commit=commit,
                reason="admin_apply",
            )
            self._pending_fields = []
            result["restart"] = self._restart_metadata((), prepared.settings)
            return result

    def admin_status(self) -> dict[str, Any]:
        settings = self.settings
        return {
            "status": "running",
            "host": settings.host,
            "port": settings.port,
            "model": settings.model,
            "provider": parse_provider_type(settings.model),
            "pending_fields": list(self._pending_fields),
            "provider_status": provider_config_status(load_value_state()),
            "cached_models": {
                provider_id: sorted(model_ids)
                for provider_id, model_ids in self.provider_manager.cached_model_ids().items()
            },
        }

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        lease = await self.provider_manager.acquire()
        try:
            provider = lease.resolve_provider(provider_id)
            infos = await provider.list_model_infos()
        except Exception as exc:
            return {
                "provider_id": provider_id,
                "ok": False,
                "error_type": type(exc).__name__,
            }
        finally:
            await lease.release()
        self.provider_manager.cache_model_infos(provider_id, infos)
        return {
            "provider_id": provider_id,
            "ok": True,
            "models": sorted(info.model_id for info in infos),
        }

    async def refresh_models(self) -> dict[str, Any]:
        await self.provider_manager.refresh_model_list_cache()
        return {
            "cached_models": {
                provider_id: sorted(model_ids)
                for provider_id, model_ids in self.provider_manager.cached_model_ids().items()
            }
        }

    async def request_restart(self) -> None:
        callback = self._restart_callback
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            await result

    def _commit_admin_update(
        self,
        prepared: PreparedAdminUpdate,
    ) -> dict[str, Any]:
        result = commit_prepared_admin_update(prepared)
        get_settings.cache_clear()
        return result

    def _restart_metadata(
        self,
        fields: tuple[str, ...],
        settings: Settings,
    ) -> dict[str, Any]:
        automatic = bool(fields and self._restart_callback is not None)
        return {
            "required": bool(fields),
            "automatic": automatic,
            "admin_url": local_admin_url(settings) if automatic else None,
            "fields": list(fields),
        }

    async def _validate_configured_models_best_effort(self) -> None:
        try:
            await self.provider_manager.validate_configured_models()
        except ApplicationUnavailableError as exc:
            logger.warning(
                "Configured provider model validation failed during startup; "
                "server will continue and requests will fail at provider resolution "
                "when config is incomplete. {}",
                exc.message,
            )

    async def _close_owned_resources(self) -> bool:
        if self._provider_manager_closed:
            return True
        verbose = self.settings.log_api_error_tracebacks
        self._provider_manager_closed = await best_effort(
            "provider_manager.close",
            self.provider_manager.close(),
            log_verbose_errors=verbose,
        )
        return self._provider_manager_closed
