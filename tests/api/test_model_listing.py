from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_settings
from config.settings import Settings
from providers.registry import ProviderRegistry


def _settings() -> Settings:
    return Settings.model_construct(
        model="deepseek/deepseek-chat",
        model_opus="open_router/anthropic/claude-opus",
        model_sonnet=None,
        model_haiku="deepseek/deepseek-chat",
        anthropic_auth_token="",
    )


def test_models_list_includes_configured_refs_cached_provider_models_and_aliases():
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    registry = ProviderRegistry()
    registry.cache_model_ids("deepseek", {"deepseek-chat"})
    registry.cache_model_ids("open_router", {"meta/llama-3.3", "anthropic/claude-opus"})
    app.state.provider_registry = registry
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["data"]]

    assert ids[:6] == [
        "anthropic/deepseek/deepseek-chat",
        "claude-3-freecc-no-thinking/deepseek/deepseek-chat",
        "anthropic/open_router/anthropic/claude-opus",
        "claude-3-freecc-no-thinking/open_router/anthropic/claude-opus",
        "anthropic/open_router/meta/llama-3.3",
        "claude-3-freecc-no-thinking/open_router/meta/llama-3.3",
    ]
    assert ids.count("anthropic/deepseek/deepseek-chat") == 1
    assert ids.count("claude-3-freecc-no-thinking/deepseek/deepseek-chat") == 1
    assert ids.count("anthropic/open_router/anthropic/claude-opus") == 1
    assert (
        ids.count("claude-3-freecc-no-thinking/open_router/anthropic/claude-opus") == 1
    )
    display_names = {item["id"]: item["display_name"] for item in data["data"]}
    assert (
        display_names["anthropic/open_router/meta/llama-3.3"]
        == "open_router/meta/llama-3.3"
    )
    assert (
        display_names["claude-3-freecc-no-thinking/open_router/meta/llama-3.3"]
        == "open_router/meta/llama-3.3 (no thinking)"
    )
    assert "claude-sonnet-4-20250514" in ids
    assert data["first_id"] == ids[0]
    assert data["last_id"] == ids[-1]
    assert data["has_more"] is False


def test_models_list_works_without_provider_registry():
    app = create_app(lifespan_enabled=False)
    settings = _settings()
    app.dependency_overrides[get_settings] = lambda: settings

    try:
        response = TestClient(app).get("/v1/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["data"]]
    assert ids[:4] == [
        "anthropic/deepseek/deepseek-chat",
        "claude-3-freecc-no-thinking/deepseek/deepseek-chat",
        "anthropic/open_router/anthropic/claude-opus",
        "claude-3-freecc-no-thinking/open_router/anthropic/claude-opus",
    ]
    assert "claude-sonnet-4-20250514" in ids
