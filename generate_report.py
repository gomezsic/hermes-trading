"""
generate_report.py — genera un report HTML interattivo con:
- Grafico prezzo BTC daily colorato per regime Markov (Bear/Sideways/Bull)
- Matrice di transizione live
- Segnale corrente e probabilità regime prossimo giorno
- Walk-forward equity curve vs Buy-and-Hold
- Volume profile intraday (ultimi 30 giorni)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# --- aggiungi il path dello skill ---
SKILL_DIR = Path(__file__).resolve().parent.parent / ".claude/skills/markov-hedge-fund-method"
sys.path.insert(0, str(SKILL_DIR))

from markov_hedge_fund_method.regime import (
    STATES,
    label_regimes,
    build_transition_matrix,
    stationary_distribution,
    signal_from_matrix,
    walk_forward_backtest,
)

REGIME_COLORS = {0: "#ef4444", 1: "#f59e0b", 2: "#22c55e"}  # Bear=red, Sideways=yellow, Bull=green
REGIME_NAMES  = {0: "Bear", 1: "Sideways", 2: "Bull"}


def fetch(ticker: str, years: int) -> pd.DataFrame:
    end   = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    start = end - pd.DateOffset(years=years)
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


def walk_forward_equity(close: pd.Series, labels: pd.Series, min_train: int = 60) -> tuple:
    """Ritorna (dates, strategy_equity, bh_equity) per il grafico."""
    daily_ret = close.pct_change().dropna()
    common = labels.index.intersection(daily_ret.index)
    labels_ = labels.loc[common]
    daily_ret_ = daily_ret.loc[common]

    strat, bh, dates = [], [], []
    for t in range(min_train, len(labels_) - 1):
        P_t = build_transition_matrix(labels_.iloc[:t])
        sig = signal_from_matrix(P_t, int(labels_.iloc[t]))
        pos = float(np.clip(sig, -1.0, 1.0))
        r = float(daily_ret_.iloc[t + 1])
        strat.append(pos * r)
        bh.append(r)
        dates.append(daily_ret_.index[t + 1].strftime("%Y-%m-%d"))

    strat_eq = list((1 + np.array(strat)).cumprod())
    bh_eq    = list((1 + np.array(bh)).cumprod())
    return dates, strat_eq, bh_eq


def build_report(ticker: str, years: int, threshold: float, window: int, out_path: Path):
    print(f"Fetching {ticker} ({years}y daily)...")
    df = fetch(ticker, years)
    close  = df["Close"]
    volume = df["Volume"] if "Volume" in df.columns else pd.Series(dtype=float)

    print("Computing Markov regimes...")
    labels = label_regimes(close, window=window, threshold=threshold)
    P      = build_transition_matrix(labels)
    pi     = stationary_distribution(P)

    # Segnale corrente
    current_state  = int(labels.iloc[-1])
    current_signal = signal_from_matrix(P, current_state)
    next_probs     = P[current_state].tolist()

    # Walk-forward
    print("Running walk-forward equity curve...")
    wf_dates, strat_eq, bh_eq = walk_forward_equity(close, labels, min_train=60)

    # Prepara serie allineate per il grafico principale
    aligned = close.to_frame("close").join(labels.rename("regime"), how="left")
    aligned["regime"] = aligned["regime"].ffill().fillna(1).astype(int)

    dates_all  = [d.strftime("%Y-%m-%d") for d in aligned.index]
    prices_all = [round(float(v), 2) for v in aligned["close"]]
    regimes_all = [int(v) for v in aligned["regime"]]
    colors_all  = [REGIME_COLORS[r] for r in regimes_all]

    # Volume (daily, se disponibile)
    vol_dates  = [d.strftime("%Y-%m-%d") for d in volume.index] if len(volume) else []
    vol_values = [round(float(v), 0) for v in volume] if len(volume) else []

    # Matrice come lista di liste per la tabella
    matrix_pct = [[round(P[i, j] * 100, 2) for j in range(3)] for i in range(3)]
    pi_pct     = [round(float(x) * 100, 2) for x in pi]

    # Stats generali
    bear_pct = round(float((labels == 0).mean()) * 100, 1)
    side_pct = round(float((labels == 1).mean()) * 100, 1)
    bull_pct = round(float((labels == 2).mean()) * 100, 1)

    result = walk_forward_backtest(close, labels, min_train=60)

    # --- JSON data blob ---
    data = {
        "ticker": ticker,
        "years": years,
        "threshold": threshold,
        "window": window,
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_price": prices_all[-1],
        "last_date": dates_all[-1],
        "current_regime": REGIME_NAMES[current_state],
        "current_signal": round(current_signal, 4),
        "next_probs": {STATES[i]: round(next_probs[i] * 100, 1) for i in range(3)},
        "pi": {STATES[i]: pi_pct[i] for i in range(3)},
        "matrix": matrix_pct,
        "bear_pct": bear_pct,
        "side_pct": side_pct,
        "bull_pct": bull_pct,
        "sharpe": round(result["sharpe"], 3) if np.isfinite(result["sharpe"]) else None,
        "bh_sharpe": round(result["bh_sharpe"], 3) if np.isfinite(result["bh_sharpe"]) else None,
        "max_dd": round(result["max_drawdown"] * 100, 2) if np.isfinite(result["max_drawdown"]) else None,
        "bh_max_dd": round(result["bh_max_drawdown"] * 100, 2) if np.isfinite(result["bh_max_drawdown"]) else None,
        "dates": dates_all,
        "prices": prices_all,
        "regimes": regimes_all,
        "colors": colors_all,
        "vol_dates": vol_dates,
        "vol_values": vol_values,
        "wf_dates": wf_dates,
        "strat_eq": [round(x, 4) for x in strat_eq],
        "bh_eq": [round(x, 4) for x in bh_eq],
    }

    html = _render_html(data)
    out_path.write_text(html, encoding="utf-8")
    print(f"Report salvato: {out_path}")


def _render_html(d: dict) -> str:
    signal_color = "#22c55e" if d["current_signal"] > 0.1 else ("#ef4444" if d["current_signal"] < -0.1 else "#f59e0b")
    regime_color = {"Bear": "#ef4444", "Sideways": "#f59e0b", "Bull": "#22c55e"}[d["current_regime"]]

    matrix_rows = ""
    for i, from_s in enumerate(STATES):
        cells = ""
        for j in range(3):
            val = d["matrix"][i][j]
            intensity = int(val * 2)
            bg = f"rgba(34,197,94,{val/100:.2f})" if j == 2 else (f"rgba(239,68,68,{val/100:.2f})" if j == 0 else f"rgba(245,158,11,{val/100:.2f})")
            diag = " font-bold" if i == j else ""
            cells += f'<td class="border border-gray-600 px-3 py-2 text-center{diag}" style="background:{bg}">{val:.1f}%</td>'
        matrix_rows += f'<tr><td class="border border-gray-600 px-3 py-2 font-semibold text-gray-300">{from_s}</td>{cells}</tr>'

    next_prob_bars = ""
    for state, prob in d["next_probs"].items():
        color = {"Bear": "#ef4444", "Sideways": "#f59e0b", "Bull": "#22c55e"}[state]
        next_prob_bars += f'''
        <div class="flex items-center gap-3 mb-2">
          <span class="text-gray-400 w-20 text-sm">{state}</span>
          <div class="flex-1 bg-gray-700 rounded-full h-4">
            <div class="h-4 rounded-full" style="width:{prob}%;background:{color}"></div>
          </div>
          <span class="text-white font-mono w-12 text-right text-sm">{prob:.1f}%</span>
        </div>'''

    data_json = json.dumps(d)

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Markov Regime Report — {d['ticker']}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-annotation@3.0.1/dist/chartjs-plugin-annotation.min.js"></script>
<style>
  body {{ background: #0f172a; color: #e2e8f0; font-family: 'Inter', system-ui, sans-serif; }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }}
  .mono {{ font-family: 'JetBrains Mono', 'Fira Code', monospace; }}
  canvas {{ max-height: 340px; }}
</style>
</head>
<body class="p-6">

<div class="max-w-7xl mx-auto">

  <!-- Header -->
  <div class="flex items-center justify-between mb-6">
    <div>
      <h1 class="text-3xl font-bold text-white">{d['ticker']} — Markov Regime Report</h1>
      <p class="text-gray-400 text-sm mt-1">Generato: {d['generated']} · Daily · Window {d['window']}d · Threshold {d['threshold']:.0%} · {d['years']}y</p>
    </div>
    <div class="text-right">
      <div class="text-4xl font-bold mono text-white">${d['last_price']:,.0f}</div>
      <div class="text-sm text-gray-400">{d['last_date']}</div>
    </div>
  </div>

  <!-- KPI cards -->
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
    <div class="card text-center">
      <div class="text-xs text-gray-400 uppercase tracking-wider mb-1">Regime Corrente</div>
      <div class="text-2xl font-bold" style="color:{regime_color}">{d['current_regime']}</div>
    </div>
    <div class="card text-center">
      <div class="text-xs text-gray-400 uppercase tracking-wider mb-1">Segnale Markov</div>
      <div class="text-2xl font-bold mono" style="color:{signal_color}">{d['current_signal']:+.3f}</div>
      <div class="text-xs text-gray-500 mt-1">-1 Bear → +1 Bull</div>
    </div>
    <div class="card text-center">
      <div class="text-xs text-gray-400 uppercase tracking-wider mb-1">Sharpe (WF vs B&H)</div>
      <div class="text-2xl font-bold mono text-white">{d['sharpe'] or 'N/A'}</div>
      <div class="text-xs text-gray-500 mt-1">B&H: {d['bh_sharpe'] or 'N/A'}</div>
    </div>
    <div class="card text-center">
      <div class="text-xs text-gray-400 uppercase tracking-wider mb-1">Max Drawdown</div>
      <div class="text-2xl font-bold mono text-red-400">{d['max_dd']}%</div>
      <div class="text-xs text-gray-500 mt-1">B&H: {d['bh_max_dd']}%</div>
    </div>
  </div>

  <!-- Grafico prezzo + regime -->
  <div class="card mb-6">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-lg font-semibold text-white">Prezzo Daily — colorato per regime Markov</h2>
      <div class="flex gap-4 text-sm">
        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full inline-block" style="background:#22c55e"></span> Bull</span>
        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full inline-block" style="background:#f59e0b"></span> Sideways</span>
        <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full inline-block" style="background:#ef4444"></span> Bear</span>
      </div>
    </div>
    <canvas id="priceChart"></canvas>
  </div>

  <!-- Volume + equity curve -->
  <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
    <div class="card">
      <h2 class="text-lg font-semibold text-white mb-4">Volume Daily</h2>
      <canvas id="volChart"></canvas>
    </div>
    <div class="card">
      <h2 class="text-lg font-semibold text-white mb-4">Equity Curve Walk-Forward</h2>
      <p class="text-xs text-gray-500 mb-2">Gross of costs. Min train: 60 giorni.</p>
      <canvas id="eqChart"></canvas>
    </div>
  </div>

  <!-- Matrice + prossimo regime + distribuzione stazionaria -->
  <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
    <div class="card md:col-span-2">
      <h2 class="text-lg font-semibold text-white mb-4">Matrice di Transizione</h2>
      <table class="w-full text-sm">
        <thead>
          <tr>
            <th class="border border-gray-600 px-3 py-2 text-left text-gray-400">Da \ A</th>
            <th class="border border-gray-600 px-3 py-2 text-center text-red-400">Bear</th>
            <th class="border border-gray-600 px-3 py-2 text-center text-yellow-400">Sideways</th>
            <th class="border border-gray-600 px-3 py-2 text-center text-green-400">Bull</th>
          </tr>
        </thead>
        <tbody>{matrix_rows}</tbody>
      </table>
      <p class="text-xs text-gray-500 mt-3">La diagonale mostra la persistenza: alta % = i trend durano.</p>
    </div>

    <div class="card">
      <h2 class="text-lg font-semibold text-white mb-4">Prossimo Giorno</h2>
      <p class="text-xs text-gray-400 mb-3">Da regime <span style="color:{regime_color}" class="font-semibold">{d['current_regime']}</span>:</p>
      {next_prob_bars}
      <hr class="border-gray-600 my-4">
      <h3 class="text-sm font-semibold text-gray-300 mb-3">Mix Stazionario (lungo termine)</h3>
      <div class="space-y-1 text-sm mono">
        <div class="flex justify-between"><span class="text-red-400">Bear</span><span>{d['pi']['Bear']}%</span></div>
        <div class="flex justify-between"><span class="text-yellow-400">Sideways</span><span>{d['pi']['Sideways']}%</span></div>
        <div class="flex justify-between"><span class="text-green-400">Bull</span><span>{d['pi']['Bull']}%</span></div>
      </div>
    </div>
  </div>

  <!-- Label mix -->
  <div class="card mb-6">
    <h2 class="text-lg font-semibold text-white mb-4">Distribuzione Storica Regimi ({d['years']}y)</h2>
    <canvas id="donutChart" style="max-height:200px"></canvas>
  </div>

  <p class="text-center text-xs text-gray-600 mt-4">
    Framework: Roan (@RohOnChain) · Dati: Yahoo Finance · Gross of costs · Non predittivo
  </p>
</div>

<script>
const D = {data_json};

// ---- 1. Price chart (segmentato per regime) ----
const priceCtx = document.getElementById('priceChart').getContext('2d');

// Crea dataset segmentati per regime
function buildSegmentedDataset(dates, prices, regimes, colors) {{
  return dates.map((d, i) => ({{ x: d, y: prices[i] }}));
}}

new Chart(priceCtx, {{
  type: 'line',
  data: {{
    datasets: [{{
      label: 'Prezzo',
      data: D.dates.map((d, i) => ({{ x: d, y: D.prices[i] }})),
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.1,
      segment: {{
        borderColor: ctx => D.colors[ctx.p0DataIndex] || '#64748b',
      }},
      fill: false,
    }}]
  }},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    scales: {{
      x: {{ type: 'category', ticks: {{ maxTicksLimit: 12, color: '#94a3b8' }}, grid: {{ color: '#1e293b' }} }},
      y: {{ ticks: {{ color: '#94a3b8', callback: v => '$' + v.toLocaleString() }}, grid: {{ color: '#334155' }} }}
    }},
    plugins: {{ legend: {{ display: false }}, tooltip: {{
      callbacks: {{
        label: ctx => `${{D.dates[ctx.dataIndex]}}: ${{D.prices[ctx.dataIndex].toLocaleString()}} · ${{['Bear','Sideways','Bull'][D.regimes[ctx.dataIndex]] || ''}}`,
      }}
    }} }}
  }}
}});

// ---- 2. Volume chart ----
if (D.vol_dates.length > 0) {{
  const volCtx = document.getElementById('volChart').getContext('2d');
  // Colora le barre di volume con il colore del regime corrispondente
  const volColors = D.vol_dates.map(d => {{
    const idx = D.dates.indexOf(d);
    return idx >= 0 ? D.colors[idx] + 'aa' : '#64748baa';
  }});
  new Chart(volCtx, {{
    type: 'bar',
    data: {{
      labels: D.vol_dates,
      datasets: [{{ label: 'Volume', data: D.vol_values, backgroundColor: volColors, borderWidth: 0 }}]
    }},
    options: {{
      responsive: true,
      scales: {{
        x: {{ ticks: {{ maxTicksLimit: 8, color: '#94a3b8' }}, grid: {{ display: false }} }},
        y: {{ ticks: {{ color: '#94a3b8', callback: v => (v/1e9).toFixed(1)+'B' }}, grid: {{ color: '#334155' }} }}
      }},
      plugins: {{ legend: {{ display: false }} }}
    }}
  }});
}}

// ---- 3. Equity curve ----
const eqCtx = document.getElementById('eqChart').getContext('2d');
new Chart(eqCtx, {{
  type: 'line',
  data: {{
    labels: D.wf_dates,
    datasets: [
      {{ label: 'Strategia Markov', data: D.strat_eq, borderColor: '#22c55e', borderWidth: 2, pointRadius: 0, fill: false }},
      {{ label: 'Buy & Hold', data: D.bh_eq, borderColor: '#64748b', borderWidth: 1.5, pointRadius: 0, borderDash: [5,3], fill: false }},
    ]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 8, color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
      y: {{ ticks: {{ color: '#94a3b8', callback: v => v.toFixed(2)+'x' }}, grid: {{ color: '#334155' }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }}
  }}
}});

// ---- 4. Donut regimi ----
const donutCtx = document.getElementById('donutChart').getContext('2d');
new Chart(donutCtx, {{
  type: 'doughnut',
  data: {{
    labels: ['Bear', 'Sideways', 'Bull'],
    datasets: [{{ data: [D.bear_pct, D.side_pct, D.bull_pct], backgroundColor: ['#ef4444','#f59e0b','#22c55e'], borderWidth: 0 }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ position: 'right', labels: {{ color: '#94a3b8' }} }},
      tooltip: {{ callbacks: {{ label: ctx => `${{ctx.label}}: ${{ctx.raw}}%` }} }}
    }}
  }}
}});
</script>
</body>
</html>"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker",    default="BTC-USD")
    parser.add_argument("--years",     type=int,   default=2)
    parser.add_argument("--threshold", type=float, default=0.08)
    parser.add_argument("--window",    type=int,   default=20)
    parser.add_argument("--out",       default=str(Path.home() / "hermes-trading" / "markov_report.html"))
    args = parser.parse_args()
    build_report(args.ticker, args.years, args.threshold, args.window, Path(args.out))
