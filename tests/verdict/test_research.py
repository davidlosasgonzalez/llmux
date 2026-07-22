"""Unit tests for the web-research phase (verdict/research.py).

Search backend and fetcher are injected, so nothing here touches the network.
"""

import pytest

from free_claude_code.verdict.research import (
    BraveSearchBackend,
    DuckDuckGoBackend,
    ResearchResult,
    ResearchService,
    SearchHit,
    Source,
    _build_query,
    _decode_ddg_href,
    _DuckDuckGoParser,
    extract_text,
    format_sources,
    mark_unverified_citations,
    parse_brave_web_results,
    research_needed,
    resolve_search_backend,
)


# --------------------------------------------------------------------------- #
# research_needed heuristic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "prompt",
    [
        "What are the current CPU limits of Cloudflare Workers?",
        "¿Cuál es el precio actual de la API de Claude?",
        "What is the latest version of Python?",
        "Read the official documentation for the rate limit",
        "Is this API deprecated as of 2026?",
    ],
)
def test_research_needed_fires_on_currency_prompts(prompt):
    assert research_needed(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        "Explica el estándar IEEE 754",
        "Design a rate limiter algorithm",  # 'rate limiter' != 'rate limit' signal? guard below
        "Write a function that reverses a linked list",
        "Prove that the square root of 2 is irrational",
    ],
)
def test_research_needed_ignores_timeless_prompts(prompt):
    # 'Design a rate limiter' contains 'limit' via 'limiter'? No: \b...limits?\b needs a
    # word boundary — 'limiter' does not match 'limits?'. This asserts no false fire.
    assert research_needed(prompt) is False


def test_research_needed_ieee_number_does_not_trip_year_signal():
    assert research_needed("IEEE 754 floating point") is False


# --------------------------------------------------------------------------- #
# HTML → text extraction
# --------------------------------------------------------------------------- #
def test_extract_text_drops_scripts_and_styles():
    html = (
        "<html><head><style>b{color:red}</style></head>"
        "<body>Hello <script>evil()</script> world</body></html>"
    )
    assert extract_text(html) == "Hello world"


def test_extract_text_collapses_whitespace():
    html = "<p>one   two</p>\n\n<p>three</p>"
    assert extract_text(html) == "one two three"


def test_extract_text_empty_on_garbage():
    assert extract_text("") == ""


# --------------------------------------------------------------------------- #
# DuckDuckGo parsing
# --------------------------------------------------------------------------- #
def test_ddg_parser_extracts_hits_and_decodes_redirects():
    sample = (
        '<a class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fp&rut=z">'
        "Title One</a>"
        '<a class="result__snippet" href="x">snippet one</a>'
        '<a class="result__a" href="https://example.org/two">Title Two</a>'
        '<a class="result__snippet" href="y">snippet two</a>'
    )
    parser = _DuckDuckGoParser()
    parser.feed(sample)
    hits = parser.hits()
    assert [h.url for h in hits] == ["https://example.com/p", "https://example.org/two"]
    assert hits[0].title == "Title One"
    assert hits[0].snippet == "snippet one"


def test_decode_ddg_href_passes_through_plain_urls():
    assert _decode_ddg_href("https://example.com/x") == "https://example.com/x"


def test_decode_ddg_href_empty():
    assert _decode_ddg_href("") == ""


def test_build_query_collapses_and_truncates():
    query = _build_query("  a\n\n  b   c  " + "x" * 500)
    assert query.startswith("a b c")
    assert len(query) <= 200


# --------------------------------------------------------------------------- #
# Brave Web Search parsing + backend selection
# --------------------------------------------------------------------------- #
def test_parse_brave_web_results_extracts_hits():
    payload = {
        "web": {
            "results": [
                {
                    "title": "CPU limits",
                    "url": "https://developers.cloudflare.com/workers/platform/limits/",
                    "description": "default CPU time",
                },
                {"title": "Skip me", "url": "", "description": "no url"},
                {
                    "title": "Second",
                    "url": "https://example.com/two",
                    "description": "snippet two",
                },
            ]
        }
    }
    hits = parse_brave_web_results(payload, limit=2)
    assert [h.url for h in hits] == [
        "https://developers.cloudflare.com/workers/platform/limits/",
        "https://example.com/two",
    ]
    assert hits[0].title == "CPU limits"
    assert hits[0].snippet == "default CPU time"


def test_parse_brave_web_results_tolerates_bad_payload():
    assert parse_brave_web_results(None, limit=4) == []
    assert parse_brave_web_results({"web": "oops"}, limit=4) == []
    assert parse_brave_web_results({"web": {"results": [1, "x"]}}, limit=4) == []


def test_resolve_search_backend_prefers_brave_when_key_set():
    backend = resolve_search_backend(timeout_s=5.0, brave_api_key="BSATtest")
    assert isinstance(backend, BraveSearchBackend)
    assert backend.name == "brave"


def test_resolve_search_backend_falls_back_to_ddg_without_key():
    backend = resolve_search_backend(timeout_s=5.0, brave_api_key="")
    assert isinstance(backend, DuckDuckGoBackend)
    assert backend.name == "ddg"


def test_resolve_search_backend_reads_env(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "BSATfromenv")
    backend = resolve_search_backend(timeout_s=5.0)
    assert isinstance(backend, BraveSearchBackend)


