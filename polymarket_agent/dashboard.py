"""
dashboard.py — Live web dashboard for the paper trading session.

Serves a browser dashboard at http://localhost:5000
The page auto-refreshes every 10 seconds by polling /api/state,
which reads data/state.json written by the paper trader after every tick.

Start automatically via:
  python main.py --strategy momentum --mode paper --duration 60 --dashboard

Or standalone (if trader is already running in another terminal):
  python dashboard.py
"""

import json
import os
import threading

from flask import Flask, jsonify, Response

from config import DATA_DIR

STATE_FILE = os.path.join(DATA_DIR, "state.json")

app = Flask(__name__)


# ---------------------------------------------------------------------------
# API endpoint — returns raw state JSON
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    if not os.path.exists(STATE_FILE):
        return jsonify({"error": "No state file yet — paper trader hasn't started a tick"}), 404
    with open(STATE_FILE) as f:
        data = json.load(f)
    return jsonify(data)


# ---------------------------------------------------------------------------
# Main dashboard page — single HTML file, no templates needed
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>BurryBot — Paper Trading Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3e;
      --text: #e2e8f0; --muted: #718096; --green: #48bb78;
      --red: #fc8181; --yellow: #f6e05e; --blue: #63b3ed;
      --purple: #b794f4; --accent: #667eea;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }

    header {
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 14px 24px; display: flex; align-items: center; justify-content: space-between;
    }
    header h1 { font-size: 16px; color: var(--blue); letter-spacing: 1px; }
    #status-pill {
      display: flex; align-items: center; gap: 8px;
      background: var(--bg); border: 1px solid var(--border);
      padding: 5px 12px; border-radius: 20px; font-size: 11px; color: var(--muted);
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green);
           animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

    main { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }

    /* Cards row */
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 14px 16px;
    }
    .card .label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 8px; }
    .card .value { font-size: 22px; font-weight: 700; }
    .card .sub   { font-size: 11px; color: var(--muted); margin-top: 4px; }
    .pos  { color: var(--green); }
    .neg  { color: var(--red); }
    .neu  { color: var(--text); }

    /* Two-column layout: chart + signals */
    .mid { display: grid; grid-template-columns: 1fr 380px; gap: 16px; }

    .panel {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 16px;
    }
    .panel h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .8px;
                color: var(--muted); margin-bottom: 12px; }

    /* Chart */
    #chart-wrap { position: relative; height: 220px; }

    /* Tables */
    table { width: 100%; border-collapse: collapse; }
    th { font-size: 10px; text-transform: uppercase; letter-spacing: .6px;
         color: var(--muted); padding: 4px 8px; text-align: left;
         border-bottom: 1px solid var(--border); }
    td { padding: 6px 8px; border-bottom: 1px solid #1e2130; vertical-align: top; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,.03); }

    .badge {
      display: inline-block; padding: 2px 7px; border-radius: 4px;
      font-size: 10px; font-weight: 700; letter-spacing: .5px;
    }
    .badge-buy  { background: rgba(72,187,120,.2); color: var(--green); }
    .badge-sell { background: rgba(252,129,129,.2); color: var(--red); }
    .badge-hold { background: rgba(113,128,150,.15); color: var(--muted); }

    .reason-text { color: var(--muted); font-size: 11px; margin-top: 2px; }

    #last-update { font-size: 10px; color: var(--muted); }

    /* Bottom: positions + trades side by side */
    .bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

    .no-data { color: var(--muted); font-size: 12px; padding: 12px 0; text-align: center; }

    /* Progress bar for time remaining */
    .progress-wrap { background: var(--bg); border-radius: 4px; height: 5px; margin-top: 8px; overflow: hidden; }
    .progress-bar  { height: 100%; background: var(--accent); border-radius: 4px; transition: width .5s; }
  </style>
</head>
<body>
<header>
  <h1>⚡ BurryBot — Paper Trading Dashboard</h1>
  <div id="status-pill">
    <span class="dot"></span>
    <span id="last-update">connecting…</span>
  </div>
</header>

<main>
  <!-- KPI Cards -->
  <div class="cards">
    <div class="card">
      <div class="label">Portfolio Value</div>
      <div class="value neu" id="kpi-total">—</div>
      <div class="sub">starting $<span id="kpi-start">—</span></div>
    </div>
    <div class="card">
      <div class="label">Total Return</div>
      <div class="value" id="kpi-return">—</div>
      <div class="sub" id="kpi-cash">cash: —</div>
    </div>
    <div class="card">
      <div class="label">Sharpe Ratio</div>
      <div class="value neu" id="kpi-sharpe">—</div>
      <div class="sub">risk-adjusted return</div>
    </div>
    <div class="card">
      <div class="label">Max Drawdown</div>
      <div class="value" id="kpi-drawdown">—</div>
      <div class="sub">worst peak-to-trough</div>
    </div>
    <div class="card">
      <div class="label">Win Rate</div>
      <div class="value neu" id="kpi-winrate">—</div>
      <div class="sub" id="kpi-trades">— trades</div>
    </div>
    <div class="card">
      <div class="label">Time Remaining</div>
      <div class="value neu" id="kpi-remaining">—</div>
      <div class="sub">tick <span id="kpi-tick">0</span> | strategy: <span id="kpi-strategy">—</span></div>
      <div class="progress-wrap"><div class="progress-bar" id="time-bar" style="width:0%"></div></div>
    </div>
  </div>

  <!-- Equity curve + market signals -->
  <div class="mid">
    <div class="panel">
      <h2>Equity Curve</h2>
      <div id="chart-wrap"><canvas id="equity-chart"></canvas></div>
    </div>
    <div class="panel" style="overflow:auto; max-height:290px;">
      <h2>Market Signals (latest)</h2>
      <table>
        <thead><tr><th>Market</th><th>Price</th><th>Signal</th></tr></thead>
        <tbody id="signals-body"><tr><td colspan="3" class="no-data">waiting for first tick…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Open positions + recent trades -->
  <div class="bottom">
    <div class="panel" style="overflow:auto;">
      <h2>Open Positions</h2>
      <table>
        <thead><tr><th>Market</th><th>Shares</th><th>Avg $</th><th>Now $</th><th>PnL</th></tr></thead>
        <tbody id="positions-body"><tr><td colspan="5" class="no-data">no open positions</td></tr></tbody>
      </table>
    </div>
    <div class="panel" style="overflow:auto;">
      <h2>Recent Trades</h2>
      <table>
        <thead><tr><th>Time</th><th>Market</th><th>Action</th><th>Price</th><th>PnL</th></tr></thead>
        <tbody id="trades-body"><tr><td colspan="5" class="no-data">no trades yet</td></tr></tbody>
      </table>
    </div>
  </div>
</main>

<script>
// ─── Chart setup ────────────────────────────────────────────────────────────
const ctx = document.getElementById('equity-chart').getContext('2d');
const equityChart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: 'Portfolio Value ($)',
      data: [],
      borderColor: '#667eea',
      backgroundColor: 'rgba(102,126,234,0.12)',
      borderWidth: 2,
      pointRadius: 3,
      pointBackgroundColor: '#667eea',
      tension: 0.3,
      fill: true,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 400 },
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: '#718096', font: { size: 10 } }, grid: { color: '#1e2130' } },
      y: { ticks: { color: '#718096', font: { size: 10 },
                    callback: v => '$' + v.toFixed(2) },
           grid: { color: '#1e2130' } }
    }
  }
});

