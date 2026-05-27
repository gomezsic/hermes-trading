from __future__ import annotations

from datetime import datetime, timezone

import ccxt.async_support as ccxt

# Kraken: works globally, no geo-block on Binance's restricted regions.
_EXCHANGE: ccxt.kraken | None = None


def _exchange() -> ccxt.kraken:
    global _EXCHANGE
    if _EXCHANGE is None:
        _EXCHANGE = ccxt.kraken({"enableRateLimit": True})
    return _EXCHANGE


async def fetch(asset: str) -> dict:
    """
    Fetch ticker + OHLCV multi-timeframe da Kraken.

    Ritorna:
      candles_1m   — 500 candele 1m con {h, l, c, v} — close REALE, candele vuote filtrate
      candles_15m  — 200 candele 15m — per Volume Profile intraday (24h) e VWMA
      candles_1h   — 168 candele 1h  — per Volume Profile swing (1 settimana) e ADX
      recent_closes — ultimi 500 close 1m per EMA

    FIX rispetto alla versione precedente:
      1. "c" (close reale) ora presente — non piu' approssimato con (h+l)/2
      2. Candele con n_trades=0 filtrate — riduceva ATR del 10%, inquinava ADX
      3. Multi-timeframe — VP e ADX ora hanno dati adeguati alla loro natura
      4. vwap_kraken conservato — il VWAP precalcolato da Kraken per candela
    """
    ex = _exchange()
    ticker = await ex.fetch_ticker(asset)

    def _parse_ohlcv(rows: list, min_trades: int = 1) -> list[dict]:
        """
        Converte le righe OHLCV Kraken in candles dict.
        Formato Kraken: [timestamp, open, high, low, close, vwap, volume, n_trades]
        Filtra candele con n_trades < min_trades (candele fantasma senza scambi reali).
        """
        out = []
        for row in rows:
            n_trades = int(row[7]) if len(row) > 7 else 0
            if n_trades < min_trades:
                continue
            out.append({
                "h":            float(row[2]),
                "l":            float(row[3]),
                "c":            float(row[4]),   # close REALE (non piu' (h+l)/2)
                "v":            float(row[6]),   # volume in BTC (base currency)
                "vwap_kraken":  float(row[5]) if (len(row) > 5 and row[5] and float(row[5]) > 0) else None,
                "n_trades":     n_trades,
            })
        return out

    # --- 1m: 500 candele per EMA e VWAP intraday (~8 ore) ---
    try:
        ohlcv_1m = await ex.fetch_ohlcv(asset, timeframe="1m", limit=500)
        candles_1m    = _parse_ohlcv(ohlcv_1m, min_trades=1)
        recent_closes = [float(row[4]) for row in ohlcv_1m]
    except Exception:
        candles_1m    = []
        recent_closes = []

    # --- 15m: 200 candele per Volume Profile intraday (50 ore) e VWMA ---
    try:
        ohlcv_15m  = await ex.fetch_ohlcv(asset, timeframe="15m", limit=200)
        candles_15m = _parse_ohlcv(ohlcv_15m, min_trades=1)
    except Exception:
        candles_15m = []

    # --- 1h: 168 candele per Volume Profile swing (1 settimana) e ADX ---
    try:
        ohlcv_1h  = await ex.fetch_ohlcv(asset, timeframe="1h", limit=168)
        candles_1h = _parse_ohlcv(ohlcv_1h, min_trades=1)
    except Exception:
        candles_1h = []

    # --- 1d: 30 candele giornaliere per ATR daily e sigma storica ---
    try:
        ohlcv_1d  = await ex.fetch_ohlcv(asset, timeframe="1d", limit=30)
        candles_1d = _parse_ohlcv(ohlcv_1d, min_trades=1)
    except Exception:
        candles_1d = []

    return {
        "schema_version": 1,
        "asset":          asset,
        "price":          float(ticker["last"]),
        "volume_24h":     float(ticker.get("quoteVolume") or ticker.get("baseVolume") or 0.0),
        "timestamp":      datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        # EMA e VWAP intraday
        "recent_closes":  recent_closes,
        "candles":        candles_1m,
        "candles_15m":    candles_15m,
        "candles_1h":     candles_1h,
        "candles_1d":     candles_1d,   # per ATR daily e sigma
    }
