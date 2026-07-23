"""Service facade, the free-only env gate, and compact MCP-shaped output."""

from dataclasses import dataclass, field

import pytest

from llmux.config.settings import Settings
from llmux.verdict.config import VerdictConfig, load_config
from llmux.verdict.errors import InsufficientFreeModelsError
from llmux.verdict.models import Privacy
from llmux.verdict.research import ResearchService, SearchHit
from llmux.verdict.service import VerdictService
from tests.verdict.support import FakeInvoker, make_model


def _settings_with(monkeypatch, **keys: str) -> Settings:
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MODEL", "groq/llama-3.3-70b-versatile")
    return Settings()


def _lister(catalogue: dict[str, list[str]]):
    async def lister(provider: str) -> list[str]:
        return catalogue.get(provider, [])

    return lister


@dataclass
class _CountingBackend:
    """Injectable search backend that records how often it was queried."""

    name: str = "fake"
    hits: list[SearchHit] = field(default_factory=list)
    raises: Exception | None = None
    calls: int = 0

    async def search(self, query: str, *, limit: int) -> list[SearchHit]:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.hits[:limit]


def _research(backend: _CountingBackend, pages: dict[str, str] | None = None):
    resolved = pages or {}

    async def fetch(url: str) -> str:
        if url not in resolved:
            raise ValueError(f"404 {url}")
        return resolved[url]

    return ResearchService(backend=backend, fetch=fetch)


def _service(
    settings, config, lister, *, research_service=None, invoker=None
) -> VerdictService:
    return VerdictService(
        config=config,
        settings=settings,
        invoker=invoker
        or FakeInvoker(critique_scores=[0.95], critique_verdicts=["pass"]),
        lister=lister,
        store=None,
        research_service=research_service,
    )


def _three_provider_service(monkeypatch, *, research_service=None, invoker=None):
    settings = _settings_with(
        monkeypatch,
        GROQ_API_KEY="k1",
        CEREBRAS_API_KEY="k2",
        GEMINI_API_KEY="k3",
    )
    config = VerdictConfig(enabled_providers=["groq", "cerebras", "gemini"])
    lister = _lister(
        {
            "groq": ["llama-3.3-70b-versatile"],
            "cerebras": ["qwen-3-32b"],
            "gemini": ["models/gemini-flash"],
        }
    )
    return _service(
        settings, config, lister, research_service=research_service, invoker=invoker
    )


@pytest.mark.asyncio
async def test_evaluate_runs_with_three_providers(monkeypatch):
    settings = _settings_with(
        monkeypatch,
        GROQ_API_KEY="k1",
        CEREBRAS_API_KEY="k2",
        GEMINI_API_KEY="k3",
    )
    config = VerdictConfig(
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
        "research",
        "elapsed_s",
    ):
        assert key in compact
    # A completed run always reports a numeric elapsed time.
    assert isinstance(compact["elapsed_s"], float)
    # A timeless prompt with no research is reported as null, not an empty object.
    assert compact["research"] is None


@pytest.mark.asyncio
async def test_full_report_carries_timing(monkeypatch):
    from llmux.verdict.service import _full_report

    settings = _settings_with(
        monkeypatch,
        GROQ_API_KEY="k1",
        CEREBRAS_API_KEY="k2",
        GEMINI_API_KEY="k3",
    )
    config = VerdictConfig(enabled_providers=["groq", "cerebras", "gemini"])
    lister = _lister(
        {
            "groq": ["llama-3.3-70b-versatile"],
            "cerebras": ["qwen-3-32b"],
            "gemini": ["models/gemini-flash"],
        }
    )
    service = _service(settings, config, lister)
    result, _report_path = await service.evaluate("Design a rate limiter")

    report = _full_report(result)
    assert report["started_at"]
    assert report["finished_at"]
    assert isinstance(report["elapsed_s"], float)
    rounds = report["rounds"]
    assert isinstance(rounds, list)
    assert rounds
    for rnd in rounds:
        assert isinstance(rnd, dict)
        assert isinstance(rnd.get("elapsed_s"), float)
        # The full report keeps the complete critique, not just the top issues.
        critique = rnd.get("critique")
        assert isinstance(critique, dict)
        assert "minor_issues" in critique
        assert "missing_evidence" in critique
    # Proposals keep their evidence and unknowns too.
    proposals = report["proposals"]
    assert isinstance(proposals, list)
    for proposal in proposals:
        assert isinstance(proposal, dict)
        assert "evidence" in proposal
        assert "unknowns" in proposal


