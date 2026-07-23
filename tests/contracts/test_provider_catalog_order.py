"""Freeze ``PROVIDER_CATALOG`` insertion order used as canonical provider ranking."""

from llmux.config.provider_catalog import (
    PROVIDER_CATALOG,
    SUPPORTED_PROVIDER_IDS,
)

_EXPECTED_PROVIDER_ORDER: tuple[str, ...] = (
    "nvidia_nim",
    "open_router",
    "gemini",
    "deepseek",
    "mistral",
    "mistral_codestral",
    "vercel",
    "huggingface",
    "cohere",
    "github_models",
    "wafer",
    "kimi",
    "minimax",
    "cerebras",
    "groq",
    "sambanova",
    "fireworks",
    "cloudflare",
    "zai",
    "ollama_cloud",
    "lmstudio",
    "llamacpp",
    "ollama",
)


def test_provider_catalog_key_order_matches_canonical_plan() -> None:
    """NIM first; gateways precede native remotes."""

    assert tuple(PROVIDER_CATALOG.keys()) == _EXPECTED_PROVIDER_ORDER
    assert SUPPORTED_PROVIDER_IDS == _EXPECTED_PROVIDER_ORDER
