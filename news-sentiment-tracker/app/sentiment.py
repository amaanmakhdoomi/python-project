"""Phase 2: aspect-based sentiment analysis ("for" vs. "against" each party).

The default engine is a dependency-free, lexicon-based ABSA model:

* text is split into sentences,
* each sentiment word is weighted by its proximity to a party mention,
* negation, intensifiers and diminishers are handled,
* the result is squashed into [-1, 1] with tanh.

An optional Gemini LLM path can be enabled with LLM_PROVIDER=gemini; it is
used when available and automatically falls back to the lexicon model.
"""

from __future__ import annotations

import json
import logging
import math
import re
from functools import lru_cache

import httpx

from .config import Settings

log = logging.getLogger(__name__)

_STRONG_POSITIVE = {
    "landslide",
    "resounding",
    "triumph",
    "triumphant",
    "historic",
    "landmark",
    "outstanding",
    "exceptional",
    "decisive",
    "visionary",
    "exemplary",
    "unprecedented",
    "overwhelming",
}
_POSITIVE = {
    "win", "wins", "winning", "won", "victory", "victories", "success", "successful",
    "boost", "surge", "gain", "gains", "rise", "rises", "rising", "lead", "leading",
    "ahead", "popular", "popularity", "praise", "praised", "applaud", "applauded",
    "applauds", "support", "supporters", "backing", "backed", "endorse", "endorsed",
    "endorsement", "strong", "strength", "strengthen", "strengthened", "growth",
    "progress", "reform", "reforms", "achievement", "achievements", "record",
    "welfare", "development", "improve", "improved", "improvement", "promising",
    "confident", "confidence", "unite", "united", "unity", "stable", "stability",
    "transparent", "accountable", "credible", "trust", "trusted", "momentum",
    "advantage", "dominate", "dominating", "celebrate", "celebrated", "welcome",
    "welcomed", "hailed", "hail", "hails", "breakthrough", "deliver", "delivered",
    "delivers", "fulfil", "fulfilled", "effective", "efficient", "capable",
    "charismatic", "honest", "integrity", "clean", "resilient", "prosperous",
    "thriving", "flourish", "accord", "agreement", "alliance", "rally", "energized",
    "enthusiastic", "optimism", "optimistic", "hope", "hopeful", "satisfaction",
    "satisfied", "approve", "approval", "highest", "best", "better", "good", "great",
    "excellent", "impressive", "favorable", "favourable", "positive", "expand",
    "expanded", "majority", "mandate", "frontrunner", "favorite", "favourite",
    "cheers", "cheered", "commends", "commended", "lauded", "lauds", "honoured",
    "honored", "respect", "respected", "admired", "inspiring", "inspired", "hero",
    "heroic", "sweeping", "dominates", "secured", "secure", "recovered", "recovery",
    "thrive", "prosperity", "peace", "peaceful", "harmony", "benefit", "benefits",
    "benefited", "empower", "empowered", "inclusive", "progressive", "credibility",
}
_WEAK_POSITIVE = {
    "okay",
    "adequate",
    "acceptable",
    "steady",
    "notable",
    "encouraging",
    "welcome",
}

