"""
safe_guard.py — Modalita' SAFE & GUARD

Determina quando il bot NON deve tradare. Un buon sistema sa soprattutto
quando stare fuori dal mercato.

Regole implementate:
  1. WEEKEND
     - Venerdi >= 21:00 UTC: chiudi posizioni aperte, niente nuovi trade
     - Sabato e Domenica: niente nuovi trade
     - La cripto non chiude ma la liquidita' crolla e i gap del lunedi
       possono essere violenti

  2. NYSE OPEN — primi 30 minuti
     - Aspetta i primi 30 min dall'apertura del Nasdaq/NYSE prima di entrare
     - L'apertura e' caotica: market maker aggiustano prezzi, ordini istituzionali
       arrivano tutti insieme, volatilita' altissima e direzione incerta
     - NYSE apre 9:30 ET = 13:30 UTC (DST) / 14:30 UTC (ora solare)
     - Blocca nuove entry dalle open fino a open+30min

  3. SIDEWAY FILTER (ADX)
     - Se ADX < adx_min_threshold (default 20): mercato laterale, niente entry
     - In laterale i cross EMA sono falsi segnali in continuazione
     - Il bot aspetta che si formi un trend reale

  4. CALENDARIO ECONOMICO (ForexFactory feed)
     - Fetch settimanale degli eventi HIGH impact USD
     - Blocca nuove entry nelle 2h precedenti e 1h successive all'evento
     - Chiudi posizioni aperte 30min prima dell'evento (configurabile)
     - Eventi: FOMC, CPI, PCE, NFP, GDP, Fed Chair speech

Tutti i parametri sono configurabili in strategy.yaml sotto chiave safe_guard.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# DST helper
# ---------------------------------------------------------------------------

def _is_us_dst(dt: datetime) -> bool:
    """True se gli USA sono in ora legale (DST)."""
    year = dt.year
    # Seconda domenica di marzo ore 2:00
    d = datetime(year, 3, 8, 2, tzinfo=timezone.utc)
    dst_start = d + timedelta(days=(6 - d.weekday()) % 7)
    # Prima domenica di novembre ore 2:00
    d = datetime(year, 11, 1, 2, tzinfo=timezone.utc)
    dst_end = d + timedelta(days=(6 - d.weekday()) % 7)
    return dst_start <= dt < dst_end


def _nyse_open_utc(dt: datetime) -> tuple[int, int]:
    """Ritorna (hour, minute) UTC dell'apertura NYSE per il giorno dato."""
    if _is_us_dst(dt):
        return (13, 30)   # 9:30 ET = 13:30 UTC in DST
    return (14, 30)       # 9:30 ET = 14:30 UTC in ora solare


# ---------------------------------------------------------------------------
# Economic calendar (ForexFactory feed pubblico)
# ---------------------------------------------------------------------------

_CALENDAR_CACHE: dict = {"events": [], "fetched_at": 0.0, "week": ""}
_CALENDAR_TTL   = 3600   # refresh ogni ora
_FF_URL         = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Parole chiave degli eventi HIGH impact che attivano SAFE & GUARD
_HIGH_IMPACT_KEYWORDS = [
    "fomc", "federal reserve", "fed chair", "powell",
    "interest rate", "rate decision",
    "cpi", "core cpi", "ppi", "core pce", "pce",
    "nonfarm", "non-farm", "nfp", "payroll",
    "gdp", "gross domestic",
    "unemployment rate", "jobless",
    "retail sales",
]


