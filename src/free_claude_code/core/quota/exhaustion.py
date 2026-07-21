"""Persistent daily quota-exhaustion memory (SQLite)."""

import sqlite3
from pathlib import Path

EXHAUSTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_exhaustion (
    model_key TEXT NOT NULL,
    provider TEXT NOT NULL,
    day TEXT NOT NULL,
    PRIMARY KEY (model_key, day)
);
"""


def ensure_exhaustion_schema(conn: sqlite3.Connection) -> None:
    conn.execute(EXHAUSTION_SCHEMA)
    conn.commit()


def record_exhaustion(
    conn: sqlite3.Connection, model_key: str, provider: str, day: str
) -> None:
    """Note that ``model_key`` exhausted free quota on ``day``."""
    conn.execute(
        "INSERT OR IGNORE INTO quota_exhaustion (model_key, provider, day) "
        "VALUES (?, ?, ?)",
        (model_key, provider, day),
    )
    conn.commit()


def exhausted_keys(conn: sqlite3.Connection, day: str) -> set[str]:
    """Model keys known to have exhausted free quota on ``day``."""
    rows = conn.execute(
        "SELECT model_key FROM quota_exhaustion WHERE day = ?",
        (day,),
    ).fetchall()
    return {
        row[0] if not isinstance(row, sqlite3.Row) else row["model_key"] for row in rows
    }


class DailyExhaustionStore:
    """Standalone store for the agent harness (own DB file)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        ensure_exhaustion_schema(self._conn)

    def record_exhaustion(self, model_key: str, provider: str, day: str) -> None:
        record_exhaustion(self._conn, model_key, provider, day)

    def exhausted_keys(self, day: str) -> set[str]:
        return exhausted_keys(self._conn, day)

    def close(self) -> None:
        self._conn.close()
