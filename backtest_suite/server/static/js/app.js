// hermes-bt frontend — routing + REST calls.
const main = document.getElementById('main');
let currentWs = null;

function activateTab(tab) {
  document.querySelectorAll('#topnav a').forEach(a => {
    a.classList.toggle('active', a.dataset.tab === tab);
  });
}

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

async function renderRuns() {
  activateTab('runs');
  const runs = await api('/runs');
  main.innerHTML = `
    <h2>Runs</h2>
    <table>
      <tr><th>id</th><th>kind</th><th>status</th><th>symbol</th>
          <th>started</th><th>best fitness</th><th></th></tr>
      ${runs.map(r => `<tr>
        <td>${r.id}</td><td>${r.kind}</td><td>${r.status}</td>
        <td>${r.symbol} ${r.timeframe}</td>
        <td>${r.started_at || ''}</td>
        <td>${r.best_fitness != null ? r.best_fitness.toFixed(4) : ''}</td>
        <td><a href="#/runs/${r.id}">open</a></td>
      </tr>`).join('')}
    </table>`;
}

async function renderRunDetail(runId) {
  activateTab('runs');
  const { run, top } = await api(`/runs/${runId}`);
  main.innerHTML = `
    <h2>Run #${run.id} — ${run.kind} · ${run.symbol} ${run.timeframe}</h2>
    <div class="card kpi">
      <div><div class="lbl">Status</div><div class="v">${run.status}</div></div>
      <div><div class="lbl">Best fitness</div><div class="v">${run.best_fitness ?? '—'}</div></div>
      <div><div class="lbl">Generations</div><div class="v">${run.n_generations ?? '—'}</div></div>
      <div><div class="lbl">Started</div><div class="v" style="font-size:13px">${run.started_at}</div></div>
    </div>
    <div class="card"><canvas id="fitness-chart" height="80"></canvas></div>
    <div class="card">
      <h3>Top individuals</h3>
      <table>
        <tr><th>rank</th><th>strategy</th><th>fitness</th><th>sharpe</th>
            <th>maxDD</th><th>n_trd</th></tr>
        ${top.map(t => `<tr>
          <td>${t.rank}</td><td>${t.strategy_id}</td>
          <td>${t.fitness.toFixed(4)}</td>
          <td>${t.sharpe != null ? t.sharpe.toFixed(3) : '—'}</td>
          <td>${t.max_drawdown != null ? (t.max_drawdown * 100).toFixed(2) + '%' : '—'}</td>
          <td>${t.n_trades ?? '—'}</td>
        </tr>`).join('')}
      </table>
    </div>`;

  if (window.charts) window.charts.fitnessChart('fitness-chart');

  // Live updates se status === 'running'
  if (run.status === 'running') {
    if (currentWs) currentWs.close();
    currentWs = new WebSocket(`ws://${location.host}/ws/runs/${runId}`);
    currentWs.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      if (ev.type === 'generation' && window.charts) {
        window.charts.pushFitnessPoint(ev.generation, ev.best_fitness, ev.mean_fitness);
        document.getElementById('live-banner').textContent =
          `● gen ${ev.generation} best=${ev.best_fitness.toFixed(3)}`;
      }
      if (ev.type === 'run_finished') {
        document.getElementById('live-banner').textContent = 'finished';
        currentWs.close();
      }
    };
  }
}

async function renderData() {
  activateTab('data');
  const tfs = ['1m', '5m', '15m', '1h', '4h', '1d'];
  const rows = await Promise.all(tfs.map(async tf => {
    const c = await api(`/data/coverage?symbol=BTCUSDT&timeframe=${tf}`);
    return { tf, ...c };
  }));
  main.innerHTML = `
    <h2>Data lake — BTCUSDT</h2>
    <table>
      <tr><th>timeframe</th><th>candles</th><th>since</th><th>until</th><th>gaps</th></tr>
      ${rows.map(r => `<tr>
        <td>${r.tf}</td><td>${r.n_candles}</td>
        <td>${r.since ? new Date(r.since * 1000).toISOString().slice(0,10) : '—'}</td>
        <td>${r.until ? new Date(r.until * 1000).toISOString().slice(0,10) : '—'}</td>
        <td>${r.gaps}</td>
      </tr>`).join('')}
    </table>`;
}

async function renderStrategies() {
  activateTab('strategies');
  const strategies = await api('/strategies');
  main.innerHTML = `
    <h2>Strategies registry</h2>
    ${strategies.map(s => `
      <div class="card">
        <h3>${s.display_name} <span style="color:#888">(${s.strategy_id})</span></h3>
        <p>Timeframes: ${s.timeframes.join(', ')}</p>
        <table>
          <tr><th>param</th><th>low</th><th>high</th><th>step</th><th>int?</th></tr>
          ${s.param_specs.map(p => `<tr>
            <td>${p.name}</td><td>${p.low}</td><td>${p.high}</td>
            <td>${p.step ?? '—'}</td><td>${p.is_int ? '✓' : ''}</td>
          </tr>`).join('')}
        </table>
      </div>`).join('')}`;
}

function renderSettings() {
  activateTab('settings');
  main.innerHTML = `
    <h2>Settings</h2>
    <div class="card">
      <p>Data root: <code>data/ohlcv/</code></p>
      <p>Catalog DB: <code>data/backtests/catalog.db</code></p>
      <p>Runs dir: <code>data/backtests/runs/</code></p>
      <p>Server: <code>${location.host}</code></p>
    </div>`;
}

function route() {
  const hash = location.hash || '#/runs';
  const m = hash.match(/^#\/runs\/(\d+)$/);
  if (m) return renderRunDetail(parseInt(m[1]));
  if (hash === '#/runs')       return renderRuns();
  if (hash === '#/data')       return renderData();
  if (hash === '#/strategies') return renderStrategies();
  if (hash === '#/settings')   return renderSettings();
  renderRuns();
}

window.addEventListener('hashchange', route);
route();
