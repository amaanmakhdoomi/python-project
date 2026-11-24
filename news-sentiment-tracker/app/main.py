"""FastAPI backend and dashboard host."""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import load_settings
from .db import get_conn, init_db, now_iso, to_iso, utcnow
from .pipeline import calculate_current, is_running, run_pipeline
from .scoring import article_weight, history, impact_label, source_weight

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

settings = load_settings()
STATIC_DIR = Path(__file__).resolve().parent / "static"
DISCLAIMER = (
    "These percentages reflect media sentiment momentum, not scientific voter polling. "
    "Coverage is limited to the configured news sources and may contain bias, sarcasm or "
    "errors in automated classification."
)


def _safe_run() -> None:
    try:
        run_pipeline(settings)
    except Exception:
        log.exception("Background update failed")


def _scheduler_loop() -> None:
    interval = settings.fetch_interval_minutes * 60
    log.info("Scheduler started: update every %s minutes", settings.fetch_interval_minutes)
    while True:
        time.sleep(interval)
        if not is_running():
            _safe_run()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
    if settings.run_on_start:
        threading.Thread(target=_safe_run, name="initial-update", daemon=True).start()
    if settings.enable_scheduler:
        threading.Thread(target=_scheduler_loop, name="scheduler", daemon=True).start()
    log.info(
        "Tracker ready: %s vs %s | %s feeds | db=%s",
        settings.party_a_name,
        settings.party_b_name,
        len(settings.feeds),
        settings.db_path,
    )
    yield


app = FastAPI(title="Election Pulse", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "time": now_iso()}


@app.get("/api/status")
def status(window_days: int | None = Query(default=None, ge=1, le=90)) -> JSONResponse:
    days = window_days or settings.window_days
    with get_conn(settings.db_path) as conn:
        result = calculate_current(settings, days)
        last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        total = conn.execute("SELECT COUNT(*) AS c FROM articles").fetchone()["c"]
        analyzed = conn.execute(
            """
            SELECT COUNT(*) AS c FROM (
                SELECT article_id FROM sentiments GROUP BY article_id HAVING COUNT(*) >= 2
            )
            """
        ).fetchone()["c"]

    run_payload = None
    if last_run:
        run_payload = dict(last_run)
        if run_payload.get("message"):
            with suppress(TypeError, ValueError):
                run_payload["message"] = json.loads(run_payload["message"])

    payload = {
        "party_a": settings.party_a_name,
        "party_b": settings.party_b_name,
        "window_days": days,
        "scores": {key: result[key] for key in (
            "p_a", "p_b", "s_a", "s_b", "delta", "article_count", "has_data", "generated_at"
        )},
        "drivers": result["drivers"],
        "counts": {
            "articles": total,
            "analyzed": analyzed,
            "feeds": len(settings.feeds),
        },
        "last_run": run_payload,
        "scheduler": {
            "enabled": settings.enable_scheduler,
            "interval_minutes": settings.fetch_interval_minutes,
            "running": is_running(),
        },
        "llm": settings.llm_provider,
        "disclaimer": DISCLAIMER,
    }
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/history")
def history_endpoint(
    days: int = Query(default=30, ge=1, le=365),
    window_days: int | None = Query(default=None, ge=1, le=90),
) -> JSONResponse:
    with get_conn(settings.db_path) as conn:
        rows = history(conn, days=days, window_days=window_days)
    payload = {
        "party_a": settings.party_a_name,
        "party_b": settings.party_b_name,
        "history": rows,
    }
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.get("/api/articles")
def articles(
    party: str = Query(default="A", pattern="^[AaBb]$"),
    stance: str = Query(default="all", pattern="^(for|against|all)$"),
    limit: int = Query(default=40, ge=1, le=200),
    window_days: int | None = Query(default=None, ge=1, le=90),
    q: str | None = Query(default=None, max_length=200),
) -> JSONResponse:
    party = party.upper()
    days = window_days or settings.window_days
    cutoff = to_iso(utcnow().replace(microsecond=0) - timedelta(days=days))

    query = """
        SELECT a.*, s.score, s.reason, s.method
        FROM articles a
        JOIN sentiments s ON s.article_id = a.id
        WHERE s.party = ? AND s.mentioned = 1 AND COALESCE(a.published_at, a.fetched_at) >= ?
    """
    params: list[object] = [party, cutoff]
    if stance == "for":
        query += " AND s.score >= 0.05"
    elif stance == "against":
        query += " AND s.score <= -0.05"
    if q:
        query += " AND (a.title LIKE ? OR a.summary LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    query += " ORDER BY COALESCE(a.published_at, a.fetched_at) DESC LIMIT ?"
    params.append(limit)

    with get_conn(settings.db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    now = utcnow()
    items = []
    for row in rows:
        score = float(row["score"])
        weight = article_weight(row, settings, now)
        label = "for" if score >= 0.05 else "against" if score <= -0.05 else "neutral"
        items.append(
            {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "source": row["source"],
                "domain": row["domain"],
                "published_at": row["published_at"],
                "fetched_at": row["fetched_at"],
                "score": round(score, 3),
                "reason": row["reason"],
                "method": row["method"],
                "stance": label,
                "weight": round(weight, 3),
                "source_weight": round(source_weight(settings, row["domain"]), 2),
                "impact": impact_label(score, weight),
            }
        )

    payload = {
        "party": party,
        "party_name": settings.party_names[party],
        "stance": stance,
        "window_days": days,
        "count": len(items),
        "items": items,
    }
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@app.post("/api/refresh")
def refresh(background_tasks: BackgroundTasks) -> dict:
    if is_running():
        return {"started": False, "reason": "An update is already running."}
    background_tasks.add_task(run_pipeline, settings)
    return {"started": True, "message": "Update started."}
