from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from .adapters import macro as macro_adapter
from .adapters import news as news_adapter
from .adapters import onchain as onchain_adapter
from .adapters import price as price_adapter
from .bootstrap import state_dir
from . import volume_profile as vp_calc
from . import markov_regime as markov_mod
from . import notify

TICK_SECONDS = 60
MAX_CONSECUTIVE_FAILURES = 5
CIRCUIT_BREAK_SLEEP = 300  # 5 minutes


class SchemaError(RuntimeError):
    """Adapter returned an unexpected schema_version."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_strategy(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def _compute_ema(prices: list[float], period: int) -> list[float]:
    """EMA series. Returns empty list if not enough data."""
    if len(prices) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for p in prices[period:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema


def _ema_cross_signal(closes: list[float], fast: int, slow: int) -> str | None:
    """
    Returns 'long' on golden cross (fast EMA crossed above slow EMA this tick).
    Returns None otherwise. Needs at least slow+2 price points.
    """
    if len(closes) < slow + 2:
        return None
    ema_fast = _compute_ema(closes, fast)
    ema_slow = _compute_ema(closes, slow)
    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return None
    # Last element of both EMAs corresponds to closes[-1]
    fast_now, fast_prev = ema_fast[-1], ema_fast[-2]
    slow_now, slow_prev = ema_slow[-1], ema_slow[-2]
    if fast_prev <= slow_prev and fast_now > slow_now:
        return "long"
    return None


# ---------------------------------------------------------------------------
# Retry / schema
# ---------------------------------------------------------------------------

async def _with_retry(name: str, fn: Callable[[], Awaitable[dict]]) -> dict:
    """3 attempts, exponential backoff 1s/2s/4s."""
    backoffs = [1, 2, 4]
    last_err: Exception | None = None
    for attempt, delay in enumerate(backoffs, start=1):
        try:
            return await fn()
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < len(backoffs):
                await asyncio.sleep(delay)
    raise RuntimeError(f"adapter '{name}' failed after retries: {last_err}")


def _validate_schema(name: str, payload: dict) -> dict:
    v = payload.get("schema_version")
    if v != 1:
        raise SchemaError(f"{name} returned schema_version={v}, expected 1")
    return payload


# ---------------------------------------------------------------------------
# Position I/O
# ---------------------------------------------------------------------------

def _open_position(state: Path, asset: str, price: float, strategy: dict) -> dict:
    pos = {
        "id": str(uuid.uuid4()),
        "asset": asset,
        "side": strategy["entry"]["direction"],
        "entry": price,
        "entry_time": _now_iso(),
        # strategy params (snapshot at open)
        "stop_loss_pct":          float(strategy["stop_loss_pct"]),
        "trailing_stop_pct":      float(strategy["trailing_stop_pct"]),
        "trailing_activate_pct":  float(strategy["trailing_activate_pct"]),
        "partial_exit_pct":       float(strategy["partial_exit_pct"]),
        "trailing_stop_tight_pct": float(strategy["trailing_stop_tight_pct"]),
        "position_size_r":        float(strategy.get("position_size_r", 0.5)),
        "strategy_version":       strategy.get("version", "??"),
        # runtime state
        "highest_price":    price,
        "trailing_active":  False,
        "trailing_price":   None,
        "half_closed":      False,
        "remaining_size":   1.0,
    }
    (state / "position.json").write_text(json.dumps(pos, indent=2))
    return pos


def _save_position(state: Path, pos: dict) -> None:
    (state / "position.json").write_text(json.dumps(pos, indent=2))


def _log_trade(state: Path, trade: dict) -> None:
    with (state / "trades.jsonl").open("a") as f:
        f.write(json.dumps(trade) + "\n")


def _build_trade_record(pos: dict, exit_price: float, reason: str, size: float) -> dict:
    """Build a trade record without touching position.json."""
    skip = {"highest_price", "trailing_active", "trailing_price", "half_closed", "remaining_size"}
    direction = pos["side"]
    if direction == "long":
        pnl_pct = (exit_price - pos["entry"]) / pos["entry"]
    else:
        pnl_pct = (pos["entry"] - exit_price) / pos["entry"]
    return {
        **{k: v for k, v in pos.items() if k not in skip},
        "exit":      exit_price,
        "exit_time": _now_iso(),
        "pnl_pct":   round(pnl_pct, 6),
        "size":      size,
        "reason":    reason,
    }


def _full_close(state: Path, pos: dict, exit_price: float, reason: str) -> dict:
    """Log trade and remove position.json."""
    size = float(pos.get("remaining_size", 1.0))
    trade = _build_trade_record(pos, exit_price, reason, size)
    _log_trade(state, trade)
    (state / "position.json").unlink(missing_ok=True)
    return trade


def _partial_close(state: Path, pos: dict, exit_price: float, reason: str) -> dict:
    """Log a 0.5-size trade; position.json is updated by caller."""
    trade = _build_trade_record(pos, exit_price, reason, 0.5)
    _log_trade(state, trade)
    return trade


def _heartbeat(state: Path, trades_total: int, position_open: bool, err: str | None, consec_fail: int) -> None:
    (state / "heartbeat.json").write_text(
        json.dumps(
            {
                "last_tick":             _now_iso(),
                "trades_total":          trades_total,
                "trades_open":           1 if position_open else 0,
                "last_error":            err,
                "consecutive_failures":  consec_fail,
            },
            indent=2,
        )
    )


def _count_trades(state: Path) -> int:
    tf = state / "trades.jsonl"
    if not tf.exists():
        return 0
    return sum(1 for _ in tf.open())


def _load_position(state: Path) -> dict | None:
    pf = state / "position.json"
    if not pf.exists():
        return None
    return json.loads(pf.read_text())


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_loop(asset: str) -> None:
    state = state_dir()
    closes: list[float] = []
    consec_fail = 0

    while True:
        tick_started = time.time()
        try:
            strategy = _read_strategy(state / "strategy.yaml")
            fast = int(strategy["entry"].get("ema_fast", 20))
            slow = int(strategy["entry"].get("ema_slow", 50))

            # --- fetch all adapters ---
            results: dict = {}
            for name, adapter, args in [
                ("price",   price_adapter,   (asset,)),
                ("onchain", onchain_adapter, (asset,)),
                ("news",    news_adapter,    ()),
                ("macro",   macro_adapter,   ()),
            ]:
                try:
                    payload = await _with_retry(name, lambda a=adapter, ar=args: a.fetch(*ar))
                    results[name] = _validate_schema(name, payload)
                except SchemaError:
                    raise
                except Exception as e:  # noqa: BLE001
                    print(f"adapter {name} failed: {e}", flush=True)
                    results[name] = None

            price_data = results.get("price")
            if not price_data:
                consec_fail += 1
                _heartbeat(state, _count_trades(state), _load_position(state) is not None,
                           "price adapter unavailable", consec_fail)
                if consec_fail >= MAX_CONSECUTIVE_FAILURES:
                    print(f"FATAL: {consec_fail} consecutive failures, sleeping {CIRCUIT_BREAK_SLEEP}s", flush=True)
                    await asyncio.sleep(CIRCUIT_BREAK_SLEEP)
                    consec_fail = 0
                else:
                    await asyncio.sleep(TICK_SECONDS)
                continue

            consec_fail = 0
            current_price = float(price_data["price"])

            recent = price_data.get("recent_closes") or []
            if recent and len(recent) > len(closes):
                # Seed / expand from historical candles, don't shrink what we have
                closes = recent
            closes.append(current_price)
            closes = closes[-500:]

            # --- Volume Profile (built from last 200 1m candles) ---
            candles = price_data.get("candles") or []
            vp = vp_calc.build(candles) if candles else {}
            poc = vp.get("poc")

            # --- Markov daily regime (cached, refresh ogni 60 min) ---
            regime = await markov_mod.get_regime(asset, state)

            position = _load_position(state)

            # ---------------------------------------------------------------
            # ENTRY — EMA golden cross + VP filter
            # ---------------------------------------------------------------
            if position is None:
                signal = _ema_cross_signal(closes, fast, slow)
                if signal == "long":
                    # Filtro 1 — VP: entra solo se price > POC (forza confermata)
                    if poc and current_price < poc:
                        print(
                            f"ENTRY BLOCKED  price={current_price:.0f} < POC={poc:.0f}  "
                            f"(VP: zona debole)",
                            flush=True,
                        )
                    # Filtro 2 — Markov: no entry in regime Bear
                    elif regime.get("label") == "Bear":
                        sig_str = f"{regime.get('signal', 0):+.3f}"
                        print(
                            f"ENTRY BLOCKED  Markov=Bear  signal={sig_str}  "
                            f"(trend daily negativo — attendiamo)",
                            flush=True,
                        )
                    else:
                        pos = _open_position(state, asset, current_price, strategy)
                        pos["poc_at_entry"] = poc  # snapshot VP al momento dell'apertura
                        pos["vah_at_entry"] = vp.get("vah")
                        pos["val_at_entry"] = vp.get("val")
                        _save_position(state, pos)
                        hard_stop = current_price * (1 - pos["stop_loss_pct"] / 100)
                        poc_str = f"  POC={poc:.0f}" if poc else ""
                        msg = (
                            f"OPEN long {asset} @ {current_price:.2f}  "
                            f"SL={hard_stop:.2f} (-{pos['stop_loss_pct']}%)  "
                            f"EMA{fast}xEMA{slow}{poc_str}  v{pos['strategy_version']}"
                        )
                        print(msg, flush=True)
                        asyncio.ensure_future(notify.send(f"📈 {msg}"))
                        position = pos

            # ---------------------------------------------------------------
            # POSITION MANAGEMENT
            # ---------------------------------------------------------------
            if position is not None:
                entry   = float(position["entry"])
                highest = float(position["highest_price"])

                sl_pct               = float(position["stop_loss_pct"])
                trailing_pct         = float(position["trailing_stop_pct"])
                trailing_activate_pct = float(position["trailing_activate_pct"])
                partial_exit_pct     = float(position["partial_exit_pct"])
                tight_pct            = float(position["trailing_stop_tight_pct"])

                gain_pct = (current_price - entry) / entry * 100  # long only

                # 1. Update highest price
                if current_price > highest:
                    position["highest_price"] = current_price
                    highest = current_price

                # 2. Hard stop loss (always active, has priority)
                hard_stop = entry * (1 - sl_pct / 100)
                if current_price <= hard_stop:
                    trade = _full_close(state, position, current_price, "stop_loss")
                    msg = (
                        f"CLOSE stop_loss @ {current_price:.2f}  "
                        f"pnl={trade['pnl_pct']:.4f}  size={trade['size']}"
                    )
                    print(msg, flush=True)
                    asyncio.ensure_future(notify.send(f"🛑 {msg}"))
                    position = None

                # 3. Activate trailing stop (once gain >= trailing_activate_pct)
                if position is not None and not position.get("trailing_active") and gain_pct >= trailing_activate_pct:
                    t_pct = tight_pct if position.get("half_closed") else trailing_pct
                    position["trailing_active"] = True
                    position["trailing_price"]  = highest * (1 - t_pct / 100)
                    print(
                        f"TRAILING STOP activated  trail={position['trailing_price']:.2f}  "
                        f"gain={gain_pct:.2f}%",
                        flush=True,
                    )
                    _save_position(state, position)

                # 4. Update trailing stop upward (ratchet)
                if position is not None and position.get("trailing_active"):
                    t_pct = tight_pct if position.get("half_closed") else trailing_pct
                    new_trail = highest * (1 - t_pct / 100)
                    if new_trail > (position.get("trailing_price") or 0):
                        position["trailing_price"] = new_trail
                    _save_position(state, position)

                # 5. Partial exit — dinamico su HVN o fallback a % fissa
                if position is not None and not position.get("half_closed"):
                    # Calcola soglia partial: usa HVN sopra l'entry se disponibile,
                    # altrimenti cade sul partial_exit_pct della strategy
                    hvn_target = vp_calc.next_hvn_above(vp, entry) if vp else None
                    hvn_gain_pct = None
                    if hvn_target:
                        # Converti HVN in % di gain dall'entry
                        hvn_gain_pct = (hvn_target - entry) / entry * 100
                        # Usa HVN solo se è raggiungibile (tra 1% e 10% dall'entry)
                        use_hvn = 1.0 <= hvn_gain_pct <= 10.0
                    else:
                        use_hvn = False

                    partial_trigger = hvn_gain_pct if use_hvn else partial_exit_pct

                    if partial_trigger is not None and gain_pct >= partial_trigger:
                        reason = f"partial_tp_hvn_{hvn_target:.0f}" if use_hvn else "partial_take_profit_50pct"
                        trade = _partial_close(state, position, current_price, reason)
                        position["half_closed"]    = True
                        position["remaining_size"] = 0.5
                        position["trailing_active"] = True
                        position["trailing_price"]  = highest * (1 - tight_pct / 100)
                        _save_position(state, position)
                        hvn_str = f"  HVN={hvn_target:.0f}" if use_hvn else ""
                        msg = (
                            f"PARTIAL CLOSE 50% @ {current_price:.2f}  "
                            f"pnl={trade['pnl_pct']:.4f}  gain={gain_pct:.2f}%{hvn_str}  "
                            f"-> trail tightened to {position['trailing_price']:.2f} (-{tight_pct}%)"
                        )
                        print(msg, flush=True)
                        asyncio.ensure_future(notify.send(f"✂️ {msg}"))

                # 6. Trailing stop hit
                if (
                    position is not None
                    and position.get("trailing_active")
                    and position.get("trailing_price") is not None
                    and current_price <= float(position["trailing_price"])
                ):
                    trade = _full_close(state, position, current_price, "trailing_stop")
                    msg = (
                        f"CLOSE trailing_stop @ {current_price:.2f}  "
                        f"pnl={trade['pnl_pct']:.4f}  size={trade['size']}"
                    )
                    print(msg, flush=True)
                    asyncio.ensure_future(notify.send(f"🔔 {msg}"))
                    position = None

            # --- heartbeat + tick log ---
            _heartbeat(state, _count_trades(state), position is not None, None, consec_fail)

            ema_fast_vals = _compute_ema(closes, fast)
            ema_slow_vals = _compute_ema(closes, slow)
            ema_info = ""
            if ema_fast_vals and ema_slow_vals:
                spread = ema_fast_vals[-1] - ema_slow_vals[-1]
                ema_info = f"  EMA{fast}={ema_fast_vals[-1]:.0f} EMA{slow}={ema_slow_vals[-1]:.0f} spread={spread:+.0f}"

            pos_info = ""
            if position is not None:
                gain = (current_price - float(position["entry"])) / float(position["entry"]) * 100
                trail_str = f"  trail={position['trailing_price']:.0f}" if position.get("trailing_price") else ""
                half_str  = " [50%left]" if position.get("half_closed") else ""
                trail_active = " T" if position.get("trailing_active") else ""
                pos_info = f"  |  POS gain={gain:+.2f}%{trail_active}{trail_str}{half_str}"

            warmup = f" (warmup {len(closes)}/{slow + 2})" if len(closes) < slow + 2 else ""
            vp_info = ""
            if vp:
                hvn_above = vp_calc.next_hvn_above(vp, current_price)
                vp_info = f"  POC={poc:.0f}" if poc else ""
                if hvn_above:
                    vp_info += f"  HVN>{hvn_above:.0f}"
            regime_label = regime.get("label", "?")
            regime_sig   = regime.get("signal", 0.0)
            fresh_mark   = "" if regime.get("fresh", True) else "~"
            regime_info  = f"  [{fresh_mark}{regime_label} {regime_sig:+.2f}]"
            print(f"tick price={current_price:.2f}{ema_info}{vp_info}{regime_info}{pos_info}{warmup}", flush=True)

        except SchemaError as e:
            print(f"FATAL SCHEMA ERROR: {e}", flush=True)
            raise
        except Exception as e:  # noqa: BLE001
            consec_fail += 1
            tb = traceback.format_exc()
            print(f"loop error: {e}\n{tb}", flush=True)
            _heartbeat(
                state, _count_trades(state),
                _load_position(state) is not None,
                str(e), consec_fail,
            )

        elapsed = time.time() - tick_started
        await asyncio.sleep(max(0.0, TICK_SECONDS - elapsed))
