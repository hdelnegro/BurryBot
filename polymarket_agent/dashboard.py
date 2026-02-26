"""
dashboard.py — Live web dashboard for paper trading sessions.

Serves a browser dashboard at http://localhost:5000.

Overview page (/) shows all running/recent instances as cards.
Detail page (/instance/<name>) shows the full single-instance view.

Start automatically via:
  python main.py --strategy momentum --mode paper --duration 60 --dashboard

Or standalone (if traders are already running in other terminals):
  python dashboard.py
"""

import glob
import json
import logging
import os
import re
import threading
import time

from flask import Flask, jsonify, Response, abort

from config import DATA_DIR

LOGS_DIR   = "logs"
ACCESS_LOG = os.path.join(LOGS_DIR, "dashboard_access.log")

STALE_SECONDS = 360  # same threshold as status.py

app = Flask(__name__)


def _redirect_werkzeug_to_file() -> None:
    """Send Werkzeug HTTP access logs to logs/dashboard_access.log instead of stdout."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    handler = logging.FileHandler(ACCESS_LOG)
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("werkzeug")
    logger.setLevel(logging.INFO)
    logger.handlers = [handler]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_cache(resp):
    """Apply standard cache-busting headers to a Flask response."""
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"]        = "no-cache"
    resp.headers["Expires"]       = "0"
    return resp


def _get_all_state_files():
    """
    Glob data/state_*.json and return list of (name, path) sorted by mtime desc.
    """
    pattern = os.path.join(DATA_DIR, "state_*.json")
    paths   = glob.glob(pattern)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    results = []
    for path in paths:
        basename = os.path.basename(path)          # state_foo.json
        name     = basename[len("state_"):-len(".json")]   # foo
        results.append((name, path))
    return results


def _load_state(name: str):
    """
    Read data/state_<name>.json.

    Returns (data_dict, is_live) where is_live is True if updated_at is recent.
    Returns (None, False) if file doesn't exist or is unreadable.
    """
    path = os.path.join(DATA_DIR, f"state_{name}.json")
    if not os.path.exists(path):
        return None, False
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return None, False

    is_live = False
    try:
        from datetime import datetime, timezone
        upd_str = str(data.get("updated_at", ""))
        upd_str = re.sub(r"(\.\d{3})\d+", r"\1", upd_str)
        if not upd_str.endswith("Z"):
            upd_str += "Z"
        upd_at  = datetime.fromisoformat(upd_str.replace("Z", "+00:00"))
        age     = (datetime.now(timezone.utc) - upd_at).total_seconds()
        is_live = age < STALE_SECONDS
    except Exception:
        pass

    return data, is_live


def _downsample(curve, n=50):
    """Return at most n evenly-spaced values from curve (for sparklines)."""
    if len(curve) <= n:
        return curve
    step = len(curve) / n
    return [curve[int(i * step)] for i in range(n)]


def _valid_name(name: str) -> bool:
    """Return True iff name contains only safe characters."""
    return bool(re.fullmatch(r"[a-zA-Z0-9_-]+", name))


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state():
    """Backwards-compat: tries state_default.json, falls back to single running instance."""
    default_path = os.path.join(DATA_DIR, "state_default.json")
    if os.path.exists(default_path):
        path = default_path
    else:
        # Find the single most-recently-modified state file
        files = _get_all_state_files()
        if not files:
            resp = jsonify({"error": "No state file yet — paper trader hasn't started a tick"})
            return _no_cache(resp)
        _, path = files[0]

    try:
        with open(path) as f:
            data = json.load(f)
        resp = jsonify(data)
    except Exception as e:
        resp = jsonify({"error": str(e)})
    return _no_cache(resp)


@app.route("/api/state/<name>")
def api_state_named(name: str):
    """Return full state JSON for one named instance."""
    if not _valid_name(name):
        abort(400, "Invalid instance name")

    path = os.path.join(DATA_DIR, f"state_{name}.json")
    if not os.path.exists(path):
        resp = jsonify({"error": f"No state file for instance '{name}'"})
        return _no_cache(resp)

    try:
        with open(path) as f:
            data = json.load(f)
        resp = jsonify(data)
    except Exception as e:
        resp = jsonify({"error": str(e)})
    return _no_cache(resp)


@app.route("/api/instances")
def api_instances():
    """Return JSON summary of all instances (for overview page polling)."""
    files   = _get_all_state_files()
    summary = []
    for name, _ in files:
        data, is_live = _load_state(name)
        if data is None:
            continue
        p = data.get("portfolio", {})
        m = data.get("metrics",   {})
        summary.append({
            "name":            name,
            "is_live":         is_live,
            "strategy":        data.get("strategy", name),
            "session_start":   data.get("session_start"),
            "tick":            data.get("tick", 0),
            "elapsed_minutes": data.get("elapsed_minutes", 0),
            "remaining_minutes": data.get("remaining_minutes", 0),
            "duration_minutes":  data.get("duration_minutes", 0),
            "portfolio": {
                "total_value":      p.get("total_value", 0),
                "starting_cash":    p.get("starting_cash", 1000),
                "total_return_pct": p.get("total_return_pct", 0),
                "total_trades":     p.get("total_trades", 0),
                "sell_trades":      p.get("sell_trades", 0),
            },
            "metrics": {
                "win_rate_pct": m.get("win_rate_pct", 0),
            },
            "sparkline": _downsample(data.get("equity_curve", []), 50),
        })
    resp = jsonify(summary)
    return _no_cache(resp)


# ---------------------------------------------------------------------------
# Dashboard HTML — single-instance detail view
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>BurryBot — __INSTANCE_NAME__</title>
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
    .back-link { color: var(--muted); text-decoration: none; font-size: 12px; margin-right: 12px; }
    .back-link:hover { color: var(--blue); }
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
  <div style="display:flex;align-items:center;">
    <a class="back-link" href="/">← Overview</a>
    <div>
      <h1>⚡ BurryBot — __INSTANCE_NAME__</h1>
      <div id="header-meta" style="font-size:10px;color:var(--muted);margin-top:3px;">connecting…</div>
    </div>
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
const INSTANCE_NAME = '__INSTANCE_NAME__';

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
      animation: { duration: 0 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: ctx => '$' + ctx.parsed.y.toFixed(2) } },
      },
      scales: {
        x: { ticks: { color: '#718096', font: { size: 10 } }, grid: { color: '#1e2130' } },
        y: {
          grace: '5%',
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
    const s = String(iso).replace(/(\\.\\d{3})\\d+/, '$1');
    const d = new Date(s.endsWith('Z') ? s : s + 'Z');
    if (!isFinite(d)) return iso;
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return String(iso); }
}

// ─── Live status + heartbeat ─────────────────────────────────────────────────
const STALE_SECONDS = 360;
let isLive = true;  // matches the dot's initial green DOM state; first setLiveStatus(false) will fire correctly
let heartbeat = 0;
let lastSuccessfulFetch = 0;

function setLiveStatus(live) {
  if (isLive === live) return;
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
  if (lastSuccessfulFetch > 0 && (Date.now() - lastSuccessfulFetch) / 1000 >= STALE_SECONDS) {
    setLiveStatus(false);
  }
  if (!isLive) return;
  heartbeat++;
  const hbEl = document.getElementById('hb-count');
  if (hbEl) hbEl.textContent = heartbeat;
}, 1000);

// ─── Main refresh ────────────────────────────────────────────────────────────
async function refresh() {
  let data;
  try {
    const res = await fetch('/api/state/' + INSTANCE_NAME + '?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) { setLiveStatus(false); scheduleNext(); return; }
    data = await res.json();
  } catch { setLiveStatus(false); scheduleNext(); return; }

  try {
    const p = data.portfolio;
    const m = data.metrics;

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

    if (equityChart) {
      const curve       = data.equity_curve || [];
      const tick        = parseFloat(data.tick) || 1;
      const elapsedMin  = parseFloat(data.elapsed_minutes) || 0;
      const minPerTick  = elapsedMin / tick;
      const startCash   = parseFloat(data.portfolio?.starting_cash) || 0;

      equityChart.data.labels              = curve.map((_, i) => '+' + Math.round(i * minPerTick) + 'm');
      equityChart.data.datasets[0].data    = curve;
      equityChart.data.datasets[1].data    = curve.map(() => startCash);
      equityChart.update('none');
    }

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

    const metaEl = document.getElementById('header-meta');
    if (metaEl && data.strategy) {
      const startStr = data.session_start ? timeLabel(data.session_start) : '—';
      metaEl.textContent = data.strategy + '  ·  started ' + startStr;
    }

    try {
      const updStr = String(data.updated_at || '').replace(/(\\d{3})\\d+/, '$1');
      const updAt  = new Date(updStr.endsWith('Z') ? updStr : updStr + 'Z');
      setLiveStatus((Date.now() - updAt.getTime()) / 1000 < STALE_SECONDS);
    } catch { setLiveStatus(false); }

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

refresh();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Overview page HTML
# ---------------------------------------------------------------------------

OVERVIEW_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>BurryBot — Multi-Instance Dashboard</title>
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
    #header-right { display: flex; align-items: center; gap: 16px; }
    #instance-counts { font-size: 11px; color: var(--muted); }
    #instance-counts span { color: var(--text); }
    #last-refresh { font-size: 10px; color: var(--muted); }

    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green);
           animation: pulse 2s infinite; flex-shrink: 0; display: inline-block; }
    .dot-dead { background: var(--red) !important; animation: none !important; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

    main { padding: 24px; }

    /* Empty state */
    #empty-state {
      display: none;
      text-align: center; color: var(--muted); padding: 80px 0; font-size: 14px;
    }
    #empty-state p { margin-top: 12px; font-size: 12px; }

    /* Instance grid */
    #instances-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
    }

    /* Instance card — entire card is a link */
    .instance-card {
      display: block; text-decoration: none; color: inherit;
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 10px; padding: 16px;
      transition: border-color .15s, box-shadow .15s;
    }
    .instance-card:hover { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
    .instance-card.dead  { opacity: 0.6; border-color: rgba(252,129,129,.4); }

    .card-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 10px;
    }
    .card-title {
      display: flex; align-items: center; gap: 8px;
    }
    .card-name { font-size: 14px; font-weight: 700; color: var(--text); }
    .card-arrow { color: var(--muted); font-size: 16px; }

    .card-meta { font-size: 11px; color: var(--muted); margin-bottom: 10px; }

    .card-stats {
      display: flex; justify-content: space-between;
      margin-bottom: 10px;
    }
    .card-stat-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .6px; }
    .card-stat-value { font-size: 16px; font-weight: 700; margin-top: 2px; }
    .pos  { color: var(--green); }
    .neg  { color: var(--red); }
    .neu  { color: var(--text); }

    /* Mini sparkline */
    .sparkline-wrap { position: relative; height: 80px; margin-bottom: 10px; }

    .card-footer { font-size: 11px; color: var(--muted); display: flex; justify-content: space-between; }
  </style>
</head>
<body>
<header>
  <div>
    <h1>⚡ BurryBot — Multi-Instance Dashboard</h1>
  </div>
  <div id="header-right">
    <div id="instance-counts"><span id="live-count">0</span> live / <span id="total-count">0</span> total</div>
    <div id="last-refresh">—</div>
  </div>
</header>

<main>
  <div id="empty-state">
    <div style="font-size:32px;">📭</div>
    <div>No running instances found</div>
    <p>Start a paper trader with:<br><code>python main.py --strategy momentum --mode paper --duration 60</code></p>
  </div>
  <div id="instances-grid"></div>
</main>

<script>
// ─── Helpers ────────────────────────────────────────────────────────────────
function fmt$(v)   { return '$' + parseFloat(v).toFixed(2); }
function fmtPct(v) { const n = parseFloat(v); return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
function colorClass(v) { const n = parseFloat(v); return n > 0 ? 'pos' : (n < 0 ? 'neg' : 'neu'); }
function fmtMin(m) {
  if (!isFinite(m) || m <= 0) return '0m';
  const h = Math.floor(m / 60), mm = Math.round(m % 60);
  return h > 0 ? `${h}h ${mm}m` : `${mm}m`;
}
function timeLabel(iso) {
  try {
    const s = String(iso).replace(/(\\d{3})\\d+/, '$1');
    const d = new Date(s.endsWith('Z') ? s : s + 'Z');
    if (!isFinite(d)) return iso;
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return String(iso); }
}

// ─── Per-instance state map: name → { card, chart } ─────────────────────────
const instanceMap = new Map();

function createSparkChart(canvas, data) {
  return new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: data.map((_, i) => i),
      datasets: [{
        data: data,
        borderColor: '#667eea',
        backgroundColor: 'rgba(102,126,234,0.12)',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.3,
        fill: true,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { display: false },
        y: { display: false, grace: '10%' }
      }
    }
  });
}

function createInstanceCard(inst) {
  const p   = inst.portfolio;
  const m   = inst.metrics;
  const live = inst.is_live;

  const a = document.createElement('a');
  a.className = 'instance-card' + (live ? '' : ' dead');
  a.href      = '/instance/' + inst.name;

  const startStr = inst.session_start ? timeLabel(inst.session_start) : '—';
  const retPct   = parseFloat(p.total_return_pct) || 0;
  const winRate  = parseFloat(m.win_rate_pct) || 0;
  const tickInfo = live
    ? `Tick ${inst.tick} · ${fmtMin(inst.remaining_minutes)} remaining`
    : 'Session ended';

  a.innerHTML = `
    <div class="card-header">
      <div class="card-title">
        <span class="dot ${live ? '' : 'dot-dead'}"></span>
        <span class="card-name">${inst.name}</span>
      </div>
      <span class="card-arrow">→</span>
    </div>
    <div class="card-meta">
      ${inst.strategy}  ·  started ${startStr}
    </div>
    <div class="card-stats">
      <div>
        <div class="card-stat-label">Portfolio</div>
        <div class="card-stat-value neu">${fmt$(p.total_value)}</div>
      </div>
      <div>
        <div class="card-stat-label">Return</div>
        <div class="card-stat-value ${colorClass(retPct)}" data-return>${fmtPct(retPct)}</div>
      </div>
    </div>
    <div class="sparkline-wrap"><canvas></canvas></div>
    <div class="card-footer">
      <span data-tick>${tickInfo}</span>
      <span data-trades>${p.total_trades} trades (${p.sell_trades} sells) · Win ${winRate.toFixed(1)}%</span>
    </div>
  `;

  document.getElementById('instances-grid').appendChild(a);

  // Init sparkline chart
  const canvas = a.querySelector('canvas');
  let chart = null;
  try {
    chart = createSparkChart(canvas, inst.sparkline || []);
  } catch (e) { /* Chart.js unavailable */ }

  instanceMap.set(inst.name, { card: a, chart });
}

function updateInstanceCard(inst) {
  const entry = instanceMap.get(inst.name);
  if (!entry) return;
  const { card, chart } = entry;

  const p    = inst.portfolio;
  const m    = inst.metrics;
  const live = inst.is_live;

  // Live/dead styling
  if (live) card.classList.remove('dead');
  else       card.classList.add('dead');
  const dot = card.querySelector('.dot');
  if (dot) { live ? dot.classList.remove('dot-dead') : dot.classList.add('dot-dead'); }

  // Return value
  const retEl = card.querySelector('[data-return]');
  const retPct = parseFloat(p.total_return_pct) || 0;
  if (retEl) {
    retEl.textContent = fmtPct(retPct);
    retEl.className   = 'card-stat-value ' + colorClass(retPct);
  }

  // Portfolio value
  const statVals = card.querySelectorAll('.card-stat-value');
  if (statVals[0]) statVals[0].textContent = fmt$(p.total_value);

  // Tick / remaining
  const tickEl = card.querySelector('[data-tick]');
  if (tickEl) {
    tickEl.textContent = live
      ? `Tick ${inst.tick} · ${fmtMin(inst.remaining_minutes)} remaining`
      : 'Session ended';
  }

  // Trades
  const tradesEl = card.querySelector('[data-trades]');
  const winRate  = parseFloat(m.win_rate_pct) || 0;
  if (tradesEl) {
    tradesEl.textContent = `${p.total_trades} trades (${p.sell_trades} sells) · Win ${winRate.toFixed(1)}%`;
  }

  // Sparkline
  if (chart) {
    const d = inst.sparkline || [];
    chart.data.labels           = d.map((_, i) => i);
    chart.data.datasets[0].data = d;
    chart.update('none');
  }
}

// ─── Main poll loop ──────────────────────────────────────────────────────────
async function pollInstances() {
  let instances;
  try {
    const res = await fetch('/api/instances?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) { scheduleNext(); return; }
    instances = await res.json();
  } catch { scheduleNext(); return; }

  const grid      = document.getElementById('instances-grid');
  const emptyEl   = document.getElementById('empty-state');
  const liveCountEl  = document.getElementById('live-count');
  const totalCountEl = document.getElementById('total-count');
  const lastRefEl    = document.getElementById('last-refresh');

  if (!instances.length) {
    emptyEl.style.display = 'block';
    grid.style.display    = 'none';
  } else {
    emptyEl.style.display = 'none';
    grid.style.display    = '';
  }

  let liveCount = 0;
  for (const inst of instances) {
    if (inst.is_live) liveCount++;
    if (instanceMap.has(inst.name)) {
      updateInstanceCard(inst);
    } else {
      createInstanceCard(inst);
    }
  }

  if (liveCountEl)  liveCountEl.textContent  = liveCount;
  if (totalCountEl) totalCountEl.textContent = instances.length;
  if (lastRefEl)    lastRefEl.textContent     = 'updated ' + new Date().toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' });

  scheduleNext();
}

function scheduleNext() {
  setTimeout(pollInstances, 2000);
}

pollInstances();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    resp = Response(OVERVIEW_HTML, mimetype="text/html")
    return _no_cache(resp)


@app.route("/instance/<name>")
def instance_detail(name: str):
    if not _valid_name(name):
        abort(400, "Invalid instance name")
    html = DASHBOARD_HTML.replace("__INSTANCE_NAME__", name)
    resp = Response(html, mimetype="text/html")
    return _no_cache(resp)


# ---------------------------------------------------------------------------
# Public helper: start Flask in a daemon thread (called from main.py)
# ---------------------------------------------------------------------------

def start_in_thread(host: str = "127.0.0.1", port: int = 5000) -> None:
    """
    Launch the Flask dev server in a background daemon thread.

    Being a daemon thread means it dies automatically when the main program exits —
    no need to manually shut it down.

    Raises OSError if the port is already in use.
    """
    import socket
    # Quick pre-check so callers can catch the error before starting the thread
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            raise OSError(f"Port {port} is already in use")

    def _run():
        _redirect_werkzeug_to_file()
        app.run(host=host, port=port, use_reloader=False, threaded=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # Brief pause to let Flask start up before the caller prints the banner
    time.sleep(0.3)
    print(f"\nDashboard running at → http://{host}:{port}")
    print("Open that URL in your browser. It refreshes every second.\n")


if __name__ == "__main__":
    # Allow running standalone: python dashboard.py
    print("Starting dashboard server (standalone mode)...")
    print("Make sure the paper trader is running in another terminal.")
    _redirect_werkzeug_to_file()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
