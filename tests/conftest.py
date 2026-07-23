import contextlib
import logging
import os

import pytest

from llmux.config.settings import Settings
from tests.providers.support import passthrough_rate_limiter

# Set mock environment BEFORE any imports that use Settings
os.environ.setdefault("NVIDIA_NIM_API_KEY", "test_key")
os.environ.setdefault("MODEL", "nvidia_nim/test-model")
# Ensure tests don't pick up a server API key from the repo .env
# (tests expect endpoints to be unauthenticated by default)
os.environ["ANTHROPIC_AUTH_TOKEN"] = ""

Settings.model_config = {**Settings.model_config, "env_file": None}


@pytest.fixture(autouse=True)
def _isolate_from_dotenv(monkeypatch):
    """Prevent Pydantic BaseSettings from reading the .env file during tests."""
    monkeypatch.setattr(
        Settings, "model_config", {**Settings.model_config, "env_file": None}
    )


@pytest.fixture
def provider_config():
    from llmux.providers.base import ProviderConfig

    return ProviderConfig(
        api_key="test_key",
        base_url="https://test.api.nvidia.com/v1",
        rate_limit=10,
        rate_window=60,
    )


@pytest.fixture
def nim_provider(provider_config):
    from llmux.config.nim import NimSettings
    from llmux.providers.nvidia_nim import NvidiaNimProvider

    return NvidiaNimProvider(
        provider_config,
        nim_settings=NimSettings(),
        rate_limiter=passthrough_rate_limiter(),
    )


@pytest.fixture
def open_router_provider(provider_config):
    from llmux.providers.open_router import OpenRouterProvider

    return OpenRouterProvider(provider_config, rate_limiter=passthrough_rate_limiter())


@pytest.fixture
def lmstudio_provider(provider_config):
    from llmux.providers.base import ProviderConfig
    from llmux.providers.lmstudio import LMStudioProvider

    lmstudio_config = ProviderConfig(
        api_key="lm-studio",
        base_url="http://localhost:1234/v1",
        rate_limit=provider_config.rate_limit,
        rate_window=provider_config.rate_window,
    )
    return LMStudioProvider(lmstudio_config, rate_limiter=passthrough_rate_limiter())


@pytest.fixture
def llamacpp_provider(provider_config):
    from llmux.providers.base import ProviderConfig
    from llmux.providers.openai_chat import create_openai_chat_provider

    llamacpp_config = ProviderConfig(
        api_key="llamacpp",
        base_url="http://localhost:8080/v1",
        rate_limit=10,
        rate_window=60,
    )
    return create_openai_chat_provider(
        "llamacpp",
        llamacpp_config,
        passthrough_rate_limiter(),
    )


@pytest.fixture(autouse=True)
def _propagate_loguru_to_caplog():
    """Route loguru logs to stdlib logging so pytest caplog captures them."""
    from loguru import logger as loguru_logger

    class _PropagateHandler:
        def write(self, message):
            record = message.record
            level = record["level"].no
            stdlib_level = min(level, logging.CRITICAL)
            py_logger = logging.getLogger(record["name"])
            py_logger.log(stdlib_level, record["message"])

    handler_id = loguru_logger.add(_PropagateHandler(), format="{message}")
    yield
    with contextlib.suppress(ValueError):
        loguru_logger.remove(
            handler_id
        )  # Handler already removed (e.g. by test_logging_config)
