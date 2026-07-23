"""Explicit test composition for the API adapter."""

from collections.abc import MutableMapping

from fastapi import FastAPI

from llmux.api.app import create_app
from llmux.api.ports import ApiServices
from llmux.config.settings import Settings
from llmux.providers.base import BaseProvider
from llmux.providers.runtime import ProviderRuntime
from llmux.runtime.application import ApplicationRuntime, RestartCallback
from llmux.runtime.provider_manager import ProviderRuntimeManager


def create_test_app(
    settings: Settings | None = None,
    *,
    providers: MutableMapping[str, BaseProvider] | None = None,
    restart_callback: RestartCallback | None = None,
) -> FastAPI:
    """Build an API app with explicit in-memory runtime services."""
    settings = settings or Settings()
    if providers is None:
        manager = ProviderRuntimeManager(settings)
    else:
        manager = ProviderRuntimeManager(
            settings,
            runtime_factory=lambda snapshot: ProviderRuntime(
                snapshot,
                dict(providers),
            ),
        )
    runtime = ApplicationRuntime(
        manager,
        restart_callback=restart_callback,
    )
    return create_app(
        ApiServices(
            requests=manager,
            admin=runtime,
        )
    )


def runtime_for_app(app: FastAPI) -> ApplicationRuntime:
    """Return the runtime supplied by :func:`create_test_app`."""
    runtime = app.state.services.admin
    if not isinstance(runtime, ApplicationRuntime):
        raise TypeError("Test app does not use ApplicationRuntime")
    return runtime


def provider_manager_for_app(app: FastAPI) -> ProviderRuntimeManager:
    """Return the provider manager supplied by :func:`create_test_app`."""
    manager = app.state.services.requests
    if not isinstance(manager, ProviderRuntimeManager):
        raise TypeError("Test app does not use ProviderRuntimeManager")
    return manager