// ─── Helpers ────────────────────────────────────────────────────────────────
function fmt$(v)   { return '$' + parseFloat(v).toFixed(2); }
function fmtPct(v) { const n = parseFloat(v); return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
function fmtMin(m) {
  const h = Math.floor(m / 60), mm = Math.round(m % 60);
  return h > 0 ? `${h}h ${mm}m` : `${mm}m`;
}
function colorClass(v) { return parseFloat(v) > 0 ? 'pos' : (parseFloat(v) < 0 ? 'neg' : 'neu'); }
function badgeClass(action) {
  return { BUY: 'badge-buy', SELL: 'badge-sell', HOLD: 'badge-hold' }[action] || 'badge-hold';
}
function timeLabel(iso) {
  const d = new Date(iso + 'Z');
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ─── Main refresh ────────────────────────────────────────────────────────────
async function refresh() {
  let data;
  try {
    const res = await fetch('/api/state');
    if (!res.ok) return;
    data = await res.json();
  } catch { return; }

  const p = data.portfolio;
  const m = data.metrics;

  // KPI cards
  document.getElementById('kpi-total').textContent   = fmt$(p.total_value);
  document.getElementById('kpi-start').textContent   = fmt$(p.starting_cash);

  const retEl = document.getElementById('kpi-return');
  retEl.textContent = fmtPct(p.total_return_pct);
  retEl.className   = 'value ' + colorClass(p.total_return_pct);

  document.getElementById('kpi-cash').textContent    = 'cash: ' + fmt$(p.cash);
  document.getElementById('kpi-sharpe').textContent  = isNaN(m.sharpe_ratio) ? '—' : m.sharpe_ratio.toFixed(4);

  const ddEl = document.getElementById('kpi-drawdown');
  ddEl.textContent = fmtPct(m.max_drawdown_pct);
  ddEl.className   = 'value ' + (m.max_drawdown_pct > 0 ? 'neg' : 'neu');

  document.getElementById('kpi-winrate').textContent  = m.win_rate_pct.toFixed(1) + '%';
  document.getElementById('kpi-trades').textContent   = p.total_trades + ' trades (' + p.sell_trades + ' sells)';
  document.getElementById('kpi-remaining').textContent = fmtMin(data.remaining_minutes);
  document.getElementById('kpi-tick').textContent     = data.tick;
  document.getElementById('kpi-strategy').textContent = data.strategy;

  const elapsed = data.elapsed_minutes;
  const total   = data.duration_minutes;
  document.getElementById('time-bar').style.width = Math.min(100, elapsed / total * 100) + '%';

  // Equity curve
  const curve = data.equity_curve || [];
  equityChart.data.labels  = curve.map((_, i) => 'T' + (i + 1));
  equityChart.data.datasets[0].data = curve;
  equityChart.update('none');

  // Market signals
  const sigBody = document.getElementById('signals-body');
  if (data.market_signals && data.market_signals.length) {
    sigBody.innerHTML = data.market_signals.map(s => `
      <tr>
        <td>
          <div style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
               title="${s.question}">${s.slug.replace(/-/g,' ')}</div>
          <div class="reason-text">${s.reason}</div>
        </td>
        <td>${parseFloat(s.price).toFixed(4)}</td>
        <td><span class="badge ${badgeClass(s.signal)}">${s.signal}</span></td>
      </tr>`).join('');
  } else {
    sigBody.innerHTML = '<tr><td colspan="3" class="no-data">waiting for first tick…</td></tr>';
  }

  // Open positions
  const posBody = document.getElementById('positions-body');
  if (data.positions && data.positions.length) {
    posBody.innerHTML = data.positions.map(pos => `
      <tr>
        <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
            title="${pos.market_slug}">${pos.market_slug.replace(/-/g,' ')}</td>
        <td>${parseFloat(pos.shares).toFixed(0)}</td>
        <td>${parseFloat(pos.avg_cost).toFixed(4)}</td>
        <td>${parseFloat(pos.current_price).toFixed(4)}</td>
        <td class="${colorClass(pos.unrealised_pnl)}">${pos.unrealised_pnl >= 0 ? '+' : ''}${parseFloat(pos.unrealised_pnl).toFixed(2)}</td>
      </tr>`).join('');
  } else {
    posBody.innerHTML = '<tr><td colspan="5" class="no-data">no open positions</td></tr>';
  }

  // Recent trades
  const trBody = document.getElementById('trades-body');
  if (data.recent_trades && data.recent_trades.length) {
    trBody.innerHTML = data.recent_trades.map(t => `
      <tr>
        <td>${timeLabel(t.timestamp)}</td>
        <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
            title="${t.market_slug}">${t.market_slug.replace(/-/g,' ')}</td>
        <td><span class="badge ${badgeClass(t.action)}">${t.action}</span></td>
        <td>${parseFloat(t.price).toFixed(4)}</td>
        <td class="${colorClass(t.pnl)}">${t.action === 'BUY' ? '—' : ((t.pnl >= 0 ? '+' : '') + parseFloat(t.pnl).toFixed(2))}</td>
      </tr>`).join('');
  } else {
    trBody.innerHTML = '<tr><td colspan="5" class="no-data">no trades yet</td></tr>';
  }

  // Status pill
  const ts = new Date(data.updated_at + 'Z');
  document.getElementById('last-update').textContent =
    'last tick ' + ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

refresh();
setInterval(refresh, 10_000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


# ---------------------------------------------------------------------------
# Public helper: start Flask in a daemon thread (called from main.py)
# ---------------------------------------------------------------------------

def start_in_thread(host: str = "127.0.0.1", port: int = 5000) -> None:
    """
    Launch the Flask dev server in a background daemon thread.

    Being a daemon thread means it dies automatically when the main program exits —
    no need to manually shut it down.
    """
    def _run():
        # use_reloader=False is required when running inside a thread
        app.run(host=host, port=port, use_reloader=False, threaded=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"\nDashboard running at → http://{host}:{port}")
    print("Open that URL in your browser. It refreshes every 10 seconds.\n")


if __name__ == "__main__":
    # Allow running standalone: python dashboard.py
    print("Starting dashboard server (standalone mode)...")
    print("Make sure the paper trader is running in another terminal.")
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
