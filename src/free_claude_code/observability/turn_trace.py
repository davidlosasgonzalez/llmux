"""Reconstruct a per-turn summary from the structured ``server.log``.

The proxy already emits one JSON row per event (loguru sink) tagged with a
``request_id``. Nothing extra needs to be captured in the hot path: this module
groups those rows by ``request_id`` and rolls them up into a :class:`TurnSummary`
so a slow or failed turn can be explained at a glance — most importantly how much
of the wall-clock time was spent waiting on provider rate-limit backoffs.
"""

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

_RATE_LIMIT_RE = re.compile(r"rate limit set for ([0-9.]+)s")
_STREAM_CLOSE_SUFFIXES = ("stream_completed", "stream_interrupted")


@dataclass(slots=True)
class TurnSummary:
    """Rolled-up view of every log row sharing one ``request_id``."""

    request_id: str
    start: datetime | None = None
    end: datetime | None = None
    gateway_model: str | None = None
    provider_id: str | None = None
    fallback_candidates: list[str] = field(default_factory=list)
    outcome: str | None = None
    rate_limit_wait_s: float = 0.0
    rate_limit_blocks: int = 0
    http_429: int = 0
    upstream_retries: int = 0
    stream_chunks: int = 0
    warnings: int = 0
    errors: int = 0

    @property
    def duration_s(self) -> float:
        if self.start is None or self.end is None:
            return 0.0
        return (self.end - self.start).total_seconds()

    @property
    def rate_limit_fraction(self) -> float:
        """Share of the turn spent blocked on reactive rate limits (0..1)."""
        total = self.duration_s
        if total <= 0:
            return 0.0
        return min(1.0, self.rate_limit_wait_s / total)


def _parse_time(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def iter_records(lines: Iterable[str]) -> Iterator[dict[str, object]]:
    """Yield JSON log rows that carry a ``request_id``; skip anything else."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("request_id"):
            yield record


def _apply(summary: TurnSummary, record: dict[str, object]) -> None:
    ts = _parse_time(record.get("time"))
    if ts is not None:
        if summary.start is None or ts < summary.start:
            summary.start = ts
        if summary.end is None or ts > summary.end:
            summary.end = ts

    level = record.get("level")
    if level == "WARNING":
        summary.warnings += 1
    elif level in ("ERROR", "CRITICAL"):
        summary.errors += 1

    message = str(record.get("message", ""))
    if match := _RATE_LIMIT_RE.search(message):
        summary.rate_limit_wait_s += float(match.group(1))
        summary.rate_limit_blocks += 1
    if "429" in message or "Too Many Requests" in message:
        summary.http_429 += 1
    if "retry" in message.lower():
        summary.upstream_retries += 1

    if model := record.get("gateway_model"):
        summary.gateway_model = str(model)
    if provider := record.get("provider_id"):
        summary.provider_id = str(provider)

    event = str(record.get("event", ""))
    if event.endswith(_STREAM_CLOSE_SUFFIXES):
        if outcome := record.get("outcome"):
            summary.outcome = str(outcome)
        chunks = record.get("stream_chunks")
        if isinstance(chunks, int):
            summary.stream_chunks = chunks
        candidates = record.get("fallback_candidates")
        if isinstance(candidates, list):
            summary.fallback_candidates = [str(item) for item in candidates]


def summarize_turns(lines: Iterable[str]) -> list[TurnSummary]:
    """Group log rows by ``request_id`` into per-turn summaries (oldest first)."""
    turns: dict[str, TurnSummary] = {}
    order: list[str] = []
    for record in iter_records(lines):
        request_id = str(record["request_id"])
        summary = turns.get(request_id)
        if summary is None:
            summary = TurnSummary(request_id=request_id)
            turns[request_id] = summary
            order.append(request_id)
        _apply(summary, record)
    return [turns[request_id] for request_id in order]


def format_summary(summary: TurnSummary) -> str:
    """Render one turn as a compact, human-readable block."""
    model = "/".join(
        part for part in (summary.provider_id, summary.gateway_model) if part
    )
    lines = [
        f"turn {summary.request_id}",
        f"  duration      {summary.duration_s:7.1f}s",
        f"  rate-limit    {summary.rate_limit_wait_s:7.1f}s"
        f"  in {summary.rate_limit_blocks} blocks"
        f"  ({summary.rate_limit_fraction * 100:.0f}% of turn)",
        f"  model         {model or '(unknown)'}",
        f"  outcome       {summary.outcome or '(none)'}"
        f"  chunks={summary.stream_chunks}",
        f"  upstream      429s={summary.http_429}"
        f"  retries={summary.upstream_retries}"
        f"  warnings={summary.warnings}  errors={summary.errors}",
    ]
    if summary.fallback_candidates:
        lines.append(f"  fallbacks     {' -> '.join(summary.fallback_candidates)}")
    return "\n".join(lines)
