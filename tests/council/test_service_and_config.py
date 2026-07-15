"""Service facade, the free-only env gate, and compact MCP-shaped output."""

import pytest

from free_claude_code.config.settings import Settings
from free_claude_code.council.config import CouncilConfig, load_config
from free_claude_code.council.errors import InsufficientFreeModelsError
from free_claude_code.council.service import CouncilService
from tests.council.support import FakeInvoker


def _settings_with(monkeypatch, **keys: str) -> Settings:
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MODEL", "groq/llama-3.3-70b-versatile")
    return Settings()


def _lister(catalogue: dict[str, list[str]]):
    async def lister(provider: str) -> list[str]:
        return catalogue.get(provider, [])

    return lister


def _service(settings, config, lister) -> CouncilService:
    return CouncilService(
        config=config,
        settings=settings,
        invoker=FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"]),
        lister=lister,
        store=None,
    )


@pytest.mark.asyncio
async def test_evaluate_runs_with_three_providers(monkeypatch):
    settings = _settings_with(
        monkeypatch,
        GROQ_API_KEY="k1",
        CEREBRAS_API_KEY="k2",
        GEMINI_API_KEY="k3",
    )
    config = CouncilConfig(
        enabled_providers=["groq", "cerebras", "gemini"],
    )
    lister = _lister(
        {
            "groq": ["llama-3.3-70b-versatile"],
            "cerebras": ["qwen-3-32b"],
            "gemini": ["models/gemini-flash"],
        }
    )
    service = _service(settings, config, lister)
    result, report_path = await service.evaluate("Design a rate limiter")

    compact = result.compact()
    assert compact["answer"]
    assert len(result.providers_used) >= 2
    assert report_path is None  # no store => no report file
    # The MCP-facing compact payload must carry exactly these keys.
    for key in (
        "answer",
        "recommended_action",
        "material_disagreements",
        "uncertainties",
        "confidence",
        "confidence_source",
        "models_used",
        "providers_used",
        "rounds",
        "quota_failures",
    ):
        assert key in compact


@pytest.mark.asyncio
async def test_insufficient_free_models_errors_clearly(monkeypatch):
    settings = _settings_with(monkeypatch, GROQ_API_KEY="only-one")
    config = CouncilConfig(enabled_providers=["groq"])
    lister = _lister({"groq": ["llama-3.3-70b-versatile"]})
    service = _service(settings, config, lister)

    with pytest.raises(InsufficientFreeModelsError) as excinfo:
        await service.evaluate("Question")
    # The error explains which providers could not be used.
    assert excinfo.value.reasons


@pytest.mark.asyncio
async def test_paid_provider_enabled_still_excluded(monkeypatch):
    # Even with a key and explicit enable, a card-required provider is unusable.
    settings = _settings_with(
        monkeypatch,
        GROQ_API_KEY="k1",
        CEREBRAS_API_KEY="k2",
        DEEPSEEK_API_KEY="paid-key",
    )
    config = CouncilConfig(enabled_providers=["groq", "cerebras", "deepseek"])
    lister = _lister(
        {
            "groq": ["llama-3.3-70b-versatile"],
            "cerebras": ["qwen-3-32b"],
            "deepseek": ["deepseek-chat"],
        }
    )
    service = _service(settings, config, lister)
    models = await service.list_free_models()
    providers = {m.provider for m in models}
    assert "deepseek" not in providers


def test_allow_paid_env_override(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOW_PAID_MODELS", raising=False)
    assert load_config(tmp_path / "missing.yaml").allow_paid_models is False

    monkeypatch.setenv("ALLOW_PAID_MODELS", "true")
    assert load_config(tmp_path / "missing.yaml").allow_paid_models is True

    monkeypatch.setenv("ALLOW_PAID_MODELS", "0")
    assert load_config(tmp_path / "missing.yaml").allow_paid_models is False


def test_config_yaml_round_trip(tmp_path):
    path = tmp_path / "council.yaml"
    path.write_text(
        "depth: quick\nmax_rounds: 2\nenabled_providers: [groq, gemini]\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.depth.value == "quick"
    assert config.max_rounds == 2
    assert config.enabled_providers == ["groq", "gemini"]


def test_depth_profile_caps_to_max_rounds():
    config = CouncilConfig(max_rounds=1)
    from free_claude_code.council.models import Depth

    profile = config.depth_profile(Depth.DEEP)
    assert profile.max_rounds == 1  # hard ceiling wins over the deep preset (3)
