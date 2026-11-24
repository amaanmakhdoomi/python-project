"""Phase 1: fetch, filter and deduplicate news articles from RSS feeds."""

from __future__ import annotations

import calendar
import html
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import httpx

from .config import Settings
from .db import get_conn, now_iso, parse_iso, to_iso, utcnow

log = logging.getLogger(__name__)

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
    "cmpid",
    "smid",
    "s_cid",
}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_NORM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Article:
    url: str
    url_key: str
    title: str
    title_norm: str
    summary: str
    source: str
    domain: str
    published_at: str | None


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = _TAG_RE.sub(" ", value)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def normalize_title(title: str) -> str:
    return _NORM_RE.sub(" ", title.lower()).strip()


def canonical_url(url: str) -> str:
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url.strip()
    if not parts.scheme:
        return url.strip()
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in _TRACKING_PARAMS
    ]
    netloc = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunparse((parts.scheme.lower(), netloc, path, "", urlencode(query), ""))


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return netloc.removeprefix("www.")


@lru_cache(maxsize=32)
def alias_pattern(aliases: tuple[str, ...]) -> re.Pattern[str]:
    """Match aliases with word boundaries.

    Aliases containing uppercase letters are matched case-sensitively (proper
    nouns such as "Party A"), which avoids false positives like "join party a".
    Lowercase aliases are matched case-insensitively.
    """
    parts = []
    for alias in sorted(aliases, key=len, reverse=True):
        escaped = re.escape(alias)
        if any(character.isupper() for character in alias):
            parts.append(f"(?-i:{escaped})")
        else:
            parts.append(f"(?i:{escaped})")
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)")


def matches_any(text: str, aliases: tuple[str, ...]) -> bool:
    return bool(text) and bool(alias_pattern(aliases).search(text))


def _entry_datetime(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                stamp = calendar.timegm(value)
                return to_iso(datetime.fromtimestamp(stamp, tz=timezone.utc))
            except (TypeError, ValueError, OverflowError):
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)
        if raw:
            try:
                parsed = parsedate_to_datetime(raw)
                return to_iso(parsed)
            except (TypeError, ValueError, OverflowError):
                pass
    return None


def _entry_source(entry, feed_title: str, url_key: str) -> tuple[str, str]:
    source_name = ""
    source_domain = ""
    source = entry.get("source")
    if isinstance(source, dict):
        source_name = strip_html(source.get("title") or "")
        href = source.get("href") or ""
        if href:
            source_domain = domain_of(canonical_url(href))
    if not source_name:
        source_name = strip_html(feed_title) or domain_of(url_key)
    return source_name, source_domain or domain_of(url_key)


def _recent_titles(conn: sqlite3.Connection, days: int = 10) -> list[tuple[int, str]]:
    cutoff = to_iso(utcnow() - timedelta(days=days))
    rows = conn.execute(
        """
        SELECT id, title_norm FROM articles
        WHERE COALESCE(published_at, fetched_at) >= ?
        ORDER BY id DESC LIMIT 1500
        """,
        (cutoff,),
    ).fetchall()
    return [(row["id"], row["title_norm"]) for row in rows]


def _is_duplicate_title(title_norm: str, known: list[str]) -> bool:
    if not title_norm:
        return True
    for existing in known:
        if existing == title_norm:
            return True
        if abs(len(existing) - len(title_norm)) > max(12, len(title_norm) * 0.4):
            continue
        if SequenceMatcher(None, title_norm, existing).ratio() >= 0.9:
            return True
    return False


