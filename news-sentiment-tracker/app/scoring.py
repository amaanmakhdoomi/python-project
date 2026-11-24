"""Phase 3: time-decayed sentiment aggregation and win-chance calculation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from .config import Settings
from .db import now_iso, to_iso, utcnow
from .ingestion import article_age_hours

IMPACT_HIGH = 1.0
IMPACT_MEDIUM = 0.4


def source_weight(settings: Settings, domain: str | None) -> float:
    if not domain:
        return settings.default_source_weight
    return float(
        settings.source_weights.get(domain.lower(), settings.default_source_weight)
    )


def article_weight(row: Mapping[str, Any], settings: Settings, now: datetime) -> float:
    age = article_age_hours(row, now)  # type: ignore[arg-type]
    decay = math.exp(-settings.decay_lambda * age)
    return max(0.0, source_weight(settings, row["domain"]) * decay)


def impact_label(score: float, weight: float) -> str:
    strength = abs(score) * min(max(weight, 0.0), 1.5)
    if strength >= IMPACT_HIGH:
        return "high"
    if strength >= IMPACT_MEDIUM:
        return "medium"
    return "low"


def build_scored_articles(
    rows: Iterable[Mapping[str, Any]], settings: Settings, now: datetime | None = None
) -> list[dict]:
    now = now or utcnow()
    scored: list[dict] = []
    for row in rows:
        score_a = float(row["score_a"] or 0.0)
        score_b = float(row["score_b"] or 0.0)
        weight = article_weight(row, settings, now)
        scored.append(
            {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "source": row["source"],
                "domain": row["domain"],
                "published_at": row["published_at"],
                "fetched_at": row["fetched_at"],
                "score_a": score_a,
                "score_b": score_b,
                "reason_a": row["reason_a"],
                "reason_b": row["reason_b"],
                "method_a": row["method_a"],
                "method_b": row["method_b"],
                "weight": weight,
            }
        )
    return scored


def _drivers(articles: list[dict], party: str, limit: int = 3) -> list[dict]:
    key = f"score_{party.lower()}"
    reason_key = f"reason_{party.lower()}"
    ranked = [item for item in articles if abs(item[key]) > 0.01]
    ranked.sort(key=lambda item: abs(item[key]) * item["weight"], reverse=True)
    drivers = []
    for item in ranked[:limit]:
        drivers.append(
            {
                "title": item["title"],
                "url": item["url"],
                "source": item["source"],
                "published_at": item["published_at"],
                "score": round(item[key], 3),
                "reason": item[reason_key],
                "impact": impact_label(item[key], item["weight"]),
            }
        )
    return drivers


def compute_scores(
    articles: list[dict], settings: Settings, window_days: int | None = None
) -> dict:
    window_days = window_days or settings.window_days
    total_weight = sum(item["weight"] for item in articles)

    if not articles or total_weight <= 0:
        return {
            "p_a": 50.0,
            "p_b": 50.0,
            "s_a": 0.0,
            "s_b": 0.0,
            "delta": 0.0,
            "article_count": 0,
            "has_data": False,
            "window_days": window_days,
            "drivers": {"A": [], "B": []},
            "generated_at": now_iso(),
        }

    s_a = sum(item["score_a"] * item["weight"] for item in articles) / total_weight
    s_b = sum(item["score_b"] * item["weight"] for item in articles) / total_weight

    # Optional cross-party transfer: negative coverage of one side can
    # indirectly help the other in a two-party race.
    effective_a = s_a + settings.cross_party_weight * max(0.0, -s_b)
    effective_b = s_b + settings.cross_party_weight * max(0.0, -s_a)
    delta = effective_a - effective_b

    p_a = 100.0 / (1.0 + math.exp(-settings.sensitivity_k * delta))
    p_a = round(min(99.0, max(1.0, p_a)), 1)
    p_b = round(100.0 - p_a, 1)

    return {
        "p_a": p_a,
        "p_b": p_b,
        "s_a": round(s_a, 4),
        "s_b": round(s_b, 4),
        "delta": round(delta, 4),
        "article_count": len(articles),
        "has_data": True,
        "window_days": window_days,
        "drivers": {"A": _drivers(articles, "A"), "B": _drivers(articles, "B")},
        "generated_at": now_iso(),
    }


def save_snapshot(conn, result: dict, window_days: int, created_at: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO snapshots
            (created_at, window_days, p_a, p_b, s_a, s_b, delta, article_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at or now_iso(),
            window_days,
            result["p_a"],
            result["p_b"],
            result["s_a"],
            result["s_b"],
            result["delta"],
            result["article_count"],
        ),
    )


def latest_snapshot(conn, window_days: int):
    return conn.execute(
        """
        SELECT * FROM snapshots
        WHERE window_days = ?
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (window_days,),
    ).fetchone()


def history(conn, days: int = 30, window_days: int | None = None) -> list[dict]:
    cutoff = to_iso(utcnow().replace(microsecond=0) - timedelta(days=days))
    query = """
        SELECT created_at, window_days, p_a, p_b, s_a, s_b, delta, article_count
        FROM snapshots
        WHERE created_at >= ?
    """
    params: list[Any] = [cutoff]
    if window_days is not None:
        query += " AND window_days = ?"
        params.append(window_days)
    query += " ORDER BY created_at ASC, id ASC"
    return [dict(row) for row in conn.execute(query, params).fetchall()]
