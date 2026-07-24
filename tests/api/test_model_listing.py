from fastapi.testclient import TestClient

from llmux.application.model_metadata import ProviderModelInfo
from llmux.config.settings import Settings
from tests.api.support import create_test_app, provider_manager_for_app


def _settings(
    *,
    model: str = "deepseek/deepseek-chat",
    model_fable: str | None = None,
    model_opus: str | None = "open_router/anthropic/claude-opus",
    model_sonnet: str | None = None,
    model_haiku: str | None = "deepseek/deepseek-chat",
    context_window_overrides: str = "",
) -> Settings:
    return Settings.model_construct(
        model=model,
        model_fable=model_fable,
        model_opus=model_opus,
        model_sonnet=model_sonnet,
        model_haiku=model_haiku,
        context_window_overrides=context_window_overrides,
        anthropic_auth_token="",
    )


def _cache_models(app, provider_id: str, *model_ids: str) -> None:
    provider_manager_for_app(app).cache_model_infos(
        provider_id,
        {ProviderModelInfo(model_id) for model_id in model_ids},
    )


def test_models_list_includes_configured_refs_cached_provider_models_and_aliases():
    app = create_test_app(_settings())
    _cache_models(app, "deepseek", "deepseek-chat")
    _cache_models(
        app,
        "open_router",
        "meta/llama-3.3",
        "anthropic/claude-opus",
    )

    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["data"]]
    assert ids[:6] == [
        "anthropic/deepseek/deepseek-chat",
        "claude-3-llmux-no-thinking/deepseek/deepseek-chat",
        "anthropic/open_router/anthropic/claude-opus",
        "claude-3-llmux-no-thinking/open_router/anthropic/claude-opus",
        "anthropic/open_router/meta/llama-3.3",
        "claude-3-llmux-no-thinking/open_router/meta/llama-3.3",
    ]
    assert ids.count("anthropic/deepseek/deepseek-chat") == 1
    assert ids.count("anthropic/open_router/anthropic/claude-opus") == 1
    display_names = {item["id"]: item["display_name"] for item in data["data"]}
    assert (
        display_names["anthropic/open_router/meta/llama-3.3"]
        == "open_router/meta/llama-3.3"
    )
    assert (
        display_names["claude-3-llmux-no-thinking/open_router/meta/llama-3.3"]
        == "open_router/meta/llama-3.3 (no thinking)"
    )
    assert "claude-sonnet-4-20250514" in ids
    assert "claude-fable-5" in ids
    assert data["first_id"] == ids[0]
    assert data["last_id"] == ids[-1]
    assert data["has_more"] is False


def test_models_list_uses_thinking_metadata_for_cached_models():
    app = create_test_app(_settings(model_opus=None))
    manager = provider_manager_for_app(app)
    _cache_models(app, "deepseek", "deepseek-chat")
    manager.cache_model_infos(
        "open_router",
        {
            ProviderModelInfo("reasoning-model", supports_thinking=True),
            ProviderModelInfo("plain-model", supports_thinking=False),
        },
    )

    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/open_router/reasoning-model" in ids
    assert "claude-3-llmux-no-thinking/open_router/reasoning-model" in ids
    assert "anthropic/open_router/plain-model" not in ids
    assert "claude-3-llmux-no-thinking/open_router/plain-model" in ids


def test_models_list_uses_cached_metadata_for_configured_refs():
    app = create_test_app(
        _settings(
            model="open_router/plain-model",
            model_opus=None,
            model_haiku=None,
        )
    )
    provider_manager_for_app(app).cache_model_infos(
        "open_router",
        {ProviderModelInfo("plain-model", supports_thinking=False)},
    )

    response = TestClient(app).get("/v1/models")

    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/open_router/plain-model" not in ids
    assert ids[0] == "claude-3-llmux-no-thinking/open_router/plain-model"


def test_models_list_includes_cached_wafer_models():
    app = create_test_app(
        _settings(
            model="wafer/DeepSeek-V4-Pro",
            model_opus=None,
            model_haiku=None,
        )
    )
    _cache_models(app, "wafer", "DeepSeek-V4-Pro", "MiniMax-M2.7")

    response = TestClient(app).get("/v1/models")

    ids = [item["id"] for item in response.json()["data"]]
    assert "anthropic/wafer/DeepSeek-V4-Pro" in ids
    assert "claude-3-llmux-no-thinking/wafer/DeepSeek-V4-Pro" in ids
    assert "anthropic/wafer/MiniMax-M2.7" in ids
    assert "claude-3-llmux-no-thinking/wafer/MiniMax-M2.7" in ids


def test_models_list_works_with_empty_discovery_catalog():
    app = create_test_app(_settings())

    response = TestClient(app).get("/v1/models")

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert ids[:4] == [
        "anthropic/deepseek/deepseek-chat",
        "claude-3-llmux-no-thinking/deepseek/deepseek-chat",
        "anthropic/open_router/anthropic/claude-opus",
        "claude-3-llmux-no-thinking/open_router/anthropic/claude-opus",
    ]
    assert "claude-sonnet-4-20250514" in ids


def test_models_list_advertises_backend_max_input_tokens_on_aliases():
    app = create_test_app(
        _settings(
            model="nvidia_nim/nvidia/nemotron-3-super-120b-a12b",
            model_sonnet="open_router/moonshotai/kimi-k2.6",
            model_opus=None,
            model_haiku="open_router/deepseek/deepseek-v4-flash",
        )
    )

    response = TestClient(app).get("/v1/models")
    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()["data"]}

    # Sonnet alias follows MODEL_SONNET (kimi ~262k), not Claude's default 200k.
    assert by_id["claude-sonnet-4-20250514"]["max_input_tokens"] == 262_144
    # Haiku alias follows MODEL_HAIKU (deepseek ~131k).
    assert by_id["claude-haiku-4-20250514"]["max_input_tokens"] == 131_072
    # Configured kimi ref also advertises its window.
    kimi_id = "anthropic/open_router/moonshotai/kimi-k2.6"
    assert by_id[kimi_id]["max_input_tokens"] == 262_144
