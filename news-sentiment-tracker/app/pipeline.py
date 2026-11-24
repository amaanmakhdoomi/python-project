"""Pipeline orchestration: ingest -> analyze -> score -> snapshot."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime

from .config import Settings, load_settings
from .db import get_conn, now_iso
from .ingestion import article_window, ingest, pending_articles
from .scoring import build_scored_articles, compute_scores, save_snapshot
from .sentiment import analyze_article

log = logging.getLogger(__name__)

_lock = threading.Lock()


def is_running() -> bool:
    return _lock.locked()


def _analyze_pending(settings: Settings) -> int:
    rows = pending_articles(settings)
    if not rows:
        return 0

    analyzed = 0
    with get_conn(settings.db_path) as conn:
        for row in rows:
            try:
                result = analyze_article(row["title"], row["summary"] or "", settings)
            except Exception as exc:  # noqa: BLE001 - one bad article must not stop the run
                log.warning("Sentiment failed for article %s: %s", row["id"], exc)
                continue
            for party in ("A", "B"):
                payload = result[party]
                conn.execute(
                    """
                    INSERT INTO sentiments
                        (article_id, party, score, reason, method, mentioned, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(article_id, party) DO UPDATE SET
                        score = excluded.score,
                        reason = excluded.reason,
                        method = excluded.method,
                        mentioned = excluded.mentioned,
                        created_at = excluded.created_at
                    """,
                    (
                        row["id"],
                        party,
                        payload["score"],
                        payload["reason"],
                        payload["method"],
                        1 if payload.get("mentioned") else 0,
                        now_iso(),
                    ),
                )
            analyzed += 1
            if analyzed % 25 == 0:
                conn.commit()
    return analyzed


def calculate_current(
    settings: Settings, window_days: int | None = None, now: datetime | None = None
) -> dict:
    window_days = window_days or settings.window_days
    rows = article_window(settings, window_days, now)
    scored = build_scored_articles(rows, settings, now)
    return compute_scores(scored, settings, window_days)


def run_pipeline(
    settings: Settings | None = None,
    window_days: int | None = None,
    save: bool = True,
) -> dict:
    """Run one full update. Safe to call from background threads."""
    settings = settings or load_settings()
    window_days = window_days or settings.window_days

    if not _lock.acquire(blocking=False):
        return {"status": "already_running", "message": "An update is already in progress."}

    run_id: int | None = None
    try:
        with get_conn(settings.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, status) VALUES (?, 'running')",
                (now_iso(),),
            )
            run_id = int(cursor.lastrowid)

        stats = ingest(settings)
        analyzed = _analyze_pending(settings)
        result = calculate_current(settings, window_days)

        if save:
            with get_conn(settings.db_path) as conn:
                save_snapshot(conn, result, window_days)

        summary = {
            "status": "ok",
            "run_id": run_id,
            "finished_at": now_iso(),
            "ingest": stats,
            "analyzed": analyzed,
            "scores": {
                "p_a": result["p_a"],
                "p_b": result["p_b"],
                "article_count": result["article_count"],
            },
        }

        if run_id is not None:
            with get_conn(settings.db_path) as conn:
                conn.execute(
                    """
                    UPDATE runs
                    SET finished_at = ?, status = 'ok', fetched = ?, inserted = ?,
                        analyzed = ?, message = ?
                    WHERE id = ?
                    """,
                    (
                        summary["finished_at"],
                        stats["fetched"],
                        stats["inserted"],
                        analyzed,
                        json.dumps(
                            {
                                "feeds_failed": stats["feeds_failed"],
                                "duplicates": stats["duplicates"],
                                "errors": stats["errors"][:5],
                            }
                        ),
                        run_id,
                    ),
                )
        log.info(
            "Pipeline done: %s fetched, %s inserted, %s analyzed, A=%s%% B=%s%%",
            stats["fetched"],
            stats["inserted"],
            analyzed,
            result["p_a"],
            result["p_b"],
        )
        return summary
    except Exception as exc:
        log.exception("Pipeline failed")
        if run_id is not None:
            try:
                with get_conn(settings.db_path) as conn:
                    conn.execute(
                        """
                        UPDATE runs SET finished_at = ?, status = 'error', message = ?
                        WHERE id = ?
                        """,
                        (now_iso(), str(exc)[:1000], run_id),
                    )
            except Exception:
                log.exception("Could not record failed run")
        return {"status": "error", "run_id": run_id, "message": str(exc)}
    finally:
        _lock.release()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    from .db import init_db

    init_db(settings.db_path)
    summary = run_pipeline(settings)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
