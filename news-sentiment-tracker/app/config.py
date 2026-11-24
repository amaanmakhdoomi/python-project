"""Configuration loaded from environment variables and an optional .env file."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (keeps dependencies light)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


_load_dotenv(BASE_DIR / ".env")


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


DEFAULT_SOURCE_WEIGHTS: dict[str, float] = {
    # Rough reach-based defaults. Override with the SOURCE_WEIGHTS env var.
    "reuters.com": 1.5,
    "apnews.com": 1.5,
    "bbc.com": 1.3,
    "bbc.co.uk": 1.3,
    "nytimes.com": 1.3,
    "washingtonpost.com": 1.3,
    "wsj.com": 1.3,
    "bloomberg.com": 1.25,
    "theguardian.com": 1.2,
    "aljazeera.com": 1.2,
    "cnn.com": 1.2,
    "foxnews.com": 1.2,
    "politico.com": 1.15,
    "thehindu.com": 1.15,
    "indianexpress.com": 1.15,
    "timesofindia.indiatimes.com": 1.1,
    "ndtv.com": 1.1,
    "hindustantimes.com": 1.1,
    "scmp.com": 1.1,
    "dw.com": 1.1,
    "france24.com": 1.1,
    "npr.org": 1.1,
}

# Articles must contain at least one of these terms to be treated as political
# news. This filters out false positives such as "watch party" or "join party".
# Disable with CONTEXT_FILTER=false.
DEFAULT_CONTEXT_TERMS: tuple[str, ...] = (
    "election", "elections", "poll", "polls", "vote", "votes", "voter", "voters",
    "campaign", "campaigning", "candidate", "candidates", "parliament",
    "parliamentary", "legislature", "assembly", "minister", "ministry",
    "government", "opposition", "ruling", "coalition", "alliance", "ballot",
    "constituency", "constituencies", "seat", "seats", "lawmaker", "lawmakers",
    "mp", "mla", "president", "prime", "premier", "mayor", "governor", "senator",
    "congress", "policy", "policies", "reform", "reforms", "governance",
    "politics", "political", "referendum", "manifesto", "rally", "leader",
    "leaders", "economic", "economy", "budget", "tax", "taxes", "jobs",
    "unemployment", "inflation", "healthcare", "health", "welfare", "education",
    "development", "corruption", "scandal", "protest", "protests", "resignation",
    "defection", "defections", "security", "immigration", "climate", "energy",
    "agriculture", "farmers", "poverty", "crime", "justice", "court", "supreme",
    "constitution", "bill", "law", "cabinet", "spokesman", "spokesperson",
)


def _default_feeds(aliases_a: tuple[str, ...], aliases_b: tuple[str, ...]) -> tuple[str, ...]:
    """Build Google News RSS searches so the MVP needs no API key."""
    language = os.getenv("NEWS_LANGUAGE", "en-US")
    country = os.getenv("NEWS_COUNTRY", "US")
    ceid = f"{country}:{language.split('-')[0]}"

    def feed(query: str) -> str:
        return (
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + f"&hl={language}&gl={country}&ceid={ceid}"
        )

    a_terms = " OR ".join(f'"{alias}"' for alias in aliases_a)
    b_terms = " OR ".join(f'"{alias}"' for alias in aliases_b)
    context = (
        "(election OR polls OR vote OR campaign OR parliament OR minister OR "
        "government OR opposition OR leader)"
    )
    return (
        feed(f"({a_terms} OR {b_terms}) {context} when:3d"),
        feed(f"{a_terms} {context} when:7d"),
        feed(f"{b_terms} {context} when:7d"),
    )


@dataclass(frozen=True)
class Settings:
    party_a_name: str
    party_b_name: str
    party_a_aliases: tuple[str, ...]
    party_b_aliases: tuple[str, ...]
    feeds: tuple[str, ...]
    db_path: Path
    window_days: int
    decay_lambda: float
    sensitivity_k: float
    cross_party_weight: float
    default_source_weight: float
    source_weights: dict[str, float]
    request_timeout: float
    max_articles_per_feed: int
    required_context_terms: tuple[str, ...]
    context_filter: bool
    enable_scheduler: bool
    run_on_start: bool
    fetch_interval_minutes: int
    max_analyze_per_run: int
    llm_provider: str
    gemini_api_key: str
    gemini_model: str
    llm_timeout: float

    @property
    def party_names(self) -> dict[str, str]:
        return {"A": self.party_a_name, "B": self.party_b_name}

    def aliases(self, party: str) -> tuple[str, ...]:
        return self.party_a_aliases if party.upper() == "A" else self.party_b_aliases

    @classmethod
    def from_env(cls) -> Settings:
        a_name = (os.getenv("PARTY_A_NAME") or "Party A").strip()
        b_name = (os.getenv("PARTY_B_NAME") or "Party B").strip()

        a_aliases = tuple(_csv(os.getenv("PARTY_A_ALIASES"))) or (a_name,)
        b_aliases = tuple(_csv(os.getenv("PARTY_B_ALIASES"))) or (b_name,)
        if a_name not in a_aliases:
            a_aliases = (a_name, *a_aliases)
        if b_name not in b_aliases:
            b_aliases = (b_name, *b_aliases)

        feeds = tuple(_csv(os.getenv("NEWS_FEEDS"))) or _default_feeds(a_aliases, b_aliases)

        weights = dict(DEFAULT_SOURCE_WEIGHTS)
        raw_weights = os.getenv("SOURCE_WEIGHTS")
        if raw_weights:
            try:
                parsed = json.loads(raw_weights)
                if isinstance(parsed, dict):
                    weights.update(
                        {str(key).lower(): float(value) for key, value in parsed.items()}
                    )
            except (TypeError, ValueError):
                pass

        db_path = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "tracker.db"))).expanduser()

        raw_context = os.getenv("REQUIRED_CONTEXT_TERMS")
        context_terms = (
            tuple(_csv(raw_context)) if raw_context and raw_context.strip() else DEFAULT_CONTEXT_TERMS
        )

        return cls(
            party_a_name=a_name,
            party_b_name=b_name,
            party_a_aliases=a_aliases,
            party_b_aliases=b_aliases,
            feeds=feeds,
            db_path=db_path,
            window_days=max(1, _env_int("WINDOW_DAYS", 7)),
            decay_lambda=max(0.0, _env_float("DECAY_LAMBDA", 0.012)),
            sensitivity_k=max(0.01, _env_float("SENSITIVITY_K", 2.0)),
            cross_party_weight=min(1.0, max(0.0, _env_float("CROSS_PARTY_WEIGHT", 0.0))),
            default_source_weight=max(0.0, _env_float("DEFAULT_SOURCE_WEIGHT", 1.0)),
            source_weights=weights,
            request_timeout=max(1.0, _env_float("REQUEST_TIMEOUT_SECONDS", 20.0)),
            max_articles_per_feed=max(1, _env_int("MAX_ARTICLES_PER_FEED", 40)),
            required_context_terms=context_terms,
            context_filter=_env_bool("CONTEXT_FILTER", True),
            enable_scheduler=_env_bool("ENABLE_SCHEDULER", True),
            run_on_start=_env_bool("RUN_ON_START", True),
            fetch_interval_minutes=max(1, _env_int("FETCH_INTERVAL_MINUTES", 60)),
            max_analyze_per_run=max(1, _env_int("MAX_ANALYZE_PER_RUN", 250)),
            llm_provider=(os.getenv("LLM_PROVIDER", "none") or "none").strip().lower(),
            gemini_api_key=(os.getenv("GEMINI_API_KEY") or "").strip(),
            gemini_model=(os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip(),
            llm_timeout=max(1.0, _env_float("LLM_TIMEOUT_SECONDS", 20.0)),
        )


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    return Settings.from_env()