def fetch_feed(url: str, settings: Settings) -> tuple[list[Article], str | None]:
    """Fetch one RSS/Atom feed. Returns (articles, error)."""
    try:
        response = httpx.get(
            url,
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "NewsSentimentTracker/1.0",
                "Cache-Control": "no-cache",
            },
        )
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
    except Exception as exc:  # noqa: BLE001 - network failures must not kill the run
        return [], f"{url}: {exc}"

    feed_title = strip_html(parsed.feed.get("title", "")) if getattr(parsed, "feed", None) else ""
    articles: list[Article] = []
    for entry in parsed.entries[: settings.max_articles_per_feed]:
        title = strip_html(entry.get("title", ""))
        link = entry.get("link", "") or ""
        if not title or not link:
            continue
        url_key = canonical_url(link)
        summary = strip_html(entry.get("summary") or entry.get("description") or "")[:4000]
        source, domain = _entry_source(entry, feed_title, url_key)
        articles.append(
            Article(
                url=link.strip(),
                url_key=url_key,
                title=title[:500],
                title_norm=normalize_title(title),
                summary=summary,
                source=source[:200],
                domain=domain[:200],
                published_at=_entry_datetime(entry),
            )
        )

    error = None
    if not articles:
        bozo = getattr(parsed, "bozo_exception", None)
        error = f"{url}: no entries" + (f" ({bozo})" if bozo else "")
    return articles, error


def ingest(settings: Settings) -> dict:
    """Run one ingestion pass over all configured feeds."""
    stats = {
        "feeds_total": len(settings.feeds),
        "feeds_ok": 0,
        "feeds_failed": 0,
        "fetched": 0,
        "relevant": 0,
        "filtered": 0,
        "inserted": 0,
        "duplicates": 0,
        "errors": [],
    }
    all_aliases = settings.party_a_aliases + settings.party_b_aliases

    with get_conn(settings.db_path) as conn:
        known = _recent_titles(conn)
        known_titles = [title for _, title in known]

        for feed_url in settings.feeds:
            articles, error = fetch_feed(feed_url, settings)
            if error:
                stats["feeds_failed"] += 1
                stats["errors"].append(error)
                continue
            stats["feeds_ok"] += 1

            for article in articles:
                stats["fetched"] += 1
                text = f"{article.title} {article.summary}"
                if not matches_any(text, all_aliases):
                    continue
                if settings.required_context_terms and settings.context_filter and not matches_any(
                    text, settings.required_context_terms
                ):
                    stats["filtered"] += 1
                    continue
                stats["relevant"] += 1

                if _is_duplicate_title(article.title_norm, known_titles):
                    stats["duplicates"] += 1
                    continue

                try:
                    conn.execute(
                        """
                        INSERT INTO articles
                            (url, url_key, title, title_norm, summary, source, domain,
                             published_at, fetched_at, relevant)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            article.url,
                            article.url_key,
                            article.title,
                            article.title_norm,
                            article.summary,
                            article.source,
                            article.domain,
                            article.published_at,
                            now_iso(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    stats["duplicates"] += 1
                    continue

                stats["inserted"] += 1
                known_titles.append(article.title_norm)
            conn.commit()

    log.info(
        "Ingestion: %s fetched, %s relevant, %s filtered, %s inserted",
        stats["fetched"],
        stats["relevant"],
        stats["filtered"],
        stats["inserted"],
    )
    return stats


def pending_articles(settings: Settings, limit: int | None = None) -> list[sqlite3.Row]:
    """Articles that do not yet have sentiment rows for both parties."""
    limit = limit or settings.max_analyze_per_run
    with get_conn(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.* FROM articles a
            WHERE (
                SELECT COUNT(*) FROM sentiments s WHERE s.article_id = a.id
            ) < 2
            ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return list(rows)


def article_window(
    settings: Settings, window_days: int, now: datetime | None = None
) -> list[sqlite3.Row]:
    now = now or utcnow()
    cutoff = to_iso(now - timedelta(days=window_days))
    with get_conn(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.*, sa.score AS score_a, sa.reason AS reason_a, sa.method AS method_a,
                   sb.score AS score_b, sb.reason AS reason_b, sb.method AS method_b
            FROM articles a
            JOIN sentiments sa ON sa.article_id = a.id AND sa.party = 'A'
            JOIN sentiments sb ON sb.article_id = a.id AND sb.party = 'B'
            WHERE COALESCE(a.published_at, a.fetched_at) >= ?
            """,
            (cutoff,),
        ).fetchall()
    return list(rows)


def article_age_hours(row: sqlite3.Row, now: datetime | None = None) -> float:
    now = now or utcnow()
    stamp = parse_iso(row["published_at"]) or parse_iso(row["fetched_at"]) or now
    return max(0.0, (now - stamp).total_seconds() / 3600.0)
