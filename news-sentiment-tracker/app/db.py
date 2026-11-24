"""SQLite storage layer."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT NOT NULL,
    url_key       TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    title_norm    TEXT NOT NULL,
    summary       TEXT,
    source        TEXT,
    domain        TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL,
    relevant      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_articles_published
    ON articles (COALESCE(published_at, fetched_at));
CREATE INDEX IF NOT EXISTS idx_articles_title_norm ON articles (title_norm);

CREATE TABLE IF NOT EXISTS sentiments (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    party       TEXT NOT NULL CHECK (party IN ('A', 'B')),
    score       REAL NOT NULL,
    reason      TEXT,
    method      TEXT NOT NULL,
    mentioned   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (article_id, party)
);

CREATE INDEX IF NOT EXISTS idx_sentiments_party_score ON sentiments (party, score);

CREATE TABLE IF NOT EXISTS snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    window_days    INTEGER NOT NULL,
    p_a            REAL NOT NULL,
    p_b            REAL NOT NULL,
    s_a            REAL NOT NULL,
    s_b            REAL NOT NULL,
    delta          REAL NOT NULL,
    article_count  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots (created_at);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,
    fetched     INTEGER NOT NULL DEFAULT 0,
    inserted    INTEGER NOT NULL DEFAULT 0,
    analyzed    INTEGER NOT NULL DEFAULT 0,
    message     TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return to_iso(utcnow())


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def get_conn(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sentiments)")}
        if "mentioned" not in columns:
            # Existing databases created before mention tracking: keep their rows
            # visible, then re-analysis will fill in accurate values.
            conn.execute(
                "ALTER TABLE sentiments ADD COLUMN mentioned INTEGER NOT NULL DEFAULT 1"
            )