_STRONG_NEGATIVE = {
    "scandal",
    "corruption",
    "corrupt",
    "scam",
    "fraud",
    "loot",
    "embezzlement",
    "bribery",
    "betrayal",
    "disgrace",
    "disgraceful",
    "shameful",
    "disastrous",
    "catastrophic",
    "massacre",
    "genocide",
    "unconstitutional",
    "illegal",
    "unlawful",
    "collusion",
    "conspiracy",
    "riots",
    "chaos",
}
_NEGATIVE = {
    "lose", "loses", "lost", "loss", "losses", "defeat", "defeated", "fail", "fails",
    "failed", "failure", "probe", "investigation", "investigate", "investigated",
    "raid", "raids", "arrest", "arrested", "charge", "charged", "charges",
    "allegation", "allegations", "accused", "accuse", "accuses", "controversy",
    "controversial", "protest", "protests", "clash", "clashes", "unrest",
    "criticism", "criticize", "criticized", "criticised", "criticise", "slam",
    "slams", "slammed", "attack", "attacks", "attacked", "blast", "blasts",
    "blasting", "condemn", "condemned", "condemns", "oppose", "opposed",
    "resignation", "resign", "resigns", "resigned", "quit", "quits", "defection",
    "defections", "desert", "deserted", "threat", "threats", "intimidate",
    "intimidation", "crackdown", "censored", "censorship", "ban", "banned",
    "backlash", "outrage", "angry", "anger", "furious", "demonstration", "strike",
    "boycott", "decline", "declining", "drop", "dropped", "fall", "falling",
    "slump", "setback", "blow", "damage", "damaged", "hurt", "harm", "weak",
    "weakness", "unpopular", "dismal", "poor", "bad", "worse", "worst", "terrible",
    "shocking", "humiliating", "embarrassment", "trouble", "troubled", "turmoil",
    "instability", "unstable", "division", "divided", "split", "rift", "infighting",
    "faction", "factionalism", "dispute", "row", "feud", "tussle", "standoff",
    "deadlock", "stalemate", "delay", "delays", "stalled", "halt", "halted",
    "suspended", "shutdown", "shortage", "scarcity", "inflation", "unemployment",
    "costly", "expensive", "debt", "deficit", "loan", "bankruptcy", "sanction",
    "sanctions", "penalty", "fine", "contempt", "violation", "violations",
    "mislead", "misleading", "false", "smear", "hate", "hatred", "divisive",
    "polarizing", "communal", "injured", "casualties", "disaster", "accident",
    "mishap", "negligence", "negligent", "irresponsible", "incompetent", "inept",
    "clueless", "puppet", "tainted", "embroiled", "engulfed", "mired", "plagued",
    "rocked", "battered", "reeling", "embattled", "besieged", "flak", "ire",
    "wrath", "censure", "questionable", "dubious", "suspicious", "improper",
    "unethical", "wrongdoing", "misconduct", "abuse", "abused", "harassment",
    "discrimination", "inequality", "poverty", "hunger", "jobless", "joblessness",
    "hike", "hikes", "burden", "suffering", "hardship", "distress", "worry",
    "concerns", "concern", "alarming", "grave", "damning", "explosive", "secret",
    "backdoor", "discontent", "resentment", "hostility", "quarrel",
    "meltdown", "debacle", "fiasco", "blunder", "misstep", "gaffe", "uproar",
    "fury", "outcry", "condemnation", "indictment", "convicted", "conviction",
    "guilty", "jailed", "prison", "custody", "bail", "summons", "seized",
    "frozen", "freeze", "blocked", "rejected", "denied", "dismissed", "snub",
    "snubbed", "isolated", "marginalized", "neglected", "betrayed", "deceived",
    "damaging", "suffer", "suffers", "suffered", "harmful",
}
_WEAK_NEGATIVE = {
    "concern",
    "question",
    "doubt",
    "uncertain",
    "delay",
    "criticism",
    "slightly",
}

_NEGATIONS = {
    "not", "no", "never", "neither", "nor", "hardly", "barely", "scarcely",
    "without", "denies", "denied", "deny", "refuses", "refused", "refuse",
    "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't",
    "won't", "wouldn't", "can't", "couldn't", "cannot", "lacks", "lacked",
}
_INTENSIFIERS = {
    "very", "extremely", "hugely", "massively", "sharply", "strongly", "deeply",
    "severely", "widely", "major", "majorly", "significant", "significantly",
    "huge", "massive", "severe", "fierce", "bitter", "outright", "blatant",
    "utter", "total", "complete", "completely", "absolutely", "resounding",
    "increasingly", "growing", "mounting", "escalating",
}
_DIMINISHERS = {
    "slightly", "somewhat", "mildly", "marginally", "reportedly", "allegedly",
    "apparent", "apparently", "possible", "possibly", "minor", "small", "brief",
    "occasional", "isolated", "alleged",
}

