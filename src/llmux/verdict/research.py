"""Optional web-research phase (Phase 2.5) that runs *before* proposals.

None of the free models can browse, so the local MCP/CLI process does the search
and fetch deterministically, then injects verified sources into the deliberation
through the ``context`` parameter the orchestrator already accepts. This is what
lets the verdict answer version/limit/pricing questions from current sources
instead of stale training memory.

Everything here is dependency-light on purpose: search and HTML-to-text run on
``httpx`` (already a core dependency) plus the stdlib ``html.parser``. Both the
search backend and the fetcher are injectable so tests never touch the network.
"""

import asyncio
import os
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Protocol

import httpx
from loguru import logger

from .models import ResearchSummary

# A plain desktop User-Agent; the DDG HTML endpoint and most docs sites reject
# obvious bot agents. This is not evasion — it is the minimum to fetch a page.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
_BRAVE_WEB_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_BRAVE_API_KEY_ENV = "BRAVE_SEARCH_API_KEY"
# Brave caps ``count`` at 20; we never request more than the research budget.
_BRAVE_MAX_COUNT = 20

# Roughly four characters per token; used only to turn the token budgets in
# VerdictConfig into deterministic character caps for truncation.
_CHARS_PER_TOKEN = 4

# Signals that a prompt depends on current facts (versions, limits, prices, docs)
# rather than timeless reasoning. Kept deliberately narrow to avoid firing on
# every prompt — timeless questions ("explain IEEE 754") must not match.
_RESEARCH_SIGNAL = re.compile(
    r"""
    \b(
        version | versi[oó]n | latest | current | currently | vigente | newest |
        up[\s-]?to[\s-]?date | nowadays | as\ of | recent |
        pric(e|ing) | precio | cost | l[ií]mite | limits? | quota |
        rate[\s-]?limit | plan | tier | free[\s-]?tier |
        documentation | documentaci[oó]n | docs | changelog | release |
        deprecat(ed|ion) | 20(2[4-9]|3\d)
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One search-engine result before its page has been fetched."""

    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True, slots=True)
class Source:
    """A fetched, text-extracted page ready to inject into the context."""

    url: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """Outcome of one research pass over a prompt."""

    queries: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    backend: str = "none"
    note: str = ""

    def summary(self) -> ResearchSummary:
        """Project onto the SDK-free summary stored on the VerdictResult."""
        return ResearchSummary(
            backend=self.backend,
            queries=list(self.queries),
            sources_fetched=[s.url for s in self.sources],
            note=self.note,
        )


Fetcher = Callable[[str], Awaitable[str]]


class SearchBackend(Protocol):
    """A pluggable search provider.

    Production picks ``brave`` when ``BRAVE_SEARCH_API_KEY`` is set, else the
    keyless ``ddg`` HTML endpoint.
    """

    name: str

    async def search(self, query: str, *, limit: int) -> list[SearchHit]: ...


def research_needed(prompt: str) -> bool:
    """True when the prompt hinges on current facts a model may not know.

    Cheap and LLM-free: a single regex over the prompt text. False positives
    only cost one search; the real risk is a false negative silently answering
    a currency question from stale memory, so the signal list leans inclusive
    for version/limit/pricing/doc terms while still ignoring timeless prompts.
    """
    return bool(_RESEARCH_SIGNAL.search(prompt))


# --------------------------------------------------------------------------- #
# HTML parsing (stdlib only)
# --------------------------------------------------------------------------- #
class _DuckDuckGoParser(HTMLParser):
    """Extract (title, url) pairs and snippets from the DDG HTML endpoint."""

    def __init__(self) -> None:
        super().__init__()
        self._results: list[tuple[str, str]] = []
        self._snippets: list[str] = []
        self._capture: str | None = None  # "title" | "snippet" | None
        self._href = ""
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if "result__a" in classes:
            self._capture = "title"
            self._href = _decode_ddg_href(attr.get("href") or "")
            self._buffer = []
        elif "result__snippet" in classes:
            self._capture = "snippet"
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._capture is None:
            return
        text = " ".join("".join(self._buffer).split())
        if self._capture == "title" and self._href:
            self._results.append((text, self._href))
        elif self._capture == "snippet":
            self._snippets.append(text)
        self._capture = None
        self._buffer = []

    def hits(self) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for index, (title, url) in enumerate(self._results):
            snippet = self._snippets[index] if index < len(self._snippets) else ""
            hits.append(SearchHit(title=title, url=url, snippet=snippet))
        return hits


