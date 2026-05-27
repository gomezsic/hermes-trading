"""
alert.py — Telegram alerts per eventi di trading.

Invia messaggi al bot Telegram quando:
- Si apre una posizione (OPEN long o short)
- Si chiude una posizione (stop_loss, trailing_stop, partial, take_profit)
- Trailing stop attivato
- Errore fatale del worker
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7014548056")

_API = "https://api.telegram.org/bot{token}/sendMessage"

REGIME_EMOJI = {"Bull": "🟢", "Sideways": "🟡", "Bear": "🔴"}
REASON_EMOJI = {
    "stop_loss":                 "🛑",
    "trailing_stop":             "📉",
    "partial_take_profit_50pct": "💰",
    "take_profit":               "✅",
}
SIDE_EMOJI = {"long": "📈", "short": "📉"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _pnl_summary(portfolio: dict) -> str:
    initial = portfolio.get("initial_capital", 100_000)
    balance = portfolio.get("balance", initial)
    peak    = portfolio.get("peak_balance", initial)
    pnl_pct = (balance - initial) / initial * 100
    dd_pct  = max(0.0, (peak - balance) / peak * 100) if peak > 0 else 0.0
    return (
        f"\n💼 <b>Balance:</b> ${balance:,.0f}  ({pnl_pct:+.2f}% totale)"
        f"\n📊 <b>Drawdown:</b> {dd_pct:.1f}%"
    )


async def send(text: str) -> None:
    """Invia un messaggio Telegram. Silenzioso su errore."""
    token = TELEGRAM_TOKEN
    if not token:
        env_path = os.path.expanduser("~/.hermes/.env")
        try:
            for line in open(env_path):
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.strip().split("=", 1)[1]
        except Exception:
            pass

    if not token:
        return

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                _API.format(token=token),
                json={
                    "chat_id":              TELEGRAM_CHAT_ID,
                    "text":                 text,
                    "parse_mode":           "HTML",
                    "disable_notification": False,
                },
            )
    except Exception:
        pass


async def trade_opened(
    pos:                  dict,
    vp:                   dict | None = None,
    regime:               dict | None = None,
    portfolio:            dict | None = None,
    confidence:           float = 0.0,
    confidence_breakdown: dict | None = None,
) -> None:
    entry     = float(pos.get("entry", 0))
    side      = pos.get("side", "long")
    asset     = pos.get("asset", "BTC/USDT")
    version   = pos.get("strategy_version", "?")
    size_r    = float(pos.get("position_size_r", 0.5))
    capital   = float(pos.get("capital_at_open", 100_000))
    invested  = capital * size_r

    poc       = vp.get("poc") if vp else None
    reg_label = regime.get("label", "—")   if regime else "—"
    reg_sig   = regime.get("signal", 0.0)  if regime else 0.0
    reg_emoji = REGIME_EMOJI.get(reg_label, "⚪")
    side_e    = SIDE_EMOJI.get(side, "🔄")

    if side == "long":
        sl = entry * (1 - float(pos.get("stop_loss_pct", 2.5)) / 100)
        sl_str = f"${sl:,.2f} (-{pos.get('stop_loss_pct', 2.5):.1f}%)"
    else:
        sl = entry * (1 + float(pos.get("stop_loss_pct", 2.5)) / 100)
        sl_str = f"${sl:,.2f} (+{pos.get('stop_loss_pct', 2.5):.1f}%)"

    poc_line = f"\n📊 <b>POC:</b> ${poc:,.0f}" if poc else ""
    regime_line = f"\n{reg_emoji} <b>Regime:</b> {reg_label} ({reg_sig:+.2f})"

    # Confidence detail
    conf_detail = ""
    if confidence_breakdown:
        b = confidence_breakdown
        conf_detail = (
            f"\n🧠 <b>Conf:</b> {confidence:.2f}  "
            f"[Markov {b.get('markov', 0):.2f} | EMA {b.get('ema_spread', 0):.2f} | "
            f"VP {b.get('vp_position', 0):.2f} | Mom {b.get('momentum', 0):.2f}]"
        )

    portfolio_line = _pnl_summary(portfolio) if portfolio else ""

    msg = (
        f"{side_e} <b>TRADE APERTO [{side.upper()}]</b> — {asset}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💵 <b>Entry:</b> ${entry:,.2f}\n"
        f"🛑 <b>Stop Loss:</b> {sl_str}\n"
        f"📈 <b>Trailing:</b> si attiva a +{pos.get('trailing_activate_pct', 2.5):.1f}%\n"
        f"💼 <b>Size:</b> {size_r:.0%} (${invested:,.0f} investiti)"
        f"{conf_detail}"
        f"{poc_line}"
        f"{regime_line}"
        f"{portfolio_line}\n"
        f"📋 <b>Strategy:</b> v{version}\n"
        f"🕐 {_now()}"
    )
    await send(msg)


async def trade_closed(
    trade:     dict,
    reason:    str,
    portfolio: dict | None = None,
) -> None:
    pnl_pct    = float(trade.get("pnl_pct", 0))
    pnl_dollar = float(trade.get("pnl_dollar", 0))
    entry      = float(trade.get("entry", 0))
    exit_p     = float(trade.get("exit", 0))
    asset      = trade.get("asset", "BTC/USDT")
    side       = trade.get("side", "long")
    size       = float(trade.get("size", 1.0))
    pnl_sign   = "🟢" if pnl_pct > 0 else "🔴"
    reason_e   = REASON_EMOJI.get(reason.split("_hvn_")[0], "📋")
    side_e     = SIDE_EMOJI.get(side, "🔄")

    extra = ""
    if "partial" in reason:
        extra = "\n⏩ <b>50% ancora aperto</b> — trailing attivo"

    portfolio_line = _pnl_summary(portfolio) if portfolio else ""

    msg = (
        f"{pnl_sign} {side_e} <b>TRADE {'PARZIALE' if 'partial' in reason else 'CHIUSO'} [{side.upper()}]</b> — {asset}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{reason_e} <b>Motivo:</b> {reason}\n"
        f"💵 <b>Entry:</b> ${entry:,.2f} → <b>Exit:</b> ${exit_p:,.2f}\n"
        f"{'📈' if pnl_pct > 0 else '📉'} <b>PnL:</b> {pnl_pct*100:+.2f}%  "
        f"<b>${pnl_dollar:+,.0f}</b>  (size {size:.0%})"
        f"{extra}"
        f"{portfolio_line}\n"
        f"🕐 {_now()}"
    )
    await send(msg)


async def trailing_activated(
    price:       float,
    trail_price: float,
    gain_pct:    float,
    side:        str = "long",
) -> None:
    side_e = SIDE_EMOJI.get(side, "🔄")
    msg = (
        f"🔒 <b>TRAILING STOP ATTIVATO [{side.upper()}]</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💵 <b>Prezzo:</b> ${price:,.2f}\n"
        f"🛡️ <b>Trail:</b> ${trail_price:,.2f}\n"
        f"{side_e} <b>Gain:</b> +{gain_pct:.2f}%\n"
        f"🕐 {_now()}"
    )
    await send(msg)


async def sizing_pause_event(decision: object, portfolio: dict | None = None) -> None:
    """Notifica Telegram quando il Kelly+vol sizing entra in PAUSE_SYSTEM."""
    reason   = getattr(decision, "reason", "edge_degradation")
    until    = getattr(decision, "pause_until", "")
    debug    = getattr(decision, "debug", {})
    portfolio_line = _pnl_summary(portfolio) if portfolio else ""

    msg = (
        f"⏸ <b>SIZING PAUSE_SYSTEM</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔴 <b>Motivo:</b> {reason}\n"
        f"📅 <b>Pausa fino a:</b> {until or '—'}\n"
        f"📊 <b>Debug:</b> {str(debug)[:200]}"
        f"{portfolio_line}\n"
        f"ℹ️ Il sistema riprende automaticamente alla scadenza.\n"
        f"🕐 {_now()}"
    )
    await send(msg)


async def news_guard_event(news: dict, portfolio: dict | None = None) -> None:
    """Notifica Telegram quando il news sentiment attiva il blocco."""
    score    = news.get("score", 0.0)
    signal   = news.get("signal", "")
    n        = news.get("n_articles", 0)
    bearish  = news.get("top_bearish", [])
    portfolio_line = _pnl_summary(portfolio) if portfolio else ""

    headlines = ""
    for h in bearish[:2]:
        headlines += f"\n  🔴 {h}"

    msg = (
        f"📰 <b>NEWS GUARD ATTIVO</b>  |  shock bearish\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📉 <b>Sentiment:</b> {score:+.3f}  [{signal}]\n"
        f"📊 <b>Articoli analizzati:</b> {n} (ultima 1h)\n"
        f"<b>Notizie negative principali:</b>{headlines}"
        f"{portfolio_line}\n"
        f"🚫 Nessun nuovo trade fino a normalizzazione sentiment\n"
        f"🕐 {_now()}"
    )
    await send(msg)


async def safe_guard_event(
    sg_result:  dict,
    trade:      dict | None = None,
    portfolio:  dict | None = None,
) -> None:
    """Notifica Telegram all'attivazione del SAFE & GUARD (o chiusura forzata)."""
    reason = sg_result.get("reason", "")
    details = sg_result.get("details", {})

    # Emoji per tipo di guard
    if "WEEKEND" in reason:      emoji = "🏖️"
    elif "NYSE"   in reason:     emoji = "🔔"
    elif "CALENDAR" in reason:   emoji = "📅"
    elif "SIDEWAY" in reason:    emoji = "😴"
    else:                        emoji = "🛡️"

    trade_line = ""
    if trade:
        pnl = trade.get("pnl_pct", 0) * 100
        pnl_d = trade.get("pnl_dollar", 0)
        side  = trade.get("side", "?")
        entry = trade.get("entry", 0)
        exit_ = trade.get("exit", 0)
        trade_line = (
            f"\n⚠️ <b>Posizione chiusa preventivamente</b>\n"
            f"   [{side.upper()}] ${entry:,.2f} → ${exit_:,.2f}  {pnl:+.2f}%  ${pnl_d:+,.0f}"
        )

    portfolio_line = _pnl_summary(portfolio) if portfolio else ""

    msg = (
        f"{emoji} <b>SAFE &amp; GUARD ATTIVO</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🚫 <b>Motivo:</b> {reason}"
        f"{trade_line}"
        f"{portfolio_line}\n"
        f"🕐 {_now()}"
    )
    await send(msg)


async def safe_guard_cleared(portfolio: dict | None = None) -> None:
    """Notifica Telegram quando il SAFE & GUARD si disattiva."""
    portfolio_line = _pnl_summary(portfolio) if portfolio else ""
    msg = (
        f"✅ <b>SAFE &amp; GUARD DISATTIVATO</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🟢 Operativita' normale ripresa — il bot puo' aprire nuovi trade."
        f"{portfolio_line}\n"
        f"🕐 {_now()}"
    )
    await send(msg)


async def worker_error(error: str, consecutive: int) -> None:
    if consecutive < 3:
        return
    msg = (
        f"⚠️ <b>WORKER ERROR</b> — hermes-trading\n"
        f"━━━━━━━━━━━━━━━\n"
        f"❌ <b>Errore:</b> {error[:200]}\n"
        f"🔁 <b>Tentativi consecutivi:</b> {consecutive}\n"
        f"🕐 {_now()}"
    )
    await send(msg)
