import json
import subprocess
import yaml
import datetime
from pathlib import Path

BASE = Path.home() / "hermes-trading"
STATE = BASE / "state"
OUT = BASE / "dashboard.html"


# ---------------------------------------------------------------------------
# Sync from Railway
# ---------------------------------------------------------------------------

def _sync_from_railway():
    STATE.mkdir(parents=True, exist_ok=True)

    files_to_sync = [
        ("cat /app/state/heartbeat.json", STATE / "heartbeat.json"),
        ("cat /app/state/strategy.yaml", STATE / "strategy.yaml"),
        ("cat /app/state/trades.jsonl", STATE / "trades.jsonl"),
        ("cat /app/state/position.json 2>/dev/null || echo '{}'", STATE / "position.json"),
        ("cat /app/state/hypotheses.jsonl 2>/dev/null || echo ''", STATE / "hypotheses.jsonl"),
        ("cat /app/state/markov_regime.json 2>/dev/null || echo '{}'", STATE / "markov_regime.json"),
    ]

    for cmd, dest in files_to_sync:
        print(f"  syncing {dest.name} ...")
        try:
            result = subprocess.run(
                ["railway", "ssh", cmd],
                cwd=str(BASE),
                timeout=20,
                capture_output=True,
                text=True,
            )
            dest.write_text(result.stdout)
        except Exception as exc:
            print(f"  WARNING: could not sync {dest.name}: {exc}")

    print("  syncing logs ...")
    try:
        result = subprocess.run(
            ["railway", "logs", "--tail", "60"],
            cwd=str(BASE),
            timeout=20,
            capture_output=True,
            text=True,
        )
        (STATE / "last_logs.txt").write_text(result.stdout)
    except Exception as exc:
        print(f"  WARNING: could not sync logs: {exc}")

    print("  sync done.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path, default=None):
    if default is None:
        default = {}
    try:
        text = Path(path).read_text().strip()
        if not text:
            return default
        return json.loads(text)
    except Exception:
        return default


def _load_yaml(path, default=None):
    if default is None:
        default = {}
    try:
        text = Path(path).read_text().strip()
        if not text:
            return default
        return yaml.safe_load(text) or default
    except Exception:
        return default


def _load_trades(path) -> list:
    trades = []
    try:
        text = Path(path).read_text().strip()
        if not text:
            return trades
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return trades


def _load_hypotheses(path) -> list:
    hypotheses = []
    try:
        text = Path(path).read_text().strip()
        if not text:
            return hypotheses
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    hypotheses.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return hypotheses


# ---------------------------------------------------------------------------
# Build Dashboard
# ---------------------------------------------------------------------------

def build_dashboard():
    _sync_from_railway()

    # Load state
    heartbeat = _load_json(STATE / "heartbeat.json")
    strategy = _load_yaml(STATE / "strategy.yaml")
    trades = _load_trades(STATE / "trades.jsonl")
    position = _load_json(STATE / "position.json")
    hypotheses = _load_hypotheses(STATE / "hypotheses.jsonl")
    markov = _load_json(STATE / "markov_regime.json")

    try:
        log_text = (STATE / "last_logs.txt").read_text()
    except Exception:
        log_text = ""

    # ------------------------------------------------------------------
    # Trade calculations
    # ------------------------------------------------------------------
    closed_trades = [t for t in trades if t.get("status") == "closed" or t.get("exit_price") is not None]
    wins = [t for t in closed_trades if t.get("pnl_pct", 0) > 0]
    losses = [t for t in closed_trades if t.get("pnl_pct", 0) <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0.0
    total_pnl = sum(t.get("pnl_pct", 0) for t in closed_trades) * 100
    avg_win = (sum(t.get("pnl_pct", 0) for t in wins) / len(wins) * 100) if wins else 0.0
    avg_loss = (sum(t.get("pnl_pct", 0) for t in losses) / len(losses) * 100) if losses else 0.0

    # Equity curve
    equity = [1.0]
    for t in closed_trades:
        pnl_pct = t.get("pnl_pct", 0)
        equity.append(equity[-1] * (1.0 + pnl_pct))

    # ------------------------------------------------------------------
    # Markov regime
    # ------------------------------------------------------------------
    regime_label = markov.get("regime", markov.get("label", "N/A"))
    regime_signal = markov.get("signal", "N/A")
    regime_fresh = markov.get("fresh", False)
    next_probs = markov.get("next_probs", markov.get("transition_probs", {}))
    regime_mix = markov.get("mix", markov.get("state_mix", {}))

    # ------------------------------------------------------------------
    # Bot status
    # ------------------------------------------------------------------
    consecutive_failures = heartbeat.get("consecutive_failures", 0)
    last_error = heartbeat.get("last_error", "")
    if consecutive_failures >= 3 or "CIRCUIT" in str(last_error).upper():
        bot_status = "CIRCUIT BREAK"
        status_color = "#ef4444"
    elif consecutive_failures >= 1 or last_error:
        bot_status = "WARNING"
        status_color = "#f59e0b"
    else:
        bot_status = "ONLINE"
        status_color = "#22c55e"

    strategy_version = strategy.get("version", "N/A") if isinstance(strategy, dict) else "N/A"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # Pre-build HTML sections (NO nested f-strings)
    # ------------------------------------------------------------------

    # --- Trade rows ---
    trade_rows_parts = []
    for t in reversed(closed_trades[-30:]):
        date_val = t.get("date", t.get("open_time", t.get("timestamp", "")))
        entry_val = t.get("entry_price", t.get("entry", ""))
        exit_val = t.get("exit_price", t.get("exit", ""))
        pnl_val = t.get("pnl_pct", 0) * 100
        reason_val = t.get("exit_reason", t.get("reason", ""))
        size_val = t.get("size", t.get("quantity", ""))
        pnl_color = "#22c55e" if pnl_val > 0 else "#ef4444"
        pnl_str = "{:.2f}%".format(pnl_val)

        row = (
            "<tr style='border-bottom:1px solid #334155'>"
            "<td style='padding:6px 8px;color:#94a3b8'>" + str(date_val) + "</td>"
            "<td style='padding:6px 8px'>" + str(entry_val) + "</td>"
            "<td style='padding:6px 8px'>" + str(exit_val) + "</td>"
            "<td style='padding:6px 8px;color:" + pnl_color + "'>" + pnl_str + "</td>"
            "<td style='padding:6px 8px;color:#94a3b8'>" + str(reason_val) + "</td>"
            "<td style='padding:6px 8px'>" + str(size_val) + "</td>"
            "</tr>"
        )
        trade_rows_parts.append(row)

    if trade_rows_parts:
        trade_rows = "\n".join(trade_rows_parts)
    else:
        trade_rows = "<tr><td colspan='6' style='padding:12px;text-align:center;color:#94a3b8'>Nessun trade chiuso</td></tr>"

    # --- Hypothesis rows ---
    hyp_rows_parts = []
    for h in reversed(hypotheses[-20:]):
        h_date = h.get("date", h.get("timestamp", ""))
        h_var = h.get("variable", h.get("param", ""))
        h_change = h.get("change", h.get("new_value", ""))
        h_rationale = h.get("rationale", h.get("reason", ""))
        h_version = h.get("version", "")

        row = (
            "<tr style='border-bottom:1px solid #334155'>"
            "<td style='padding:6px 8px;color:#94a3b8'>" + str(h_date) + "</td>"
            "<td style='padding:6px 8px;color:#38bdf8'>" + str(h_var) + "</td>"
            "<td style='padding:6px 8px'>" + str(h_change) + "</td>"
            "<td style='padding:6px 8px;color:#94a3b8;font-size:12px'>" + str(h_rationale) + "</td>"
            "<td style='padding:6px 8px;color:#a78bfa'>" + str(h_version) + "</td>"
            "</tr>"
        )
        hyp_rows_parts.append(row)

    if hyp_rows_parts:
        hyp_rows = "\n".join(hyp_rows_parts)
    else:
        hyp_rows = "<tr><td colspan='5' style='padding:12px;text-align:center;color:#94a3b8'>Nessuna reflection</td></tr>"

    # --- Log colorization ---
    log_lines = log_text.splitlines()[-30:]
    log_html_parts = []
    for line in log_lines:
        upper = line.upper()
        if any(k in upper for k in ["OPEN", "PARTIAL", "TRAILING"]):
            color = "#22c55e"
        elif any(k in upper for k in ["CLOSE STOP", "FATAL", "ERROR"]):
            color = "#ef4444"
        elif "BLOCKED" in upper:
            color = "#f59e0b"
        else:
            color = "#94a3b8"

        safe_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        log_html_parts.append("<div style='color:" + color + ";font-size:11px;font-family:monospace'>" + safe_line + "</div>")

    if log_html_parts:
        log_html = "\n".join(log_html_parts)
    else:
        log_html = "<div style='color:#94a3b8;font-size:12px'>Nessun log disponibile</div>"

    # --- Markov prob bars ---
    prob_bars_parts = []
    for regime_name, prob in next_probs.items():
        pct = round(float(prob) * 100, 1)
        pct_str = str(pct) + "%"
        bar_width = str(pct) + "%"
        bar_color = "#22c55e" if "bull" in str(regime_name).lower() else "#ef4444" if "bear" in str(regime_name).lower() else "#f59e0b"

        bar_html = (
            "<div style='margin-bottom:8px'>"
            "<div style='display:flex;justify-content:space-between;margin-bottom:2px'>"
            "<span style='font-size:12px'>" + str(regime_name) + "</span>"
            "<span style='font-size:12px;color:#94a3b8'>" + pct_str + "</span>"
            "</div>"
            "<div style='background:#334155;border-radius:4px;height:8px'>"
            "<div style='background:" + bar_color + ";width:" + bar_width + ";height:8px;border-radius:4px'></div>"
            "</div>"
            "</div>"
        )
        prob_bars_parts.append(bar_html)

    if prob_bars_parts:
        prob_bars = "\n".join(prob_bars_parts)
    else:
        prob_bars = "<div style='color:#94a3b8;font-size:12px'>Dati non disponibili</div>"

    # --- Position card ---
    if position and isinstance(position, dict) and position.get("entry_price"):
        pos_entry = position.get("entry_price", "N/A")
        pos_sl = position.get("stop_loss", "N/A")
        pos_trailing = position.get("trailing_stop", position.get("trailing", "N/A"))
        pos_size = position.get("size", position.get("quantity", "N/A"))
        pos_html = (
            "<div class='card' style='margin-bottom:24px;padding:20px'>"
            "<h2 style='font-size:16px;font-weight:600;margin-bottom:12px;color:#38bdf8'>Posizione Aperta</h2>"
            "<div style='display:grid;grid-template-columns:repeat(4,1fr);gap:16px'>"
            "<div><div style='color:#94a3b8;font-size:12px;margin-bottom:4px'>Entry</div>"
            "<div style='font-size:18px;font-weight:700'>" + str(pos_entry) + "</div></div>"
            "<div><div style='color:#94a3b8;font-size:12px;margin-bottom:4px'>Stop Loss</div>"
            "<div style='font-size:18px;font-weight:700;color:#ef4444'>" + str(pos_sl) + "</div></div>"
            "<div><div style='color:#94a3b8;font-size:12px;margin-bottom:4px'>Trailing</div>"
            "<div style='font-size:18px;font-weight:700;color:#f59e0b'>" + str(pos_trailing) + "</div></div>"
            "<div><div style='color:#94a3b8;font-size:12px;margin-bottom:4px'>Size</div>"
            "<div style='font-size:18px;font-weight:700'>" + str(pos_size) + "</div></div>"
            "</div>"
            "</div>"
        )
    else:
        pos_html = ""

    # --- Strategy YAML as text ---
    try:
        strategy_yaml_text = (STATE / "strategy.yaml").read_text()
    except Exception:
        strategy_yaml_text = "N/A"
    safe_strategy = strategy_yaml_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # --- Equity curve JS ---
    if closed_trades and len(equity) > 1:
        eq_labels = list(range(len(equity)))
        eq_labels_json = json.dumps(eq_labels)
        eq_data_json = json.dumps([round(v, 4) for v in equity])
        eq_script = (
            "<script>"
            "const ctx = document.getElementById('equityChart').getContext('2d');"
            "new Chart(ctx, {"
            "type: 'line',"
            "data: {"
            "labels: " + eq_labels_json + ","
            "datasets: [{"
            "label: 'Equity',"
            "data: " + eq_data_json + ","
            "borderColor: '#38bdf8',"
            "backgroundColor: 'rgba(56,189,248,0.1)',"
            "borderWidth: 2,"
            "pointRadius: 0,"
            "fill: true,"
            "tension: 0.3"
            "}]"
            "},"
            "options: {"
            "responsive: true,"
            "maintainAspectRatio: false,"
            "plugins: {legend: {display: false}},"
            "scales: {"
            "x: {display: false},"
            "y: {"
            "grid: {color: '#334155'},"
            "ticks: {color: '#94a3b8'}"
            "}"
            "}"
            "}"
            "});"
            "</script>"
        )
        equity_canvas = "<canvas id='equityChart' style='width:100%;height:200px'></canvas>"
    else:
        eq_script = ""
        equity_canvas = "<div style='color:#94a3b8;font-size:13px;text-align:center;padding:40px'>Nessun trade chiuso — equity curve non disponibile</div>"

    # --- Pre-compute display values ---
    win_rate_str = "{:.1f}%".format(win_rate)
    total_pnl_str = "{:.2f}%".format(total_pnl)
    avg_win_str = "{:.2f}%".format(avg_win)
    avg_loss_str = "{:.2f}%".format(avg_loss)
    pnl_color_str = "#22c55e" if total_pnl >= 0 else "#ef4444"
    regime_fresh_str = "FRESH" if regime_fresh else "STALE"
    fresh_color = "#22c55e" if regime_fresh else "#f59e0b"
    closed_count_str = str(len(closed_trades))
    hyp_count_str = str(len(hypotheses))

    # Mix storico
    mix_parts = []
    for k, v in regime_mix.items():
        mix_parts.append(str(k) + ": " + str(round(float(v), 3)))
    mix_str = " | ".join(mix_parts) if mix_parts else "N/A"

    last_hb = heartbeat.get("timestamp", heartbeat.get("last_tick", "N/A"))

    # ------------------------------------------------------------------
    # Build full HTML (single f-string, no nesting)
    # ------------------------------------------------------------------
    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="60">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hermes Trading Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body {{ background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif; margin: 0; padding: 0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; }}
    .kpi-val {{ font-size: 24px; font-weight: 700; margin-top: 6px; }}
    .kpi-label {{ font-size: 12px; color: #94a3b8; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; padding: 8px; color: #94a3b8; font-size: 12px; font-weight: 500; border-bottom: 1px solid #334155; }}
    td {{ vertical-align: top; }}
    pre {{ white-space: pre-wrap; word-break: break-all; font-size: 12px; font-family: monospace; color: #94a3b8; }}
  </style>
</head>
<body style="padding: 24px;">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px">
    <div>
      <h1 style="font-size:24px;font-weight:700;margin:0">Hermes Trading Bot</h1>
      <div style="color:#94a3b8;font-size:13px;margin-top:4px">Aggiornato: {now_str} &nbsp;|&nbsp; Heartbeat: {last_hb}</div>
    </div>
    <div style="display:flex;align-items:center;gap:16px">
      <span style="background:{status_color};color:#0f172a;padding:6px 16px;border-radius:20px;font-weight:700;font-size:14px">{bot_status}</span>
      <span style="color:#a78bfa;font-size:13px">v{strategy_version}</span>
    </div>
  </div>

  <!-- KPI Row -->
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin-bottom:24px">
    <div class="card" style="padding:16px">
      <div class="kpi-label">Regime Daily</div>
      <div class="kpi-val" style="color:#38bdf8">{regime_label}</div>
      <div style="font-size:11px;color:{fresh_color};margin-top:4px">{regime_fresh_str}</div>
    </div>
    <div class="card" style="padding:16px">
      <div class="kpi-label">Trade Chiusi</div>
      <div class="kpi-val">{closed_count_str}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">W:{len(wins)} L:{len(losses)}</div>
    </div>
    <div class="card" style="padding:16px">
      <div class="kpi-label">Win Rate</div>
      <div class="kpi-val" style="color:#22c55e">{win_rate_str}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">avg win {avg_win_str} / loss {avg_loss_str}</div>
    </div>
    <div class="card" style="padding:16px">
      <div class="kpi-label">PnL Totale</div>
      <div class="kpi-val" style="color:{pnl_color_str}">{total_pnl_str}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">somma pnl_pct chiusi</div>
    </div>
    <div class="card" style="padding:16px">
      <div class="kpi-label">Reflections</div>
      <div class="kpi-val" style="color:#a78bfa">{hyp_count_str}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">ipotesi totali</div>
    </div>
  </div>

  <!-- Position Card -->
  {pos_html}

  <!-- Markov + Equity Grid -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px">

    <!-- Markov Card -->
    <div class="card" style="padding:20px">
      <h2 style="font-size:16px;font-weight:600;margin-bottom:12px;color:#38bdf8">Markov Regime</h2>
      <div style="margin-bottom:8px">
        <span style="color:#94a3b8;font-size:12px">Regime corrente: </span>
        <span style="font-weight:600">{regime_label}</span>
      </div>
      <div style="margin-bottom:8px">
        <span style="color:#94a3b8;font-size:12px">Signal: </span>
        <span style="font-weight:600">{regime_signal}</span>
      </div>
      <div style="margin-bottom:16px">
        <span style="color:#94a3b8;font-size:12px">Mix storico: </span>
        <span style="font-size:12px;color:#e2e8f0">{mix_str}</span>
      </div>
      <div style="color:#94a3b8;font-size:12px;margin-bottom:8px;font-weight:500">Prob prossimo giorno:</div>
      {prob_bars}
    </div>

    <!-- Equity Curve Card -->
    <div class="card" style="padding:20px">
      <h2 style="font-size:16px;font-weight:600;margin-bottom:12px;color:#38bdf8">Equity Curve</h2>
      <div style="height:200px;position:relative">
        {equity_canvas}
      </div>
    </div>

  </div>

  <!-- Strategy + Logs Grid -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px">

    <!-- Strategy Card -->
    <div class="card" style="padding:20px">
      <h2 style="font-size:16px;font-weight:600;margin-bottom:12px;color:#38bdf8">Strategia Corrente</h2>
      <pre>{safe_strategy}</pre>
    </div>

    <!-- Log Live Card -->
    <div class="card" style="padding:20px">
      <h2 style="font-size:16px;font-weight:600;margin-bottom:12px;color:#38bdf8">Log Live (ultimi 30)</h2>
      <div style="max-height:320px;overflow-y:auto">
        {log_html}
      </div>
    </div>

  </div>

  <!-- Trade Table -->
  <div class="card" style="padding:20px;margin-bottom:24px">
    <h2 style="font-size:16px;font-weight:600;margin-bottom:12px;color:#38bdf8">Ultimi Trade Chiusi</h2>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Data</th><th>Entry</th><th>Exit</th><th>PnL%</th><th>Motivo</th><th>Size</th>
          </tr>
        </thead>
        <tbody>
          {trade_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Reflections Table -->
  <div class="card" style="padding:20px;margin-bottom:24px">
    <h2 style="font-size:16px;font-weight:600;margin-bottom:12px;color:#a78bfa">Storico Reflections</h2>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Data</th><th>Variabile</th><th>Cambio</th><th>Rationale</th><th>Versione</th>
          </tr>
        </thead>
        <tbody>
          {hyp_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- Footer -->
  <div style="text-align:center;color:#475569;font-size:12px;padding:16px 0">
    Hermes Trading Dashboard &nbsp;&bull;&nbsp; Auto-refresh ogni 60s &nbsp;&bull;&nbsp; {now_str}
  </div>

  {eq_script}

</body>
</html>"""

    OUT.write_text(html)
    print("Dashboard written to:", str(OUT))


if __name__ == "__main__":
    build_dashboard()
