"""
kraken_source — downloader OHLCV da Kraken via ccxt con paginazione e rate limit.

Vedi: docs/superpowers/specs/2026-05-27-backtest-suite-design.md §9.
"""
from __future__ import annotations

import logging
import time

import ccxt

log = logging.getLogger(__name__)


_TIMEFRAME_TO_MS: dict[str, int] = {
    "1m":   60_000,
    "5m":   300_000,
    "15m":  900_000,
    "1h":   3_600_000,
    "4h":   14_400_000,
    "1d":   86_400_000,
}


def _normalize_symbol(symbol: str) -> str:
    """Converti BTCUSDT in BTC/USDT (canonico per ccxt)."""
    if "/" in symbol:
        return symbol
    # Heuristica semplice: split sulle quote più comuni
    for quote in ("USDT", "USDC", "USD", "EUR", "BTC", "ETH"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[: -len(quote)]
            return f"{base}/{quote}"
    return symbol


def _build_exchange() -> ccxt.Exchange:
    """Factory ccxt Kraken — separato per facilitare mocking nei test."""
    return ccxt.kraken({"enableRateLimit": True, "timeout": 30000})


def fetch_ohlcv_range(
    symbol:       str,
    timeframe:    str,
    since:        int,           # unix seconds UTC inclusivo
    until:        int,            # unix seconds UTC inclusivo
    sleep_seconds: float = 1.0,   # rate-limit Kraken (override a 0 nei test)
    page_limit:   int = 720,      # max candele per request (Kraken default)
) -> list[dict]:
    """
    Scarica OHLCV per il range [since, until].

    Itera con paginazione: ogni richiesta ritorna fino a page_limit candele.
    Aspetta sleep_seconds tra le request per rispettare il rate limit.
    Ritorna list[dict] con chiavi {t, o, h, l, c, v, n_trades}.
    """
    if timeframe not in _TIMEFRAME_TO_MS:
        raise ValueError(f"timeframe non supportato: {timeframe}")

    step_ms = _TIMEFRAME_TO_MS[timeframe]
    since_ms = since * 1000
    until_ms = until * 1000

    pair = _normalize_symbol(symbol)
    ex   = _build_exchange()

    out: list[dict] = []
    cursor_ms = since_ms

    while cursor_ms <= until_ms:
        try:
            batch = ex.fetch_ohlcv(pair, timeframe=timeframe,
                                   since=cursor_ms, limit=page_limit)
        except Exception as exc:
            log.warning("[kraken_source] fetch failed at %d: %s — retrying once", cursor_ms, exc)
            time.sleep(max(sleep_seconds, 2.0))
            batch = ex.fetch_ohlcv(pair, timeframe=timeframe,
                                   since=cursor_ms, limit=page_limit)

        if not batch:
            break

        for row in batch:
            t_ms = int(row[0])
            if t_ms > until_ms:
                continue
            out.append({
                "t":        t_ms // 1000,
                "o":        float(row[1]),
                "h":        float(row[2]),
                "l":        float(row[3]),
                "c":        float(row[4]),
                "v":        float(row[5]),
                "n_trades": 0,        # ccxt non lo espone uniformemente
            })

        last_ms = int(batch[-1][0])
        if last_ms <= cursor_ms:
            # Niente progresso → exit per evitare loop infinito
            break
        cursor_ms = last_ms + step_ms

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return out