def _decode_ddg_href(href: str) -> str:
    """DDG wraps result links as ``/l/?uddg=<encoded>``; unwrap to the real URL."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        params = urllib.parse.parse_qs(parsed.query)
        target = params.get("uddg", [""])[0]
        return target or href
    return href


class _TextExtractor(HTMLParser):
    """Collapse an HTML document to readable text, dropping non-content tags."""

    _SKIP = frozenset({"script", "style", "noscript", "head", "template", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def extract_text(html: str) -> str:
    """Best-effort HTML-to-text. Returns ``""`` if parsing fails entirely."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except ValueError, AssertionError:
        return parser.text()
    return parser.text()


# --------------------------------------------------------------------------- #
# Production backend + fetcher
# --------------------------------------------------------------------------- #
class DuckDuckGoBackend:
    """Free, no-API-key search via the DuckDuckGo HTML endpoint."""

    name = "ddg"

    def __init__(self, *, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    async def search(self, query: str, *, limit: int) -> list[SearchHit]:
        async with httpx.AsyncClient(
            timeout=self._timeout_s,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(_DDG_HTML_ENDPOINT, params={"q": query})
            resp.raise_for_status()
        parser = _DuckDuckGoParser()
        parser.feed(resp.text)
        return parser.hits()[:limit]


class BraveSearchBackend:
    """Official Brave Web Search API (requires ``BRAVE_SEARCH_API_KEY``)."""

    name = "brave"

    def __init__(self, *, api_key: str, timeout_s: float) -> None:
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def search(self, query: str, *, limit: int) -> list[SearchHit]:
        count = max(1, min(limit, _BRAVE_MAX_COUNT))
        async with httpx.AsyncClient(
            timeout=self._timeout_s,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self._api_key,
            },
        ) as client:
            resp = await client.get(
                _BRAVE_WEB_ENDPOINT,
                params={"q": query, "count": count},
            )
            resp.raise_for_status()
        return parse_brave_web_results(resp.json(), limit=limit)


def parse_brave_web_results(payload: object, *, limit: int) -> list[SearchHit]:
    """Map a Brave Web Search JSON body onto ``SearchHit`` rows.

    Tolerates missing ``web`` / ``results`` so a partial payload never crashes
    the research phase — empty hits degrade to the usual unavailable note.
    """
    if not isinstance(payload, dict):
        return []
    web = payload.get("web")
    if not isinstance(web, dict):
        return []
    results = web.get("results")
    if not isinstance(results, list):
        return []

    hits: list[SearchHit] = []
    for item in results:
        if len(hits) >= limit:
            break
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        title = item.get("title")
        snippet = item.get("description")
        hits.append(
            SearchHit(
                title=title.strip() if isinstance(title, str) else "",
                url=url.strip(),
                snippet=snippet.strip() if isinstance(snippet, str) else "",
            )
        )
    return hits


def resolve_search_backend(
    *,
    timeout_s: float,
    brave_api_key: str | None = None,
) -> SearchBackend:
    """Prefer Brave when a key is available; otherwise fall back to DuckDuckGo.

    ``brave_api_key=None`` (default) reads ``BRAVE_SEARCH_API_KEY`` from the
    environment. Pass an empty string in tests to force the DDG fallback.
    """
    key = (
        brave_api_key
        if brave_api_key is not None
        else os.getenv(_BRAVE_API_KEY_ENV, "")
    ).strip()
    if key:
        return BraveSearchBackend(api_key=key, timeout_s=timeout_s)
    return DuckDuckGoBackend(timeout_s=timeout_s)


class HttpFetcher:
    """Fetch a URL and return its body, refusing non-text content types."""

    def __init__(self, *, timeout_s: float) -> None:
        self._timeout_s = timeout_s

    async def __call__(self, url: str) -> str:
        async with httpx.AsyncClient(
            timeout=self._timeout_s,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            raise ValueError(f"non-text content type: {content_type or 'unknown'}")
        return resp.text


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ResearchService:
    """Search, fetch and budget sources for one prompt. Injectable for tests."""

    backend: SearchBackend
    fetch: Fetcher
    max_sources: int = 4
    chars_per_source: int = 2000 * _CHARS_PER_TOKEN
    chars_total: int = 6000 * _CHARS_PER_TOKEN

    async def investigate(self, prompt: str) -> ResearchResult:
        """Return sources for ``prompt``; never raises on network failure."""
        query = _build_query(prompt)
        try:
            hits = await self.backend.search(query, limit=self.max_sources)
        except Exception as exc:
            logger.warning("verdict.research.search_failed query={} err={}", query, exc)
            return ResearchResult(
                queries=[query],
                backend=self.backend.name,
                note=f"research unavailable: search failed ({exc})",
            )

        htmls = await asyncio.gather(*(self._safe_fetch(h.url) for h in hits))
        sources: list[Source] = []
        budget = self.chars_total
        for hit, html in zip(hits, htmls, strict=True):
            if budget <= 0:
                break
            text = extract_text(html)
            if not text:
                continue
            take = text[: min(self.chars_per_source, budget)]
            sources.append(Source(url=hit.url, title=hit.title, text=take))
            budget -= len(take)

        note = "" if sources else "research unavailable: no sources could be fetched"
        return ResearchResult(
            queries=[query],
            sources=sources,
            backend=self.backend.name,
            note=note,
        )

    async def _safe_fetch(self, url: str) -> str:
        try:
            return await self.fetch(url)
        except Exception as exc:
            logger.info("verdict.research.fetch_failed url={} err={}", url, exc)
            return ""


def _build_query(prompt: str) -> str:
    """Reduce a prompt to a single search query (engines cap query length)."""
    collapsed = " ".join(prompt.split())
    return collapsed[:200]


def format_sources(result: ResearchResult, *, fetched_on: str) -> str:
    """Render fetched sources as an authoritative context block for the panel."""
    lines = [f"FUENTES VERIFICADAS (fetched {fetched_on}):"]
    for index, source in enumerate(result.sources, start=1):
        lines.append(f"[S{index}] {source.url}\n{source.text}")
    lines.append(
        "Instrucción: para hechos de versión, límites, precios o fechas, estas "
        "FUENTES mandan sobre tu memoria de entrenamiento. Cita [S#] al usarlas."
    )
    return "\n\n".join(lines)


# A citation should never survive on the model's word alone: models copy real,
# correct-looking URLs from training memory to back up stale facts (findings
# §B3 — a genuine Cloudflare docs URL cited for an outdated limit). Excludes the
# closing punctuation a URL is commonly followed by in prose.
_URL_RE = re.compile(r"https?://[^\s)\]}\"'<>]+")
_TRAILING_PUNCT = ".,;:!?"
_UNVERIFIED_MARKER = " (URL recordada, no verificada en esta ejecución)"


def mark_unverified_citations(text: str, verified_urls: set[str]) -> str:
    """Flag every URL not in ``verified_urls`` — never trust the prompt contract.

    Deterministic post-processing (T7): the propose/synthesis prompts ask models
    to cite only fetched sources, but a prompt is a request, not a guarantee.
    This runs on the actual output and settles it either way.
    """
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        trailing = ""
        while raw and raw[-1] in _TRAILING_PUNCT:
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        if not raw or raw in verified_urls:
            return match.group(0)
        return f"{raw}{_UNVERIFIED_MARKER}{trailing}"

    return _URL_RE.sub(_replace, text)


def build_research_service(
    *,
    max_sources: int,
    tokens_per_source: int,
    tokens_total: int,
    fetch_timeout_s: float,
    brave_api_key: str | None = None,
) -> ResearchService:
    """Construct the production research service (Brave or DDG + httpx fetcher)."""
    return ResearchService(
        backend=resolve_search_backend(
            timeout_s=fetch_timeout_s,
            brave_api_key=brave_api_key,
        ),
        fetch=HttpFetcher(timeout_s=fetch_timeout_s),
        max_sources=max_sources,
        chars_per_source=tokens_per_source * _CHARS_PER_TOKEN,
        chars_total=tokens_total * _CHARS_PER_TOKEN,
    )