# --------------------------------------------------------------------------- #
# ResearchService.investigate
# --------------------------------------------------------------------------- #
class _FakeBackend:
    name = "fake"

    def __init__(self, hits: list[SearchHit], *, raises: Exception | None = None):
        self._hits = hits
        self._raises = raises

    async def search(self, query: str, *, limit: int) -> list[SearchHit]:
        if self._raises is not None:
            raise self._raises
        return self._hits[:limit]


def _fetcher(pages: dict[str, str]):
    async def fetch(url: str) -> str:
        if url not in pages:
            raise ValueError(f"404 {url}")
        return pages[url]

    return fetch


@pytest.mark.asyncio
async def test_investigate_fetches_and_budgets_sources():
    hits = [
        SearchHit("A", "https://a.test"),
        SearchHit("B", "https://b.test"),
    ]
    pages = {
        "https://a.test": "<p>alpha content</p>",
        "https://b.test": "<p>beta content</p>",
    }
    service = ResearchService(backend=_FakeBackend(hits), fetch=_fetcher(pages))
    result = await service.investigate("latest version of X")

    assert result.backend == "fake"
    assert [s.url for s in result.sources] == ["https://a.test", "https://b.test"]
    assert result.sources[0].text == "alpha content"
    assert result.note == ""


@pytest.mark.asyncio
async def test_investigate_skips_dead_links_but_keeps_others():
    hits = [SearchHit("A", "https://a.test"), SearchHit("B", "https://dead.test")]
    pages = {"https://a.test": "<p>alpha</p>"}  # b.test 404s
    service = ResearchService(backend=_FakeBackend(hits), fetch=_fetcher(pages))
    result = await service.investigate("current pricing")

    assert [s.url for s in result.sources] == ["https://a.test"]
    assert result.note == ""


@pytest.mark.asyncio
async def test_investigate_respects_total_char_budget():
    hits = [SearchHit("A", "https://a.test"), SearchHit("B", "https://b.test")]
    pages = {
        "https://a.test": "<p>" + "x" * 100 + "</p>",
        "https://b.test": "<p>" + "y" * 100 + "</p>",
    }
    service = ResearchService(
        backend=_FakeBackend(hits),
        fetch=_fetcher(pages),
        chars_per_source=1000,
        chars_total=60,  # only the first source's 100 chars, truncated to 60
    )
    result = await service.investigate("latest limits")

    assert len(result.sources) == 1
    assert len(result.sources[0].text) == 60


@pytest.mark.asyncio
async def test_investigate_degrades_when_search_fails():
    service = ResearchService(
        backend=_FakeBackend([], raises=OSError("offline")),
        fetch=_fetcher({}),
    )
    result = await service.investigate("latest version")

    assert result.sources == []
    assert "research unavailable" in result.note
    assert result.summary().unavailable is True


@pytest.mark.asyncio
async def test_investigate_note_when_no_page_fetchable():
    hits = [SearchHit("A", "https://a.test")]
    service = ResearchService(backend=_FakeBackend(hits), fetch=_fetcher({}))
    result = await service.investigate("current limits")

    assert result.sources == []
    assert "research unavailable" in result.note


# --------------------------------------------------------------------------- #
# format_sources
# --------------------------------------------------------------------------- #
def test_format_sources_numbers_and_labels():
    result = ResearchResult(
        queries=["q"],
        sources=[
            Source("https://a.test", "A", "alpha"),
            Source("https://b.test", "B", "beta"),
        ],
        backend="fake",
    )
    block = format_sources(result, fetched_on="2026-07-15")
    assert "FUENTES VERIFICADAS (fetched 2026-07-15)" in block
    assert "[S1] https://a.test" in block
    assert "[S2] https://b.test" in block
    assert "alpha" in block and "beta" in block


def test_summary_projection_carries_urls():
    result = ResearchResult(
        sources=[Source("https://a.test", "A", "alpha")],
        backend="ddg",
        queries=["q"],
    )
    summary = result.summary()
    assert summary.backend == "ddg"
    assert summary.sources_fetched == ["https://a.test"]
    assert summary.unavailable is False


# --------------------------------------------------------------------------- #
# mark_unverified_citations (T7 — citation discipline)
# --------------------------------------------------------------------------- #
def test_mark_unverified_citations_leaves_verified_url_clean():
    text = "See https://a.test/docs for the limit."
    out = mark_unverified_citations(text, {"https://a.test/docs"})
    assert out == text


def test_mark_unverified_citations_flags_unfetched_url():
    out = mark_unverified_citations("See https://b.test/other for details.", set())
    assert (
        "https://b.test/other (URL recordada, no verificada en esta ejecución)" in out
    )


def test_mark_unverified_citations_handles_mixed_urls():
    text = "Verified: https://a.test/docs. Unverified: https://b.test/x."
    out = mark_unverified_citations(text, {"https://a.test/docs"})
    assert "https://a.test/docs. " in out  # clean, trailing period preserved outside
    assert "https://b.test/x (URL recordada, no verificada en esta ejecución)." in out


def test_mark_unverified_citations_strips_trailing_punctuation_before_marking():
    out = mark_unverified_citations("(see https://b.test/x)", set())
    # The trailing ')' from the char class exclusion is not part of the URL match
    # at all (parens are excluded), so only the marker is appended, cleanly.
    assert "https://b.test/x (URL recordada, no verificada en esta ejecución)" in out


def test_mark_unverified_citations_no_urls_is_noop():
    assert mark_unverified_citations("no links here", {"https://a.test"}) == (
        "no links here"
    )


def test_mark_unverified_citations_empty_text():
    assert mark_unverified_citations("", set()) == ""
