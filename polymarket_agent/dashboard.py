"""
dashboard.py — Live web dashboard for the paper trading session.

Serves a browser dashboard at http://localhost:5000
The page auto-refreshes every 1 second by polling /api/state,
which reads data/state.json written by the paper trader after every tick.

Start automatically via:
  python main.py --strategy momentum --mode paper --duration 60 --dashboard

Or standalone (if trader is already running in another terminal):
  python dashboard.py
"""

import json
import logging
import os
import threading

from flask import Flask, jsonify, Response

from config import DATA_DIR

STATE_FILE = os.path.join(DATA_DIR, "state.json")

LOGS_DIR    = "logs"
ACCESS_LOG  = os.path.join(LOGS_DIR, "dashboard_access.log")

app = Flask(__name__)


def _redirect_werkzeug_to_file() -> None:
    """Send Werkzeug HTTP access logs to logs/dashboard_access.log instead of stdout."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    handler = logging.FileHandler(ACCESS_LOG)
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("werkzeug")
    logger.setLevel(logging.INFO)
    logger.handlers = [handler]  # replace stdout handler with file handler


# ---------------------------------------------------------------------------
# API endpoint — returns raw state JSON
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    if not os.path.exists(STATE_FILE):
        resp = jsonify({"error": "No state file yet — paper trader hasn't started a tick"})
    else:
        with open(STATE_FILE) as f:
            data = json.load(f)
        resp = jsonify(data)
    # Prevent the browser and any proxy from caching this response
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


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
           animation: pulse 2s infinite; flex-shrink: 0; }
    .dot-dead { background: var(--red) !important; animation: none !important; }
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
    .mid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

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

    /* Session ended banner */
    #session-ended-banner {
      display: none;
      background: rgba(252,129,129,.12); border-bottom: 1px solid rgba(252,129,129,.4);
      color: var(--red); padding: 8px 24px; font-size: 12px; text-align: center;
      letter-spacing: .4px;
    }
  </style>
</head>
<body>
<div id="session-ended-banner">⚠ Trader session ended — data is frozen</div>
<header>
  <div>
    <h1>⚡ BurryBot — Paper Trading Dashboard</h1>
    <div id="header-meta" style="font-size:10px;color:var(--muted);margin-top:3px;">connecting…</div>
  </div>
  <div id="status-pill">
    <span id="last-update">connecting…</span>
    <span style="color:var(--border);margin:0 4px;">|</span>
    <span style="display:flex;align-items:center;gap:5px;font-size:10px;color:var(--muted);"><span class="dot" id="live-dot"></span>hb:<span id="hb-count">0</span></span>
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
      <table style="table-layout:fixed">
        <thead><tr><th>Market</th><th style="width:70px">Price</th><th style="width:64px">Signal</th><th style="width:44px">Conf</th></tr></thead>
        <tbody id="signals-body"><tr><td colspan="4" class="no-data">waiting for first tick…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Open positions + recent trades -->
  <div class="bottom">
    <div class="panel" style="overflow:auto;">
      <h2>Open Positions</h2>
      <table>
        <thead><tr><th>Market</th><th>Shares</th><th>Avg $</th><th>Now $</th><th>PnL</th><th>% Portf</th></tr></thead>
        <tbody id="positions-body"><tr><td colspan="6" class="no-data">no open positions</td></tr></tbody>
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
// ─── Error banner (visible on page if JS crashes) ────────────────────────────
function showError(msg) {
  let el = document.getElementById('js-error-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'js-error-banner';
    el.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#e53e3e;color:#fff;' +
                       'padding:6px 14px;font-size:12px;z-index:9999;font-family:monospace;';
    document.body.prepend(el);
  }
  el.textContent = '⚠ JS error: ' + msg;
}

// ─── Chart setup (optional — gracefully skipped if Chart.js CDN unavailable) ─
let equityChart = null;
try {
  const ctx = document.getElementById('equity-chart').getContext('2d');
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Portfolio Value ($)',
          data: [],
          borderColor: '#667eea',
          backgroundColor: 'rgba(102,126,234,0.12)',
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.3,
          fill: true,
        },
        {
          label: 'Starting Cash',
          data: [],
          borderColor: 'rgba(113,128,150,0.5)',
          borderWidth: 1,
          borderDash: [4, 4],
          pointRadius: 0,
          pointHoverRadius: 0,
          tension: 0,
          fill: false,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },  // instant updates, no transition delay
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => '$' + ctx.parsed.y.toFixed(2) } },
      },
      scales: {
        x: { ticks: { color: '#718096', font: { size: 10 } }, grid: { color: '#1e2130' } },
        y: {
          grace: '5%',             // always show some y-range even on a flat equity curve
          ticks: { color: '#718096', font: { size: 10 }, callback: v => '$' + v.toFixed(2) },
          grid: { color: '#1e2130' }
        }
      }
    }
  });
} catch (e) {
  document.getElementById('chart-wrap').innerHTML =
    '<div style="color:var(--muted);text-align:center;padding:60px 0;font-size:12px;">' +
    'Chart unavailable (Chart.js failed to load)</div>';
}

// ─── Helpers ────────────────────────────────────────────────────────────────
function fmt$(v)   { return '$' + parseFloat(v).toFixed(2); }
function fmtPct(v) { const n = parseFloat(v); return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
function fmtMin(m) {
  if (!isFinite(m) || m <= 0) return '0m';
  const h = Math.floor(m / 60), mm = Math.round(m % 60);
  return h > 0 ? `${h}h ${mm}m` : `${mm}m`;
}
function colorClass(v) { const n = parseFloat(v); return n > 0 ? 'pos' : (n < 0 ? 'neg' : 'neu'); }
function badgeClass(action) {
  return { BUY: 'badge-buy', SELL: 'badge-sell', HOLD: 'badge-hold' }[action] || 'badge-hold';
}
function timeLabel(iso) {
  try {
    // isoformat() produces "2026-02-23T22:00:00.123456" — append Z for UTC
    const s = String(iso).replace(/(\.\d{3})\d+/, '$1');  // truncate microseconds → ms
    const d = new Date(s.endsWith('Z') ? s : s + 'Z');
    if (!isFinite(d)) return iso;
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return String(iso); }
}

// ─── Live status + heartbeat ─────────────────────────────────────────────────
const STALE_SECONDS = 360;  // same threshold as status.py
let isLive = false;
let heartbeat = 0;
let lastSuccessfulFetch = 0;  // epoch ms of last successful /api/state response

function setLiveStatus(live) {
  if (isLive === live) return;  // no change — skip DOM update
  isLive = live;
  const dot    = document.getElementById('live-dot');
  const banner = document.getElementById('session-ended-banner');
  if (live) {
    dot?.classList.remove('dot-dead');
    if (banner) banner.style.display = 'none';
  } else {
    dot?.classList.add('dot-dead');
    if (banner) banner.style.display = 'block';
  }
}

setInterval(() => {
  // Mark stale if the API hasn't responded successfully for STALE_SECONDS
  if (lastSuccessfulFetch > 0 && (Date.now() - lastSuccessfulFetch) / 1000 >= STALE_SECONDS) {
    setLiveStatus(false);
  }
  if (!isLive) return;
  heartbeat++;
  const hbEl = document.getElementById('hb-count');
  if (hbEl) hbEl.textContent = heartbeat;
}, 1000);

// ─── Main refresh (recursive setTimeout — never piles up) ────────────────────
async function refresh() {
  // Step 1: fetch state (errors here are expected during startup — silent)
  let data;
  try {
    const res = await fetch('/api/state?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) { setLiveStatus(false); scheduleNext(); return; }
    data = await res.json();
  } catch { setLiveStatus(false); scheduleNext(); return; }

  // Step 2: update DOM — wrapped so any bug shows visibly on the page
  try {
    const p = data.portfolio;
    const m = data.metrics;

    // KPI cards
    document.getElementById('kpi-total').textContent  = fmt$(p.total_value);
    document.getElementById('kpi-start').textContent  = fmt$(p.starting_cash);

    const retEl = document.getElementById('kpi-return');
    retEl.textContent = fmtPct(p.total_return_pct);
    retEl.className   = 'value ' + colorClass(p.total_return_pct);

    document.getElementById('kpi-cash').textContent   = 'cash: ' + fmt$(p.cash);

    const sharpe = parseFloat(m.sharpe_ratio);
    document.getElementById('kpi-sharpe').textContent = isFinite(sharpe) ? sharpe.toFixed(4) : '—';

    const ddEl = document.getElementById('kpi-drawdown');
    ddEl.textContent = fmtPct(m.max_drawdown_pct);
    ddEl.className   = 'value ' + (parseFloat(m.max_drawdown_pct) > 0 ? 'neg' : 'neu');

    document.getElementById('kpi-winrate').textContent = parseFloat(m.win_rate_pct).toFixed(1) + '%';
    document.getElementById('kpi-trades').textContent  = p.total_trades + ' trades (' + p.sell_trades + ' sells)';
    document.getElementById('kpi-remaining').textContent = fmtMin(data.remaining_minutes);
    document.getElementById('kpi-tick').textContent    = data.tick;
    document.getElementById('kpi-strategy').textContent = data.strategy;

    const elapsed = parseFloat(data.elapsed_minutes) || 0;
    const total   = parseFloat(data.duration_minutes) || 1;
    document.getElementById('time-bar').style.width = Math.min(100, elapsed / total * 100) + '%';

    // Equity curve (only update if chart loaded successfully)
    if (equityChart) {
      const curve       = data.equity_curve || [];
      const tick        = parseFloat(data.tick) || 1;
      const elapsed     = parseFloat(data.elapsed_minutes) || 0;
      const minPerTick  = elapsed / tick;
      const startCash   = parseFloat(data.portfolio?.starting_cash) || 0;

      equityChart.data.labels              = curve.map((_, i) => '+' + Math.round(i * minPerTick) + 'm');
      equityChart.data.datasets[0].data    = curve;
      equityChart.data.datasets[1].data    = curve.map(() => startCash);
      equityChart.update('none');
    }

    // Market signals
    const sigBody = document.getElementById('signals-body');
    if (data.market_signals && data.market_signals.length) {
      sigBody.innerHTML = data.market_signals.map(s => `
        <tr>
          <td style="overflow:hidden">
            <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                 title="${s.question || ''}">${(s.slug || '').replace(/-/g,' ')}</div>
            <div class="reason-text">${s.reason || ''}</div>
          </td>
          <td>${parseFloat(s.price).toFixed(4)}</td>
          <td><span class="badge ${badgeClass(s.signal)}">${s.signal}</span></td>
          <td style="color:var(--muted)">${s.signal === 'HOLD' ? '—' : (parseFloat(s.confidence) * 100).toFixed(0) + '%'}</td>
        </tr>`).join('');
    } else {
      sigBody.innerHTML = '<tr><td colspan="4" class="no-data">waiting for first tick…</td></tr>';
    }

    // Open positions
    const posBody    = document.getElementById('positions-body');
    const totalValue = parseFloat(data.portfolio?.total_value) || 1;
    if (data.positions && data.positions.length) {
      posBody.innerHTML = data.positions.map(pos => {
        const posValue = parseFloat(pos.shares) * parseFloat(pos.current_price);
        const pct      = (posValue / totalValue * 100).toFixed(1) + '%';
        return `<tr>
          <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="${pos.market_slug || ''}">${(pos.market_slug || '').replace(/-/g,' ')}</td>
          <td>${parseFloat(pos.shares).toFixed(0)}</td>
          <td>${parseFloat(pos.avg_cost).toFixed(4)}</td>
          <td>${parseFloat(pos.current_price).toFixed(4)}</td>
          <td class="${colorClass(pos.unrealised_pnl)}">${parseFloat(pos.unrealised_pnl) >= 0 ? '+' : ''}${parseFloat(pos.unrealised_pnl).toFixed(2)}</td>
          <td style="color:var(--muted)">${pct}</td>
        </tr>`;
      }).join('');
    } else {
      posBody.innerHTML = '<tr><td colspan="6" class="no-data">no open positions</td></tr>';
    }

    // Recent trades
    const trBody = document.getElementById('trades-body');
    if (data.recent_trades && data.recent_trades.length) {
      trBody.innerHTML = data.recent_trades.map(t => `
        <tr>
          <td>${timeLabel(t.timestamp)}</td>
          <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="${t.market_slug || ''}">${(t.market_slug || '').replace(/-/g,' ')}</td>
          <td><span class="badge ${badgeClass(t.action)}">${t.action}</span></td>
          <td>${parseFloat(t.price).toFixed(4)}</td>
          <td class="${colorClass(t.pnl)}">${t.action === 'BUY' ? '—' : ((parseFloat(t.pnl) >= 0 ? '+' : '') + parseFloat(t.pnl).toFixed(2))}</td>
        </tr>`).join('');
    } else {
      trBody.innerHTML = '<tr><td colspan="5" class="no-data">no trades yet</td></tr>';
    }

    lastSuccessfulFetch = Date.now();

    // Header meta: strategy + session start
    const metaEl = document.getElementById('header-meta');
    if (metaEl && data.strategy) {
      const startStr = data.session_start ? timeLabel(data.session_start) : '—';
      metaEl.textContent = data.strategy + '  ·  started ' + startStr;
    }

    // Live status: check updated_at age against stale threshold
    try {
      const updStr = String(data.updated_at || '').replace(/(\.\d{3})\d+/, '$1');
      const updAt  = new Date(updStr.endsWith('Z') ? updStr : updStr + 'Z');
      setLiveStatus((Date.now() - updAt.getTime()) / 1000 < STALE_SECONDS);
    } catch { setLiveStatus(false); }

    // Status pill — use browser's local time (avoids issues with server timestamp format)
    document.getElementById('last-update').textContent =
      'tick ' + data.tick + ' · ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  } catch (e) {
    setLiveStatus(false);
    showError(e.message);
  }

  scheduleNext();
}

function scheduleNext() {
  setTimeout(refresh, 1000);
}

// Kick off the first poll immediately
refresh();
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
        _redirect_werkzeug_to_file()
        # use_reloader=False is required when running inside a thread
        app.run(host=host, port=port, use_reloader=False, threaded=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print(f"\nDashboard running at → http://{host}:{port}")
    print("Open that URL in your browser. It refreshes every second.\n")


if __name__ == "__main__":
    # Allow running standalone: python dashboard.py
    print("Starting dashboard server (standalone mode)...")
    print("Make sure the paper trader is running in another terminal.")
    _redirect_werkzeug_to_file()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
