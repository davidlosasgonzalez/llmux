"""Single production composition root for the LLMux server."""

import os
from pathlib import Path

from llmux.api.app import create_app
from llmux.api.ports import ApiServices
from llmux.config.logging_config import configure_logging
from llmux.config.paths import server_log_path
from llmux.config.settings import Settings

from .application import ApplicationRuntime, RestartCallback
from .asgi import RuntimeASGIApp
from .provider_manager import ProviderRuntimeManager


def build_asgi_app(
    settings: Settings,
    restart_callback: RestartCallback | None = None,
) -> RuntimeASGIApp:
    """Construct the complete server application and its resource owner."""
    log_path = Path(os.getenv("LOG_FILE", server_log_path()))
    configure_logging(
        log_path,
        verbose_third_party=settings.log_raw_api_payloads,
        truncate=False,
    )
    provider_manager = ProviderRuntimeManager(settings)
    runtime = ApplicationRuntime(
        provider_manager,
        restart_callback=restart_callback,
    )
    services = ApiServices(
        requests=provider_manager,
        admin=runtime,
    )
    return RuntimeASGIApp(create_app(services), runtime)
