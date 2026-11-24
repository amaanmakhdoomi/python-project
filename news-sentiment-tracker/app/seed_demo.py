"""Seed the database with fictional demo articles so the dashboard is usable offline.

Usage: python -m app.seed_demo
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import timedelta

from .config import load_settings
from .db import get_conn, init_db, now_iso, to_iso, utcnow
from .ingestion import canonical_url, normalize_title
from .pipeline import calculate_current
from .scoring import save_snapshot
from .sentiment import analyze_article

DEMO_ARTICLES = [
    {
        "title": "Party A unveils landmark welfare reform package",
        "summary": "The opposition called the plan a historic achievement and praised Party A "
        "for delivering a transparent, inclusive programme that will benefit millions.",
        "source": "National Daily",
        "domain": "national-daily.example",
        "age_hours": 2,
    },
    {
        "title": "Party B leader faces corruption probe over land deals",
        "summary": "Investigators opened a probe into allegations of bribery and fraud. Party B "
        "denied wrongdoing, but angry protesters demanded a resignation.",
        "source": "Capital Times",
        "domain": "capital-times.example",
        "age_hours": 5,
    },
    {
        "title": "Party A rally draws record crowds as momentum builds",
        "summary": "Supporters celebrated a resounding victory atmosphere, cheering the party's "
        "strong leadership and growing popularity ahead of the election.",
        "source": "Metro Wire",
        "domain": "metro-wire.example",
        "age_hours": 9,
    },
    {
        "title": "Party B slams Party A over unemployment, promises jobs",
        "summary": "Party B attacked the government's poor economic record and promised new jobs. "
        "The party said its plan offers hope to struggling families.",
        "source": "National Daily",
        "domain": "national-daily.example",
        "age_hours": 14,
    },
    {
        "title": "Party A accused of mishandling flood relief as protests erupt",
        "summary": "Critics condemned the response as negligent and incompetent. Angry residents "
        "staged a protest over delays and shortages in relief camps.",
        "source": "Capital Times",
        "domain": "capital-times.example",
        "age_hours": 20,
    },
    {
        "title": "Party B wins key alliance, boosting election prospects",
        "summary": "The agreement was hailed as a breakthrough. Party B leaders said the alliance "
        "strengthens its majority and builds optimistic momentum.",
        "source": "Metro Wire",
        "domain": "metro-wire.example",
        "age_hours": 26,
    },
    {
        "title": "Party A launches transparent digital governance initiative",
        "summary": "Independent analysts praised the reform as effective and accountable. The "
        "initiative won approval from civic groups and improved trust.",
        "source": "Civic Herald",
        "domain": "civic-herald.example",
        "age_hours": 33,
    },
    {
        "title": "Party B under fire for controversial remarks, apology demanded",
        "summary": "A backlash erupted after divisive comments. Opponents condemned the remarks as "
        "irresponsible, and even allies described the episode as an embarrassment.",
        "source": "Capital Times",
        "domain": "capital-times.example",
        "age_hours": 41,
    },
    {
        "title": "Party A and Party B clash over inflation in heated debate",
        "summary": "Both leaders attacked each other's record as prices rise. The debate exposed "
        "weakness in both camps and left voters frustrated.",
        "source": "Metro Wire",
        "domain": "metro-wire.example",
        "age_hours": 50,
    },
    {
        "title": "Party B celebrates successful membership drive",
        "summary": "Party B welcomed thousands of new members and described the drive as a "
        "success. Organisers said the response was encouraging and energising.",
        "source": "National Daily",
        "domain": "national-daily.example",
        "age_hours": 62,
    },
    {
        "title": "Party A's economic record praised by independent analysts",
        "summary": "A report highlighted strong growth, stable prices and effective reforms. "
        "Analysts said the record gives Party A a clear advantage.",
        "source": "Civic Herald",
        "domain": "civic-herald.example",
        "age_hours": 76,
    },
    {
        "title": "Party B faces defections as senior leaders quit",
        "summary": "Several senior figures resigned, deepening infighting and division. Party B "
        "called the defections a setback but insisted it remains united.",
        "source": "Capital Times",
        "domain": "capital-times.example",
        "age_hours": 91,
    },
    {
        "title": "Party A neglects rural development, say farmers",
        "summary": "Farmers protested over poverty and a shortage of support. Activists accused "
        "Party A of broken promises and neglect.",
        "source": "Rural Voice",
        "domain": "rural-voice.example",
        "age_hours": 108,
    },
    {
        "title": "Party B unveils ambitious healthcare plan",
        "summary": "The proposal was welcomed by doctors and described as a promising reform. "
        "Party B said the plan would improve care for poor families.",
        "source": "Civic Herald",
        "domain": "civic-herald.example",
        "age_hours": 130,
    },
]


def seed(force: bool = False) -> dict:
    base = load_settings()
    # Demo data must be deterministic: disable any LLM call.
    settings = dataclasses.replace(base, llm_provider="none", run_on_start=False)
    init_db(settings.db_path)

    with get_conn(settings.db_path) as conn:
        real_count = conn.execute(
            "SELECT COUNT(*) AS c FROM articles WHERE domain NOT LIKE '%.example'"
        ).fetchone()["c"]
    if real_count and not force:
        message = {
            "status": "skipped",
            "message": (
                f"Database already contains {real_count} real articles. Demo seeding is "
                "disabled so fictional stories cannot pollute real scores. Delete "
                "data/tracker.db first, or re-run with --force."
            ),
        }
        print(json.dumps(message, indent=2))
        return message

    now = utcnow()
    inserted = 0

    with get_conn(settings.db_path) as conn:
        for index, item in enumerate(DEMO_ARTICLES):
            url = f"https://{item['domain']}/demo/{index + 1}"
            published = to_iso(now - timedelta(hours=item["age_hours"]))
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO articles
                    (url, url_key, title, title_norm, summary, source, domain,
                     published_at, fetched_at, relevant)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    url,
                    canonical_url(url),
                    item["title"],
                    normalize_title(item["title"]),
                    item["summary"],
                    item["source"],
                    item["domain"],
                    published,
                    now_iso(),
                ),
            )
            if cursor.rowcount:
                inserted += 1
            row = conn.execute("SELECT id, title, summary FROM articles WHERE url_key = ?", (canonical_url(url),)).fetchone()
            result = analyze_article(row["title"], row["summary"] or "", settings)
            for party in ("A", "B"):
                payload = result[party]
                conn.execute(
                    """
                    INSERT INTO sentiments
                        (article_id, party, score, reason, method, mentioned, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(article_id, party) DO UPDATE SET
                        score = excluded.score, reason = excluded.reason,
                        method = excluded.method, mentioned = excluded.mentioned,
                        created_at = excluded.created_at
                    """,
                    (
                        row["id"], party, payload["score"], payload["reason"],
                        payload["method"], 1 if payload.get("mentioned") else 0, now_iso(),
                    ),
                )

    result = calculate_current(settings, now=now)
    with get_conn(settings.db_path) as conn:
        save_snapshot(conn, result, settings.window_days, created_at=to_iso(now))

    summary = {
        "inserted": inserted,
        "total_demo_articles": len(DEMO_ARTICLES),
        "party_a": settings.party_a_name,
        "party_b": settings.party_b_name,
        "p_a": result["p_a"],
        "p_b": result["p_b"],
        "article_count": result["article_count"],
    }
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed fictional demo articles.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="seed even when the database already contains real articles",
    )
    args = parser.parse_args()
    seed(force=args.force)
