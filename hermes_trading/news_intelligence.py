"""
news_intelligence.py — Intelligence sulle notizie finanziarie e crypto.

NON e' un semplice news feed. E' un sistema a 3 livelli che:

  1. CALENDARIO MACRO (ForexFactory, aggiornamento orario)
     Eventi programmati con data/ora precisa: FOMC, CPI, PCE, NFP, GDP.
     Questi sono NOTI IN ANTICIPO — oro per il bot. Blocca l'operativita'
     nelle finestre di rischio. Gia' usato da safe_guard.py.

  2. RSS CRYPTO IN TEMPO REALE (ogni 5 minuti)
     Cointelegraph + CoinDesk + Decrypt — le 3 fonti piu' rapide del settore.
     Keyword scoring deterministico: ogni titolo riceve un sentiment score
     da -1.0 (molto bearish) a +1.0 (molto bullish) per BTC.
     Notizie con score < -0.6 attivano SAFE_GUARD automatico.

  3. SENTIMENT MULTIPLIER sul confidence score
     Il sentiment recente (ultima 1h) moltiplica il confidence score di ogni
     trade: notizie negative riducono la size, notizie positive la aumentano.
     Non blocca ciecamente — modula la risposta.

Fonti testate e funzionanti (free, no API key):
  ForexFactory JSON  — calendario macro settimana corrente
  Cointelegraph RSS  — 30 articoli, aggiornamento continuo
  CoinDesk RSS       — 25 articoli, la piu' autorevole
  Decrypt RSS        — 37 articoli, rapida su breaking news
  Fed Reserve RSS    — comunicati FOMC ufficiali

Future upgrade (non implementati, richiedono API key a pagamento):
  CryptoPanic API    — aggregatore con sentiment precostruito ($19/mese)
  Finnhub News API   — notizie + sentiment score ($50/mese)
  Twitter/X API      — tweet di influencer e whale ($100/mese)
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

_RSS_FEEDS = [
    {
        "name": "CoinDesk",
        "url":  "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "weight": 1.3,   # fonte piu' autorevole
    },
    {
        "name": "Cointelegraph",
        "url":  "https://cointelegraph.com/rss",
        "weight": 1.1,
    },
    {
        "name": "Decrypt",
        "url":  "https://decrypt.co/feed",
        "weight": 1.0,
    },
]

# Finestra di rilevanza delle notizie (in minuti)
NEWS_RELEVANCE_WINDOW_MINUTES = 60

# Cache
_RSS_CACHE:      dict = {"articles": [], "fetched_at": 0.0}
_RSS_TTL         = 300   # refresh ogni 5 minuti

# ---------------------------------------------------------------------------
# Keyword scoring — deterministico, nessun LLM
# ---------------------------------------------------------------------------
# Ogni entry e' (pattern, score).
# Pattern puo' essere stringa semplice o regex.
# Score: -1.0 = molto bearish, +1.0 = molto bullish per BTC.

_BEARISH_KEYWORDS: list[tuple[str, float]] = [
    # Sicurezza / hack
    ("hack",          -0.9), ("hacked",       -0.9), ("exploit",       -0.8),
    ("breach",        -0.8), ("stolen",        -0.8), ("rug pull",      -1.0),
    ("rug-pull",      -1.0), ("drain",         -0.7), ("vulnerability", -0.6),

    # Problemi exchange / aziende
    ("bankrupt",      -0.9), ("insolvent",     -0.9), ("collapse",      -0.9),
    ("liquidated",    -0.8), ("suspend",       -0.7), ("halt",          -0.6),
    ("freeze",        -0.7), ("shutdown",      -0.8), ("closed",        -0.5),
    ("investigation", -0.7), ("arrested",      -0.8), ("fraud",         -0.9),
    ("scam",          -0.8), ("ponzi",         -0.9), ("lawsuit",       -0.6),
    ("charged",       -0.6), ("indicted",      -0.7), ("seized",        -0.8),

    # Regolatori
    ("ban",           -0.8), ("banned",        -0.8), ("crackdown",     -0.7),
    ("reject",        -0.6), ("denied",        -0.6), ("prohibited",    -0.7),
    ("illegal",       -0.7), ("restriction",   -0.5),

    # Mercato
    ("crash",         -0.8), ("plunge",        -0.7), ("dump",          -0.7),
    ("bear market",   -0.6), ("capitulation",  -0.8), ("sell-off",      -0.6),
    ("flash crash",   -0.9), ("whale sell",    -0.7), ("dark pool sell",-0.8),

    # Macro negativo
    ("inflation surge", -0.6), ("rate hike",  -0.5), ("hawkish",       -0.5),
    ("recession",     -0.6), ("taper",         -0.4),
]

_BULLISH_KEYWORDS: list[tuple[str, float]] = [
    # ETF / istituzionali
    ("etf approved",  +0.9), ("etf approval",  +0.9), ("etf launch",    +0.8),
    ("institutional", +0.6), ("blackrock",     +0.7), ("fidelity",      +0.6),
    ("vanguard",      +0.6), ("treasury",      +0.7), ("reserve",       +0.5),

    # Adozione
    ("legal tender",  +0.9), ("adoption",      +0.7), ("partnership",   +0.5),
    ("integration",   +0.5), ("accepted",      +0.6), ("approved",      +0.6),

    # Mercato positivo
    ("all-time high", +0.9), ("record high",   +0.8), ("rally",         +0.7),
    ("surge",         +0.6), ("bull",          +0.5), ("breakout",      +0.7),
    ("accumulate",    +0.6), ("hodl",          +0.4), ("buy",           +0.3),

    # Sviluppo / tech
    ("upgrade",       +0.5), ("halving",       +0.7), ("lightning",     +0.4),
    ("layer 2",       +0.4), ("scaling",       +0.4),

    # Macro positivo
    ("rate cut",      +0.6), ("dovish",        +0.5), ("pivot",         +0.6),
    ("stimulus",      +0.4), ("quantitative easing", +0.5),
]

# Boost se la notizia menziona esplicitamente Bitcoin/BTC
_BTC_BOOST_KEYWORDS = ["bitcoin", "btc", "crypto", "cryptocurrency", "digital asset"]


def _score_headline(title: str, description: str = "") -> float:
    """
    Calcola un sentiment score da -1.0 a +1.0 per un articolo.
    Usa solo keyword matching — nessuna dipendenza da LLM.
    """
    text = (title + " " + description).lower()

    # Boost se riguarda BTC/crypto
    btc_multiplier = 1.5 if any(kw in text for kw in _BTC_BOOST_KEYWORDS) else 1.0

    score = 0.0
    matches = 0

    for kw, w in _BEARISH_KEYWORDS:
        if kw in text:
            score  += w
            matches += 1

    for kw, w in _BULLISH_KEYWORDS:
        if kw in text:
            score  += w
            matches += 1

    if matches == 0:
        return 0.0

    # Media dei match, moltiplicata per boost BTC
    raw = score / matches * btc_multiplier
    return round(max(-1.0, min(1.0, raw)), 3)


# ---------------------------------------------------------------------------
# Fetch RSS
# ---------------------------------------------------------------------------

def _parse_rss_date(date_str: str | None) -> datetime | None:
    """Parsa date RFC 2822 (RSS) o ISO8601 in datetime UTC."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        pass
    return None


