"""Local persistence for empirical model selection and full-report archival.

Per-model, per-category statistics live in a small SQLite database
(``~/.llmux/verdict.db``); full deliberation reports are written to JSON files so
they never bloat the caller's context. Speed is tracked but deliberately given
low weight elsewhere — quality, reliability and structured-output compliance
matter more.
"""

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from llmux.core.quota.exhaustion import (
    exhausted_keys as _exhausted_keys,
)
from llmux.core.quota.exhaustion import (
    record_exhaustion as _record_exhaustion,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_stats (
    model_key TEXT NOT NULL,
    category TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    rate_limited INTEGER NOT NULL DEFAULT 0,
    json_ok INTEGER NOT NULL DEFAULT 0,
    latency_sum REAL NOT NULL DEFAULT 0.0,
    cross_review_score_sum REAL NOT NULL DEFAULT 0.0,
    cross_review_count INTEGER NOT NULL DEFAULT 0,
    selected_best INTEGER NOT NULL DEFAULT 0,
    synthesis_rejected INTEGER NOT NULL DEFAULT 0,
    last_used REAL,
    PRIMARY KEY (model_key, category)
);
CREATE TABLE IF NOT EXISTS usage_log (
    provider TEXT NOT NULL,
    model_key TEXT NOT NULL,
    day TEXT NOT NULL,
    requests INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, model_key, day)
);
CREATE TABLE IF NOT EXISTS quota_exhaustion (
    model_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    day TEXT NOT NULL,
    PRIMARY KEY (model_key, day)
);
"""


@dataclass(frozen=True, slots=True)
class ModelStats:
    """Aggregated performance for one model in one category."""

    model_key: str
    category: str
    requests: int = 0
    valid: int = 0
    failures: int = 0
    rate_limited: int = 0
    json_ok: int = 0
    latency_sum: float = 0.0
    cross_review_score_sum: float = 0.0
    cross_review_count: int = 0
    selected_best: int = 0
    synthesis_rejected: int = 0
    last_used: float | None = None

    @property
    def reliability(self) -> float:
        if self.requests == 0:
            return 0.5
        return self.valid / self.requests

    @property
    def json_compliance(self) -> float:
        if self.requests == 0:
            return 0.5
        return self.json_ok / self.requests

    @property
    def avg_cross_review(self) -> float:
        if self.cross_review_count == 0:
            return 0.5
        return self.cross_review_score_sum / self.cross_review_count

    @property
    def avg_latency(self) -> float:
        if self.valid == 0:
            return 0.0
        return self.latency_sum / self.valid


@dataclass(frozen=True, slots=True)
class UsageRow:
    """Requests and tokens spent by one model on one day (usage / quota view)."""

    provider: str
    model_key: str
    day: str
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class VerdictStore:
    """Thin SQLite wrapper for model statistics."""

    def __init__(self, db_path: Path):
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> VerdictStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _ensure_row(self, model_key: str, category: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO model_stats (model_key, category) VALUES (?, ?)",
            (model_key, category),
        )

    def record_invocation(
        self,
        model_key: str,
        category: str,
        *,
        ok: bool,
        json_ok: bool,
        rate_limited: bool,
        latency_s: float,
    ) -> None:
        self._ensure_row(model_key, category)
        self._conn.execute(
            """
            UPDATE model_stats SET
                requests = requests + 1,
                valid = valid + ?,
                failures = failures + ?,
                rate_limited = rate_limited + ?,
                json_ok = json_ok + ?,
                latency_sum = latency_sum + ?,
                last_used = ?
            WHERE model_key = ? AND category = ?
            """,
            (
                1 if ok else 0,
                0 if ok else 1,
                1 if rate_limited else 0,
                1 if json_ok else 0,
                latency_s if ok else 0.0,
                time.time(),
                model_key,
                category,
            ),
        )
        self._conn.commit()

    def record_cross_review_score(
        self, model_key: str, category: str, score: float
    ) -> None:
        self._ensure_row(model_key, category)
        self._conn.execute(
            """
            UPDATE model_stats SET
                cross_review_score_sum = cross_review_score_sum + ?,
                cross_review_count = cross_review_count + 1
            WHERE model_key = ? AND category = ?
            """,
            (score, model_key, category),
        )
        self._conn.commit()

    def record_selected_best(self, model_key: str, category: str) -> None:
        self._ensure_row(model_key, category)
        self._conn.execute(
            "UPDATE model_stats SET selected_best = selected_best + 1 "
            "WHERE model_key = ? AND category = ?",
            (model_key, category),
        )
        self._conn.commit()

    def record_synthesis_rejected(self, model_key: str, category: str) -> None:
        self._ensure_row(model_key, category)
        self._conn.execute(
            "UPDATE model_stats SET synthesis_rejected = synthesis_rejected + 1 "
            "WHERE model_key = ? AND category = ?",
            (model_key, category),
        )
        self._conn.commit()

    def record_usage(
        self,
        provider: str,
        model_key: str,
        day: str,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Accumulate one request's token spend into the daily usage log."""
        self._conn.execute(
            """
            INSERT INTO usage_log
                (provider, model_key, day, requests, input_tokens, output_tokens)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(provider, model_key, day) DO UPDATE SET
                requests = requests + 1,
                input_tokens = input_tokens + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens
            """,
            (provider, model_key, day, input_tokens, output_tokens),
        )
        self._conn.commit()

    def record_exhaustion(self, model_key: str, provider: str, day: str) -> None:
        """Note that ``model_key`` exhausted its free quota on ``day``.

        Lets a later run on the same day skip a model already known to be out of
        quota, instead of spending a call to rediscover the 429.
        """
        _record_exhaustion(self._conn, model_key, provider, day)

    def exhausted_keys(self, day: str) -> set[str]:
        """Model keys known to have exhausted their free quota on ``day``."""
        return _exhausted_keys(self._conn, day)

    def usage_rows(self, day: str | None = None) -> list[UsageRow]:
        """Return usage rows, optionally filtered to a single ``day``."""
        if day is None:
            rows = self._conn.execute(
                "SELECT * FROM usage_log ORDER BY day DESC, provider, model_key"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM usage_log WHERE day = ? ORDER BY provider, model_key",
                (day,),
            ).fetchall()
        return [
            UsageRow(
                provider=row["provider"],
                model_key=row["model_key"],
                day=row["day"],
                requests=row["requests"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
            )
            for row in rows
        ]

    def stats_for(self, model_key: str, category: str) -> ModelStats:
        row = self._conn.execute(
            "SELECT * FROM model_stats WHERE model_key = ? AND category = ?",
            (model_key, category),
        ).fetchone()
        if row is None:
            return ModelStats(model_key=model_key, category=category)
        return _row_to_stats(row)


def _row_to_stats(row: sqlite3.Row) -> ModelStats:
    return ModelStats(
        model_key=row["model_key"],
        category=row["category"],
        requests=row["requests"],
        valid=row["valid"],
        failures=row["failures"],
        rate_limited=row["rate_limited"],
        json_ok=row["json_ok"],
        latency_sum=row["latency_sum"],
        cross_review_score_sum=row["cross_review_score_sum"],
        cross_review_count=row["cross_review_count"],
        selected_best=row["selected_best"],
        synthesis_rejected=row["synthesis_rejected"],
        last_used=row["last_used"],
    )


def save_report(reports_dir: Path, report: dict[str, object], *, name: str) -> Path:
    """Write a full report as JSON and return its path."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{name}.json"
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return path