LEXICON: dict[str, float] = {}
LEXICON.update(dict.fromkeys(_STRONG_POSITIVE, 1.5))
LEXICON.update(dict.fromkeys(_POSITIVE, 1.0))
LEXICON.update(dict.fromkeys(_WEAK_POSITIVE, 0.5))
LEXICON.update(dict.fromkeys(_STRONG_NEGATIVE, -1.6))
LEXICON.update(dict.fromkeys(_NEGATIVE, -1.0))
LEXICON.update(dict.fromkeys(_WEAK_NEGATIVE, -0.5))

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_SPLIT = re.compile(
    r"\b(?:while|whereas|but|however|yet|although|though|despite|nevertheless|nonetheless)\b|;",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_POSSESSIVE_RE = re.compile(r"'s\b")
_MAX_TOKEN_DISTANCE = 12
_PROXIMITY_SCALE = 5.0
_SCORE_DIVISOR = 4.0


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(_POSSESSIVE_RE.sub("", text.lower()))


@lru_cache(maxsize=32)
def _alias_token_sequences(aliases: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    sequences = []
    for alias in aliases:
        tokens = tuple(_tokenize(alias))
        if tokens:
            sequences.append(tokens)
    return tuple(sequences)


def _split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    return [part.strip() for part in _SENTENCE_SPLIT.split(cleaned) if part.strip()]


def _split_clauses(sentence: str) -> list[str]:
    return [part.strip() for part in _CLAUSE_SPLIT.split(sentence) if part.strip()]


def _mention_positions(tokens: list[str], aliases: tuple[str, ...]) -> list[int]:
    positions: list[int] = []
    for sequence in _alias_token_sequences(aliases):
        size = len(sequence)
        if size == 0:
            continue
        for start in range(len(tokens) - size + 1):
            if tuple(tokens[start : start + size]) == sequence:
                positions.extend(range(start, start + size))
    return positions


def _sentence_score(
    sentence: str, aliases: tuple[str, ...], weight: float
) -> tuple[float, str | None]:
    tokens = _tokenize(sentence)
    if not tokens:
        return 0.0, None

    mentions = _mention_positions(tokens, aliases)
    if not mentions:
        return 0.0, None

    total = 0.0
    for index, token in enumerate(tokens):
        polarity = LEXICON.get(token)
        if polarity is None:
            continue

        distance = min(abs(index - mention) for mention in mentions)
        if distance > _MAX_TOKEN_DISTANCE:
            continue

        if any(previous in _NEGATIONS for previous in tokens[max(0, index - 3) : index]):
            polarity *= -0.8
        if index > 0 and tokens[index - 1] in _INTENSIFIERS:
            polarity *= 1.5
        elif index > 0 and tokens[index - 1] in _DIMINISHERS:
            polarity *= 0.6

        total += polarity * (1.0 / (1.0 + distance / _PROXIMITY_SCALE))

    total *= weight
    reason = sentence.strip() if abs(total) >= 0.45 else None
    if reason and len(reason) > 240:
        reason = reason[:237].rstrip() + "..."
    return total, reason


def _lexicon_score(
    text: str, aliases: tuple[str, ...], weight: float = 1.0
) -> tuple[float, str | None]:
    best_total = 0.0
    best_reason: str | None = None
    for sentence in _split_sentences(text):
        for clause in _split_clauses(sentence):
            subtotal, reason = _sentence_score(clause, aliases, weight)
            if abs(subtotal) > abs(best_total):
                best_total = subtotal
                best_reason = reason
    return best_total, best_reason


def lexicon_analyze(title: str, summary: str, settings: Settings) -> dict[str, dict]:
    results: dict[str, dict] = {}
    full_text = f"{title} {summary}"
    for party in ("A", "B"):
        aliases = settings.aliases(party)
        title_total, title_reason = _lexicon_score(title, aliases, weight=1.6)
        body_total, body_reason = _lexicon_score(summary, aliases, weight=1.0)
        raw = title_total + body_total
        score = math.tanh(raw / _SCORE_DIVISOR)
        score = round(max(-1.0, min(1.0, score)), 4)
        results[party] = {
            "score": score,
            "reason": body_reason or title_reason,
            "method": "lexicon",
            "mentioned": _mentions_any(full_text, aliases),
        }
    return results


def _clamp_sentiment(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(max(-1.0, min(1.0, number)), 4)


def _gemini_analyze(title: str, summary: str, settings: Settings) -> dict[str, dict] | None:
    if settings.llm_provider != "gemini" or not settings.gemini_api_key:
        return None

    prompt = f"""You are a neutral political media analyst. Read the news item and rate how it
affects each party's electoral standing.

Party A: {settings.party_a_name}
Party B: {settings.party_b_name}

Rules:
- A score of +1.0 means very favorable, -1.0 means very unfavorable, 0 means neutral.
- Judge only what the text says; do not assume facts beyond it.
- Reasons must be one short sentence grounded in the text.
- Return strict JSON only, with exactly these keys:
  {{"party_a_sentiment": number, "party_a_reason": string,
    "party_b_sentiment": number, "party_b_reason": string}}

Headline: {title}
Summary: {summary[:3000]}
"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    try:
        response = httpx.post(url, json=payload, timeout=settings.llm_timeout)
        response.raise_for_status()
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
        parsed = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - any API/parse error falls back to lexicon
        log.warning("Gemini sentiment failed, using lexicon fallback: %s", exc)
        return None

    score_a = _clamp_sentiment(parsed.get("party_a_sentiment"))
    score_b = _clamp_sentiment(parsed.get("party_b_sentiment"))
    if score_a is None or score_b is None:
        log.warning("Gemini returned invalid sentiment payload; using lexicon fallback")
        return None

    reason_a = str(parsed.get("party_a_reason") or "").strip()[:240] or None
    reason_b = str(parsed.get("party_b_reason") or "").strip()[:240] or None
    full_text = f"{title} {summary}"
    return {
        "A": {
            "score": score_a,
            "reason": reason_a,
            "method": "llm:gemini",
            "mentioned": _mentions_any(full_text, settings.party_a_aliases),
        },
        "B": {
            "score": score_b,
            "reason": reason_b,
            "method": "llm:gemini",
            "mentioned": _mentions_any(full_text, settings.party_b_aliases),
        },
    }


def _mentions_any(text: str, aliases: tuple[str, ...]) -> bool:
    parts = [re.escape(alias) for alias in sorted(aliases, key=len, reverse=True)]
    pattern = r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)"
    return bool(re.search(pattern, text, re.IGNORECASE))


def analyze_article(title: str, summary: str, settings: Settings) -> dict[str, dict]:
    """Return {"A": {score, reason, method}, "B": {...}} for one article."""
    fallback = lexicon_analyze(title, summary, settings)
    text = f"{title} {summary}"
    mentions_any_party = _mentions_any(text, settings.party_a_aliases) or _mentions_any(
        text, settings.party_b_aliases
    )
    if settings.llm_provider == "gemini" and settings.gemini_api_key and mentions_any_party:
        llm_result = _gemini_analyze(title, summary, settings)
        if llm_result:
            return llm_result
    return fallback
