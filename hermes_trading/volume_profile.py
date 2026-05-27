from __future__ import annotations

"""
Volume Profile calculator.

Given a list of candles [{h, l, v}] and a number of price bins,
distributes each candle's volume uniformly across the price range [l, h],
then returns:
  - poc   : Point of Control (price level with highest volume)
  - vah   : Value Area High  (top of the 70% value area)
  - val   : Value Area Low   (bottom of the 70% value area)
  - hvn   : list of High Volume Nodes above current price (resistances)
  - lvn   : list of Low Volume Nodes (thin spots — price moves fast through these)
  - profile: full {price: volume} dict for inspection / logging
"""

from collections import defaultdict


def build(candles: list[dict], bins: int = 60) -> dict:
    """
    candles: list of {"h": float, "l": float, "v": float}
    bins:    number of price levels to bucket into (default 60)

    Returns dict with poc, vah, val, hvn, lvn, profile.
    Returns empty dict if candles is empty or all volumes are zero.
    """
    if not candles:
        return {}

    all_highs = [c["h"] for c in candles]
    all_lows  = [c["l"] for c in candles]
    price_min = min(all_lows)
    price_max = max(all_highs)

    if price_max <= price_min:
        return {}

    bin_size = (price_max - price_min) / bins

    # Accumulate volume per bin
    vol: dict[int, float] = defaultdict(float)
    for c in candles:
        h, l, v = c["h"], c["l"], c["v"]
        if v <= 0 or h <= l:
            continue
        # bins touched by this candle
        b_lo = int((l - price_min) / bin_size)
        b_hi = int((h - price_min) / bin_size)
        b_hi = min(b_hi, bins - 1)
        b_lo = max(b_lo, 0)
        n_bins = b_hi - b_lo + 1
        vol_per_bin = v / n_bins
        for b in range(b_lo, b_hi + 1):
            vol[b] += vol_per_bin

    if not vol:
        return {}

    total_vol = sum(vol.values())

    # POC = bin with max volume
    poc_bin = max(vol, key=lambda b: vol[b])
    poc_price = round(price_min + (poc_bin + 0.5) * bin_size, 2)

    # Value Area: expand from POC until 70% of total volume captured
    va_target = total_vol * 0.70
    va_bins = {poc_bin}
    va_vol = vol[poc_bin]
    remaining = sorted([b for b in vol if b != poc_bin], key=lambda b: vol[b], reverse=True)
    for b in remaining:
        if va_vol >= va_target:
            break
        va_bins.add(b)
        va_vol += vol[b]

    vah_bin = max(va_bins)
    val_bin = min(va_bins)
    vah = round(price_min + (vah_bin + 1) * bin_size, 2)
    val = round(price_min + val_bin * bin_size, 2)

    # HVN / LVN: bins above average volume = HVN, below = LVN
    avg_vol = total_vol / bins
    hvn_threshold = avg_vol * 1.5
    lvn_threshold = avg_vol * 0.4

    hvn_prices = sorted(
        [round(price_min + (b + 0.5) * bin_size, 2) for b, v in vol.items() if v >= hvn_threshold],
    )
    lvn_prices = sorted(
        [round(price_min + (b + 0.5) * bin_size, 2) for b, v in vol.items() if v <= lvn_threshold],
    )

    # Full profile as {price_level: volume} for logging
    profile = {
        round(price_min + (b + 0.5) * bin_size, 2): round(vol[b], 4)
        for b in sorted(vol)
    }

    return {
        "poc": poc_price,
        "vah": vah,
        "val": val,
        "hvn": hvn_prices,
        "lvn": lvn_prices,
        "profile": profile,
        "price_min": round(price_min, 2),
        "price_max": round(price_max, 2),
        "bin_size": round(bin_size, 2),
    }


def next_hvn_above(vp: dict, price: float) -> float | None:
    """Return the nearest HVN price level strictly above current price."""
    candidates = [p for p in vp.get("hvn", []) if p > price]
    return min(candidates) if candidates else None


def next_hvn_below(vp: dict, price: float) -> float | None:
    """Return the nearest HVN price level strictly below current price (target per short)."""
    candidates = [p for p in vp.get("hvn", []) if p < price]
    return max(candidates) if candidates else None


def nearest_lvn_above(vp: dict, price: float) -> float | None:
    """Return the nearest LVN price level strictly above current price (fast-move zone)."""
    candidates = [p for p in vp.get("lvn", []) if p > price]
    return min(candidates) if candidates else None