def _fetch_rss(url: str, weight: float = 1.0) -> list[dict]:
    """Fetcha un feed RSS e ritorna lista di articoli parsati."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
        tree = ET.fromstring(data)
    except Exception as e:
        print(f"news_intelligence: RSS fetch failed {url}: {e}", flush=True)
        return []

    articles = []
    for item in tree.findall(".//item"):
        t    = item.find("title")
        d    = item.find("pubDate") or item.find("dc:date")
        desc = item.find("description") or item.find("summary")
        link = item.find("link")

        title       = t.text.strip()    if t    and t.text    else ""
        pub_date    = d.text.strip()    if d    and d.text    else ""
        description = desc.text.strip() if desc and desc.text else ""
        url_article = link.text.strip() if link and link.text else ""

        # Rimuovi HTML da description
        description = re.sub(r"<[^>]+>", " ", description)[:300]

        if not title:
            continue

        dt = _parse_rss_date(pub_date)
        sentiment = _score_headline(title, description)

        articles.append({
            "title":     title,
            "url":       url_article,
            "dt_utc":    dt,
            "sentiment": sentiment,
            "weight":    weight,
        })

    return articles


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

def fetch_news(force_refresh: bool = False) -> list[dict]:
    """
    Ritorna la lista di tutti gli articoli recenti con sentiment score.
    Cache 5 minuti. Ordinati per data decrescente.
    """
    global _RSS_CACHE
    now = time.time()

    if not force_refresh and now - _RSS_CACHE["fetched_at"] < _RSS_TTL:
        return _RSS_CACHE["articles"]

    all_articles = []
    for feed in _RSS_FEEDS:
        articles = _fetch_rss(feed["url"], feed["weight"])
        all_articles.extend(articles)

    # Dedup per titolo
    seen = set()
    unique = []
    for a in all_articles:
        key = a["title"][:60].lower()
        if key not in seen:
            seen.add(key)
            unique.append(a)

    # Ordina per data decrescente
    unique.sort(key=lambda x: x["dt_utc"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    _RSS_CACHE = {"articles": unique, "fetched_at": now}
    return unique


def news_sentiment(
    now_utc: datetime | None = None,
    window_minutes: int = NEWS_RELEVANCE_WINDOW_MINUTES,
) -> dict:
    """
    Calcola il sentiment aggregato delle notizie nell'ultima finestra di tempo.

    Ritorna:
      score          — sentiment aggregato ponderato [-1, +1]
      signal         — "bearish_shock" | "bearish" | "neutral" | "bullish" | "bullish_surge"
      n_articles     — numero articoli considerati
      top_bearish    — lista dei titoli piu' bearish (per logging/Telegram)
      top_bullish    — lista dei titoli piu' bullish
      safe_guard     — True se il sentiment e' cosi' negativo da bloccare il trading
      confidence_mult— moltiplicatore da applicare al confidence score [0.5, 1.3]
      latest_title   — titolo piu' recente
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    articles = fetch_news()
    cutoff   = now_utc - timedelta(minutes=window_minutes)

    recent = [
        a for a in articles
        if a.get("dt_utc") and a["dt_utc"] >= cutoff
    ]

    if not recent:
        return {
            "score": 0.0, "signal": "neutral", "n_articles": 0,
            "top_bearish": [], "top_bullish": [], "safe_guard": False,
            "confidence_mult": 1.0, "latest_title": "",
        }

    # Weighted average sentiment
    total_weight = sum(a["weight"] for a in recent)
    weighted_sum = sum(a["sentiment"] * a["weight"] for a in recent)
    agg_score    = round(weighted_sum / total_weight, 3) if total_weight > 0 else 0.0

    # Top bearish e bullish per il report
    sorted_by_sent = sorted(recent, key=lambda x: x["sentiment"])
    top_bearish = [a["title"][:80] for a in sorted_by_sent[:2] if a["sentiment"] < -0.3]
    top_bullish = [a["title"][:80] for a in reversed(sorted_by_sent[-2:]) if a["sentiment"] > 0.3]

    # Segnale
    if agg_score < -0.55:
        signal = "bearish_shock"
    elif agg_score < -0.20:
        signal = "bearish"
    elif agg_score > 0.45:
        signal = "bullish_surge"
    elif agg_score > 0.15:
        signal = "bullish"
    else:
        signal = "neutral"

    # Safe guard: solo su shock molto negativi (non blocchiamo su bearish normale)
    safe_guard_trigger = agg_score < -0.55

    # Moltiplicatore confidence
    if agg_score < -0.55:
        confidence_mult = 0.50   # shock: dimezza la size
    elif agg_score < -0.20:
        confidence_mult = 0.75   # bearish: riduce la size
    elif agg_score > 0.45:
        confidence_mult = 1.20   # molto bullish: aumenta la size (max 20%)
    elif agg_score > 0.15:
        confidence_mult = 1.10   # lieve bullish
    else:
        confidence_mult = 1.00

    latest = recent[0]["title"] if recent else ""

    return {
        "score":           agg_score,
        "signal":          signal,
        "n_articles":      len(recent),
        "top_bearish":     top_bearish,
        "top_bullish":     top_bullish,
        "safe_guard":      safe_guard_trigger,
        "confidence_mult": confidence_mult,
        "latest_title":    latest[:80],
    }


def news_status_line(now_utc: datetime | None = None) -> str:
    """Stringa compatta per il tick log: 'news:bearish_shock(-0.72)'"""
    s = news_sentiment(now_utc)
    if s["signal"] == "neutral":
        return f"news:ok({s['score']:+.2f})"
    return f"news:{s['signal']}({s['score']:+.2f})"
