# Election Pulse

An automated political news sentiment and win-chance tracker. It fetches recent
articles about two parties, scores how favorable or unfavorable each article is
toward each side, turns that into a time-weighted win-chance percentage, and
explains the result on a web dashboard.

This is a working MVP implementation of
`news_sentiment_win_chance_tracker_plan.md`.

> **Disclaimer:** the percentages reflect media sentiment momentum, not
> scientific voter polling. Coverage is limited to the configured sources and
> automated sentiment models can misread sarcasm, nuance and framing.

---

## What it does

| Plan phase | Implementation |
| :--- | :--- |
| 1. News collection | RSS ingestion via `feedparser`, Google News RSS queries by default, keyword filtering, URL + fuzzy-title deduplication, hourly background worker |
| 2. Aspect-based sentiment | Sentence-level ABSA: sentiment words weighted by proximity to each party, negation/intensifier handling, optional Gemini JSON scoring, per-article reason extraction |
| 3. Win-chance engine | Source-reach weighting, exponential time decay, weighted net sentiment, logistic probability conversion, optional cross-party transfer |
| 4. Dashboard | FastAPI + SQLite backend, Tailwind dashboard with split win-chance bar, trend chart, key drivers, and a filterable evidence feed |

---

## Quick start

Requirements: Python 3.10+ and either [uv](https://docs.astral.sh/uv/) or the
`python3-venv` package. `run.sh` uses whichever is available.

```bash
cd ~/news-sentiment-tracker
cp .env.example .env        # optional: set party names / feeds
./run.sh
```

Open <http://127.0.0.1:8000>.

> The default parties are named `Party A` / `Party B`, which will not match
> real news. Set real party names and aliases in `.env` before the first fetch,
> or run the demo seed below to explore the dashboard immediately.

On startup the app fetches news, scores it, saves a snapshot, and then repeats
every 60 minutes. The first fetch needs internet access.

### No internet? Load demo data

```bash
source .venv/bin/activate
python -m app.seed_demo
```

This inserts 14 fictional articles with realistic sentiment so you can explore
the full dashboard offline. The demo rows use `.example` domains.

Demo data is for exploration only. Before tracking real parties, delete
`data/tracker.db` (or change `DB_PATH`) so fictional articles cannot affect the
scores. Seeding is automatically refused if the database already contains real
articles; pass `--force` to override.

### Useful commands

```bash
# Run the pipeline once (fetch + score + snapshot) without starting the server
python -m app.pipeline

# Run the test suite
python -m unittest discover -s tests -v

# Start with auto-reload during development
./run.sh --reload
```

---

## Configuration

All settings are environment variables, loaded from `.env` if present.

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `PARTY_A_NAME` / `PARTY_B_NAME` | `Party A` / `Party B` | Display names |
| `PARTY_A_ALIASES` / `PARTY_B_ALIASES` | party name | Comma-separated mention aliases (leader names, abbreviations). Uppercase aliases are matched case-sensitively; lowercase aliases are case-insensitive. || `NEWS_FEEDS` | auto-generated Google News RSS | Comma-separated RSS/Atom URLs |
| `NEWS_LANGUAGE` / `NEWS_COUNTRY` | `en-US` / `US` | Google News locale |
| `REQUIRED_CONTEXT_TERMS` | curated political terms | Override the political-context keyword list |
| `CONTEXT_FILTER` | `true` | Set `false` to disable political-context filtering |
| `SOURCE_WEIGHTS` | built-in reach table | JSON map of domain → weight |
| `WINDOW_DAYS` | `7` | Rolling scoring window |
| `DECAY_LAMBDA` | `0.012` | Exponential time decay per hour (~58 h half-life) |
| `SENSITIVITY_K` | `2.0` | Logistic sensitivity to sentiment differences |
| `CROSS_PARTY_WEIGHT` | `0.0` | Fraction of opponent's negative score added to a party |
| `ENABLE_SCHEDULER` | `true` | Run the background update loop |
| `RUN_ON_START` | `true` | Fetch immediately when the server starts |
| `FETCH_INTERVAL_MINUTES` | `60` | Update cadence |
| `LLM_PROVIDER` | `none` | Set to `gemini` to use LLM scoring |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | empty / `gemini-2.0-flash` | Gemini credentials |
| `DB_PATH` | `data/tracker.db` | SQLite database location |

Example: track two real parties with leader aliases:

```dotenv
PARTY_A_NAME=Party One
PARTY_A_ALIASES=Party One,Leader One,PO
PARTY_B_NAME=Party Two
PARTY_B_ALIASES=Party Two,Leader Two,PT
```

---

## How the score is calculated

For each article `i` in the rolling window:

1. **Article weight** — reach of the outlet multiplied by recency decay:

   ```
   w_i = W_source,i * exp(-lambda * age_hours_i)
   ```

2. **Net sentiment per party** — weighted average of aspect-based scores in
   `[-1, +1]`:

   ```
   S_A = sum(score_A,i * w_i) / sum(w_i)
   S_B = sum(score_B,i * w_i) / sum(w_i)
   ```

3. **Advantage** — with an optional cross-party transfer:

   ```
   delta = (S_A + c * max(0, -S_B)) - (S_B + c * max(0, -S_A))
   ```

4. **Win chance** — a calibrated logistic anchored at 50/50:

   ```
   P(A) = 100 / (1 + exp(-k * delta))
   P(B) = 100 - P(A)
   ```

Probabilities are clamped to 1–99% so a single headline can never imply
certainty. With no scored articles the dashboard shows exactly 50/50 and labels
the state as having no data.

The built-in sentiment model is a lexicon-based ABSA engine: it splits text into
sentences, finds party mentions, and weights each positive/negative word by its
token distance to the nearest mention. Negations flip polarity; intensifiers and
diminishers scale it. The highest-impact sentence becomes the displayed reason.
When `LLM_PROVIDER=gemini` is configured, Gemini returns structured JSON scores
and reasons instead, with automatic fallback to the lexicon engine on error.

---

## API

| Endpoint | Description |
| :--- | :--- |
| `GET /api/status` | Current percentages, net scores, drivers, counts, last run |
| `GET /api/history?days=30` | Snapshot history for the trend chart |
| `GET /api/articles?party=A&stance=for&limit=60` | Classified evidence feed (`stance`: `all`, `for`, `against`) |
| `POST /api/refresh` | Trigger an update in the background |
| `GET /api/health` | Liveness check |

---

## Project structure

```
news-sentiment-tracker/
├── app/
│   ├── config.py       # env-driven settings, party aliases, default feeds
│   ├── db.py           # SQLite schema and helpers
│   ├── ingestion.py    # RSS fetch, filtering, deduplication
│   ├── sentiment.py    # lexicon ABSA + optional Gemini scoring
│   ├── scoring.py      # time decay, aggregation, logistic win chance
│   ├── pipeline.py     # ingest → analyze → score → snapshot
│   ├── main.py         # FastAPI app, scheduler, JSON API
│   ├── seed_demo.py    # offline demo data
│   └── static/         # dashboard (Tailwind + Chart.js)
├── tests/              # unittest suite
├── requirements.txt
├── .env.example
└── run.sh
```

---

## Tests

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

The suite covers ingestion helpers, party-specific sentiment, negation and
proximity handling, time decay, cross-party transfer, probability bounds and
driver ranking.

---

## Operational notes & limitations

- **One worker only.** The scheduler is an in-process thread. If you run multiple
  Uvicorn workers, set `ENABLE_SCHEDULER=false` and trigger `python -m app.pipeline`
  from cron/systemd instead.
- **Google News RSS** is convenient but can rate-limit or change redirect URLs.
  Add direct publisher feeds through `NEWS_FEEDS` for production use.
- **Source balance matters.** The default Google News query returns a broad mix,
  but you should curate feeds across the political spectrum to limit skew.
- **Deduplication** combines canonical URLs with fuzzy title matching
  (SequenceMatcher ≥ 0.9 over recent titles) to reduce syndicated duplicates.
- **Not a polling model.** Sentiment momentum is only one signal; do not treat the
  output as an election forecast.
- **SQLite** is fine for this MVP. Swap in PostgreSQL by replacing `db.py` if you
  need concurrent writers or larger history.