@pytest.mark.asyncio
async def test_insufficient_free_models_errors_clearly(monkeypatch):
    settings = _settings_with(monkeypatch, GROQ_API_KEY="only-one")
    config = VerdictConfig(enabled_providers=["groq"])
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
    config = VerdictConfig(enabled_providers=["groq", "cerebras", "deepseek"])
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
    path = tmp_path / "verdict.yaml"
    path.write_text(
        "depth: quick\nmax_rounds: 2\nenabled_providers: [groq, gemini]\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.depth.value == "quick"
    assert config.max_rounds == 2
    assert config.enabled_providers == ["groq", "gemini"]


def test_depth_profile_caps_to_max_rounds():
    config = VerdictConfig(max_rounds=1)
    from llmux.verdict.models import Depth

    profile = config.depth_profile(Depth.DEEP)
    assert profile.max_rounds == 1  # hard ceiling wins over the deep preset (3)


# --------------------------------------------------------------------------- #
# Web research (Phase 2.5) wiring
# --------------------------------------------------------------------------- #
def _propose_prompts(invoker: FakeInvoker) -> list[str]:
    return [user for (_k, phase, _s, user) in invoker.calls if phase == "propose"]


@pytest.mark.asyncio
async def test_research_auto_injects_sources_into_context(monkeypatch):
    backend = _CountingBackend(hits=[SearchHit("Docs", "https://docs.test/limits")])
    research = _research(backend, {"https://docs.test/limits": "<p>default 30s</p>"})
    service = _three_provider_service(monkeypatch, research_service=research)

    result, _ = await service.evaluate("What are the current CPU limits of Workers?")

    assert backend.calls == 1
    # The verified sources reached the panel through the context seam.
    prompts = _propose_prompts(service.invoker)
    assert prompts and all("VERIFIED SOURCES" in p for p in prompts)
    assert any("https://docs.test/limits" in p for p in prompts)
    # ...and the run records what it fetched.
    assert result.research is not None
    assert result.research.sources_fetched == ["https://docs.test/limits"]
    assert result.compact()["research"]["sources_fetched"] == [
        "https://docs.test/limits"
    ]


def test_research_service_uses_brave_key_from_settings_not_process_env(
    monkeypatch,
):
    """Regression: the key lives in ~/.llmux/.env (loaded into Settings), not
    necessarily exported in the process environment. ``_research_service()``
    must read ``settings.brave_search_api_key`` explicitly rather than rely
    on ``resolve_search_backend``'s ``os.getenv`` fallback, or research
    silently degrades to the keyless DDG backend everywhere Brave was meant
    to run.
    """
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    settings = Settings()
    settings.brave_search_api_key = "test-brave-key"
    config = VerdictConfig(enabled_providers=["groq", "cerebras", "gemini"])
    service = _service(settings, config, _lister({}))

    backend = service._research_service().backend

    assert backend.name == "brave"


@pytest.mark.asyncio
async def test_research_auto_skipped_for_timeless_prompt(monkeypatch):
    backend = _CountingBackend(hits=[SearchHit("x", "https://x.test")])
    research = _research(backend, {"https://x.test": "<p>y</p>"})
    service = _three_provider_service(monkeypatch, research_service=research)

    result, _ = await service.evaluate("Explain the IEEE 754 standard")

    assert backend.calls == 0  # no HTTP: the heuristic never fired
    assert result.research is None
    assert all("VERIFIED SOURCES" not in p for p in _propose_prompts(service.invoker))


@pytest.mark.asyncio
async def test_research_off_disables_even_for_currency_prompt(monkeypatch):
    backend = _CountingBackend(hits=[SearchHit("x", "https://x.test")])
    research = _research(backend, {"https://x.test": "<p>y</p>"})
    service = _three_provider_service(monkeypatch, research_service=research)

    result, _ = await service.evaluate(
        "What is the latest version of Python?", research="off"
    )

    assert backend.calls == 0
    assert result.research is None


@pytest.mark.asyncio
async def test_research_offline_degrades_to_uncertainties(monkeypatch):
    backend = _CountingBackend(raises=OSError("offline"))
    research = _research(backend)
    service = _three_provider_service(monkeypatch, research_service=research)

    result, _ = await service.evaluate("current pricing of the API")

    assert result.research is not None
    assert result.research.unavailable is True
    # The degradation is surfaced to the consumer, never silently swallowed.
    uncertainties = result.compact()["uncertainties"]
    assert any("research unavailable" in u for u in uncertainties)


@pytest.mark.asyncio
async def test_research_skipped_under_local_only_privacy(monkeypatch):
    backend = _CountingBackend(hits=[SearchHit("x", "https://x.test")])
    research = _research(backend)
    service = _three_provider_service(monkeypatch, research_service=research)

    # Tested in isolation: local_only also filters candidates, which is a separate
    # concern from research never reaching an external search engine.
    summary, block = await service._run_research(
        "current pricing", Privacy.LOCAL_ONLY, "on"
    )

    assert backend.calls == 0
    assert block == ""
    assert summary is not None
    assert "local_only" in summary.note


# --------------------------------------------------------------------------- #
# T6 — factual disagreements escalate to evidence, never resolved by majority
# --------------------------------------------------------------------------- #
def _disagreeing_invoker() -> FakeInvoker:
    return FakeInvoker(
        critique_scores=[0.95],
        critique_verdicts=["pass"],
        synthesis_disagreements=["default is 30s vs 50ms"],
    )


@pytest.mark.asyncio
async def test_material_disagreement_escalates_with_directed_research(monkeypatch):
    backend = _CountingBackend(hits=[SearchHit("Docs", "https://docs.test/limits")])
    research = _research(backend, {"https://docs.test/limits": "<p>default 30s</p>"})
    service = _three_provider_service(
        monkeypatch, research_service=research, invoker=_disagreeing_invoker()
    )

    # Timeless-looking prompt: Phase 2.5 auto does NOT fire, so the only research
    # is the escalation triggered by the disagreement itself.
    result, _ = await service.evaluate("Design the worker runtime")

    assert backend.calls == 1  # directed research only
    assert result.stop_reason == "escalated with research"
    assert len(result.rounds) == 2  # original round + one escalated round
    assert result.research is not None
    assert result.research.sources_fetched == ["https://docs.test/limits"]
    # The escalated synthesis saw the fetched sources.
    synthesis_prompts = [
        user for (_k, phase, _s, user) in service.invoker.calls if phase == "synthesis"
    ]
    assert any("VERIFIED SOURCES" in p for p in synthesis_prompts)


@pytest.mark.asyncio
async def test_no_escalation_when_already_grounded(monkeypatch):
    backend = _CountingBackend(hits=[SearchHit("Docs", "https://docs.test/limits")])
    research = _research(backend, {"https://docs.test/limits": "<p>default 30s</p>"})
    service = _three_provider_service(
        monkeypatch, research_service=research, invoker=_disagreeing_invoker()
    )

    # Currency prompt: Phase 2.5 already grounds the run, so no second research.
    result, _ = await service.evaluate("What are the current Worker CPU limits?")

    assert backend.calls == 1  # only the initial Phase 2.5 research
    assert len(result.rounds) == 1
    assert result.stop_reason != "escalated with research"


@pytest.mark.asyncio
async def test_no_escalation_without_material_disagreement(monkeypatch):
    backend = _CountingBackend(hits=[SearchHit("Docs", "https://docs.test")])
    research = _research(backend, {"https://docs.test": "<p>x</p>"})
    service = _three_provider_service(monkeypatch, research_service=research)

    result, _ = await service.evaluate("Design the worker runtime")

    assert backend.calls == 0  # no disagreement, no currency signal → no research
    assert len(result.rounds) == 1


@pytest.mark.asyncio
async def test_escalation_skipped_when_directed_research_finds_nothing(monkeypatch):
    backend = _CountingBackend(raises=OSError("offline"))
    research = _research(backend)
    service = _three_provider_service(
        monkeypatch, research_service=research, invoker=_disagreeing_invoker()
    )

    result, _ = await service.evaluate("Design the worker runtime")

    # Directed research was attempted but found nothing, so no extra round and the
    # disagreement is honestly preserved rather than papered over.
    assert backend.calls == 1
    assert len(result.rounds) == 1
    assert result.final_synthesis.material_disagreements == ["default is 30s vs 50ms"]


# --------------------------------------------------------------------------- #
# T7 — citation discipline: only URLs research actually fetched stay clean
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_citation_discipline_marks_unverified_url_but_not_fetched_one(
    monkeypatch,
):
    backend = _CountingBackend(hits=[SearchHit("Docs", "https://docs.test/limits")])
    research = _research(backend, {"https://docs.test/limits": "<p>default 30s</p>"})
    invoker = FakeInvoker(
        critique_scores=[0.95],
        critique_verdicts=["pass"],
        synthesis_urls=["https://docs.test/limits", "https://other.test/unverified"],
    )
    service = _three_provider_service(
        monkeypatch, research_service=research, invoker=invoker
    )

    result, _ = await service.evaluate("What are the current Worker CPU limits?")

    answer = result.final_synthesis.final_answer
    assert "https://docs.test/limits" in answer
    assert "https://docs.test/limits (URL recalled" not in answer
    assert (
        "https://other.test/unverified (URL recalled from memory, not "
        "verified in this run)" in answer
    )


@pytest.mark.asyncio
async def test_citation_discipline_marks_every_url_without_research(monkeypatch):
    invoker = FakeInvoker(
        critique_scores=[0.95],
        critique_verdicts=["pass"],
        synthesis_urls=["https://x.test/a"],
    )
    service = _three_provider_service(monkeypatch, invoker=invoker)

    result, _ = await service.evaluate("Design the worker runtime")

    assert result.research is None  # timeless prompt: research never ran
    assert (
        "https://x.test/a (URL recalled from memory, not verified in this run)"
        in result.final_synthesis.final_answer
    )


# --------------------------------------------------------------------------- #
# Short-context filtering when the context (e.g. research) is large
# --------------------------------------------------------------------------- #
def _ctx_service(monkeypatch) -> VerdictService:
    settings = _settings_with(monkeypatch, GROQ_API_KEY="k1")
    return _service(settings, VerdictConfig(), _lister({}))


def test_fit_context_window_drops_short_models_when_enough_remain(monkeypatch):
    service = _ctx_service(monkeypatch)
    candidates = [
        make_model("groq", "big-1", family="a", context_length=131072),
        make_model("cerebras", "small", family="b", context_length=8192),
        make_model("gemini", "big-2", family="c", context_length=1_000_000),
        make_model("mistral", "unknown-window", family="d", context_length=None),
    ]
    big_context = "x" * 80_000  # ≈ 20k tokens — over the 8k model, under the rest

    kept = service._fit_context_window(candidates, big_context)

    kept_ids = {m.model_id for m in kept}
    assert "small" not in kept_ids  # the 8k model cannot hold it
    assert {"big-1", "big-2", "unknown-window"} <= kept_ids  # unknown window kept


def test_fit_context_window_drops_provider_request_cap_despite_big_window(
    monkeypatch,
):
    # Regression: github_models advertises a large model context_length, but
    # its free tier rejects the request itself well below that (observed:
    # HTTP 413 "Max size: 4000 tokens" for deepseek-r1-0528). A large window
    # must not mask that provider-level cap.
    service = _ctx_service(monkeypatch)
    candidates = [
        make_model("groq", "big-1", family="a", context_length=131072),
        make_model("cerebras", "big-2", family="b", context_length=131072),
        make_model("gemini", "big-3", family="c", context_length=131072),
        make_model(
            "github_models",
            "deepseek/deepseek-r1-0528",
            family="d",
            context_length=131072,
        ),
    ]
    big_context = "x" * 80_000  # ≈ 20k tokens — over github_models' 4k request cap

    kept = service._fit_context_window(candidates, big_context)

    kept_ids = {m.model_id for m in kept}
    assert "deepseek/deepseek-r1-0528" not in kept_ids
    assert {"big-1", "big-2", "big-3"} <= kept_ids


def test_fit_context_window_keeps_all_when_dropping_breaks_minimum(monkeypatch):
    service = _ctx_service(monkeypatch)
    # Only one model has a big enough window; dropping the rest would fall below
    # the free-only minimum, so all are kept rather than failing.
    candidates = [
        make_model("groq", "big", family="a", context_length=131072),
        make_model("cerebras", "small-1", family="b", context_length=8192),
        make_model("gemini", "small-2", family="c", context_length=8192),
    ]
    big_context = "x" * 80_000

    kept = service._fit_context_window(candidates, big_context)

    assert len(kept) == 3  # nothing dropped: the minimum wins over the fit


def test_fit_context_window_noop_without_context(monkeypatch):
    service = _ctx_service(monkeypatch)
    candidates = [make_model("cerebras", "small", context_length=8192)]
    assert service._fit_context_window(candidates, "") is candidates