def _fetch_calendar() -> list[dict]:
    """
    Fetcha il calendario economico settimanale da ForexFactory.
    Ritorna lista di eventi HIGH impact USD con datetime UTC.
    Cache in-memory per 1 ora.
    """
    global _CALENDAR_CACHE
    now = time.time()
    week_key = datetime.now(timezone.utc).strftime("%Y-W%W")

    if (now - _CALENDAR_CACHE["fetched_at"] < _CALENDAR_TTL
            and _CALENDAR_CACHE["week"] == week_key):
        return _CALENDAR_CACHE["events"]

    try:
        req = urllib.request.Request(
            _FF_URL,
            headers={"User-Agent": "hermes-trading/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read())
    except Exception as e:
        print(f"safe_guard: calendar fetch failed: {e}", flush=True)
        _CALENDAR_CACHE["fetched_at"] = now   # evita retry continui
        return _CALENDAR_CACHE["events"]       # usa cache precedente

    events = []
    for ev in raw:
        if ev.get("country") != "USD":
            continue
        impact = ev.get("impact", "").lower()
        if impact not in ("high", "holiday"):
            continue
        title = ev.get("title", "").lower()
        if not any(kw in title for kw in _HIGH_IMPACT_KEYWORDS):
            continue
        # Parsa la data (formato: "2026-05-28T08:30:00-04:00")
        date_str = ev.get("date", "")
        try:
            # Python 3.7+ supporta offset-aware ISO format
            dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
        except Exception:
            continue
        events.append({
            "title":  ev.get("title", ""),
            "dt_utc": dt,
        })

    _CALENDAR_CACHE = {"events": events, "fetched_at": now, "week": week_key}
    print(
        f"safe_guard: calendar loaded — {len(events)} HIGH impact USD events this week",
        flush=True,
    )
    return events


def _nearest_calendar_event(now_utc: datetime) -> tuple[dict | None, float]:
    """
    Ritorna (evento_piu_vicino, minuti_di_distanza).
    minuti_di_distanza e' negativo se siamo PRIMA dell'evento, positivo se DOPO.
    """
    events = _fetch_calendar()
    if not events:
        return None, float("inf")
    closest = min(events, key=lambda e: abs((e["dt_utc"] - now_utc).total_seconds()))
    delta_min = (closest["dt_utc"] - now_utc).total_seconds() / 60
    return closest, delta_min


# ---------------------------------------------------------------------------
# Funzione principale
# ---------------------------------------------------------------------------

SafeGuardResult = dict   # {blocked: bool, reason: str, close_position: bool, details: dict}


def check(
    now_utc:  datetime | None = None,
    candles:  list[dict] | None = None,   # per ADX
    cfg:      dict | None = None,          # safe_guard block da strategy.yaml
) -> SafeGuardResult:
    """
    Controlla tutte le regole SAFE & GUARD.

    Ritorna un dizionario con:
      blocked:         True = non aprire nuovi trade
      close_position:  True = chiudi posizioni aperte ADESSO
      reason:          stringa descrittiva
      details:         dict con info per logging/Telegram
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    cfg = cfg or {}
    result: SafeGuardResult = {
        "blocked":        False,
        "close_position": False,
        "reason":         "",
        "details":        {},
    }

    weekday   = now_utc.weekday()  # 0=Lun, 4=Ven, 5=Sab, 6=Dom
    hour_utc  = now_utc.hour
    minute_utc= now_utc.minute

    # ------------------------------------------------------------------
    # 1. WEEKEND
    # ------------------------------------------------------------------
    weekend_enabled = cfg.get("weekend_guard", True)
    fri_close_hour  = int(cfg.get("friday_close_hour_utc",  21))
    fri_close_min   = int(cfg.get("friday_close_minute_utc", 0))

    if weekend_enabled:
        # Sabato e Domenica: niente trade
        if weekday in (5, 6):
            result["blocked"] = True
            result["reason"]  = f"WEEKEND ({'Sab' if weekday==5 else 'Dom'}) — nessun trade"
            result["details"]["weekend"] = True
            return result

        # Venerdi sera: chiudi posizioni e blocca
        if weekday == 4:
            fri_close_mins = fri_close_hour * 60 + fri_close_min
            now_mins       = hour_utc * 60 + minute_utc
            if now_mins >= fri_close_mins:
                result["blocked"]        = True
                result["close_position"] = True
                result["reason"] = (
                    f"WEEKEND GUARD — Ven {hour_utc:02d}:{minute_utc:02d} UTC >= "
                    f"soglia {fri_close_hour:02d}:{fri_close_min:02d} UTC  "
                    f"(chiudi posizioni, mercato illiquido fino a lun)"
                )
                result["details"]["weekend_close"] = True
                return result

    # ------------------------------------------------------------------
    # 2. NYSE OPEN — primi 30 minuti
    # ------------------------------------------------------------------
    nyse_guard_enabled = cfg.get("nyse_open_guard", True)
    nyse_wait_minutes  = int(cfg.get("nyse_open_wait_minutes", 30))

    if nyse_guard_enabled and weekday < 5:   # solo giorni feriali
        open_h, open_m = _nyse_open_utc(now_utc)
        open_total  = open_h * 60 + open_m
        now_total   = hour_utc * 60 + minute_utc
        guard_end   = open_total + nyse_wait_minutes

        if open_total <= now_total < guard_end:
            remain = guard_end - now_total
            result["blocked"] = True
            result["reason"]  = (
                f"NYSE OPEN GUARD — mercato aperto da {now_total - open_total}min, "
                f"aspettiamo altri {remain}min  "
                f"(no trade nei primi {nyse_wait_minutes}min dall'apertura)"
            )
            result["details"]["nyse_open"] = {
                "open_utc": f"{open_h:02d}:{open_m:02d}",
                "minutes_since_open": now_total - open_total,
                "guard_end_utc": f"{guard_end//60:02d}:{guard_end%60:02d}",
            }
            return result

    # ------------------------------------------------------------------
    # 3. CALENDARIO ECONOMICO
    # ------------------------------------------------------------------
    calendar_enabled = cfg.get("calendar_guard", True)
    pre_event_mins   = int(cfg.get("pre_event_guard_minutes",   120))
    post_event_mins  = int(cfg.get("post_event_guard_minutes",   60))
    close_before_mins= int(cfg.get("close_position_minutes",     30))

    if calendar_enabled:
        event, delta_min = _nearest_calendar_event(now_utc)
        if event is not None:
            # Entro la finestra di guardia?
            if -pre_event_mins <= delta_min <= post_event_mins:
                result["blocked"] = True
                if -close_before_mins <= delta_min <= 0:
                    result["close_position"] = True
                    timing = f"tra {abs(delta_min):.0f}min"
                    result["reason"] = (
                        f"CALENDAR GUARD — '{event['title']}' {timing}  "
                        f"(chiudi posizioni {close_before_mins}min prima)"
                    )
                elif delta_min < 0:
                    timing = f"tra {abs(delta_min):.0f}min"
                    result["reason"] = (
                        f"CALENDAR GUARD — '{event['title']}' {timing}  "
                        f"(no nuove entry nelle {pre_event_mins}h pre-evento)"
                    )
                else:
                    timing = f"{delta_min:.0f}min fa"
                    result["reason"] = (
                        f"CALENDAR GUARD — '{event['title']}' {timing}  "
                        f"(attesa {post_event_mins}min post-evento per stabilizzazione)"
                    )
                result["details"]["calendar_event"] = {
                    "title":     event["title"],
                    "dt_utc":    event["dt_utc"].strftime("%Y-%m-%d %H:%M UTC"),
                    "delta_min": round(delta_min, 1),
                }
                return result

    # ------------------------------------------------------------------
    # 4. ADX SIDEWAY FILTER
    # ------------------------------------------------------------------
    adx_guard_enabled = cfg.get("adx_guard", True)
    adx_min           = float(cfg.get("adx_min_threshold", 20.0))

    if adx_guard_enabled and candles:
        try:
            from . import indicators as ind
            adx_data = ind.compute_adx(candles, period=14)
            if adx_data is not None and adx_data["adx"] < adx_min:
                result["blocked"] = True
                result["reason"]  = (
                    f"SIDEWAY GUARD — ADX={adx_data['adx']:.1f} < {adx_min}  "
                    f"(mercato laterale, cross EMA non affidabile)"
                )
                result["details"]["adx"] = {
                    "adx":      adx_data["adx"],
                    "plus_di":  adx_data["plus_di"],
                    "minus_di": adx_data["minus_di"],
                    "strength": adx_data["trend_strength"],
                }
                return result
        except Exception:
            pass   # ADX non disponibile: non bloccare

    # Tutto ok
    result["reason"] = "OK"
    return result


# ---------------------------------------------------------------------------
# Descrizione human-readable dello stato corrente
# ---------------------------------------------------------------------------

def status_line(now_utc: datetime | None = None, cfg: dict | None = None) -> str:
    """
    Ritorna una stringa compatta per il tick log:
    'SAFE_GUARD:WEEKEND' oppure 'SAFE_GUARD:OK'
    """
    r = check(now_utc=now_utc, cfg=cfg)
    if r["blocked"]:
        tag = r["reason"].split("—")[0].strip().replace(" ", "_")
        return f"[{tag}]"
    return ""
