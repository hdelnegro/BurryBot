"""
shared/dashboard.py — Live web dashboard for all BurryBot paper trading sessions.

Serves a browser dashboard at http://localhost:5000.
Discovers state files from ALL *_agent/data/ directories automatically.

Overview page (/) shows all running/recent instances as cards with platform badges.
Detail page (/instance/<name>) shows the full single-instance view.

Run standalone:
  cd BurryBot
  source shared/venv/bin/activate
  python shared/dashboard.py

Or automatically via an agent's --dashboard flag:
  cd polymarket_agent && source venv/bin/activate
  python main.py --strategy momentum --mode paper --duration 60 --dashboard
"""

import glob
import json
import logging
import os
import re
import signal as _signal
import subprocess
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, redirect, request, Response, abort

# BurryBot root dir (parent of shared/)
_BURRYBOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AGENT_DIR     = os.path.join(_BURRYBOT_ROOT, 'polymarket_agent')
_PYTHON_PATH   = os.path.join(_AGENT_DIR, 'venv', 'bin', 'python')
_LOG_DIR       = os.path.join(_AGENT_DIR, 'logs')

LOGS_DIR   = os.path.join(_BURRYBOT_ROOT, "shared", "logs")
ACCESS_LOG = os.path.join(LOGS_DIR, "dashboard_access.log")

STALE_SECONDS = 360  # same threshold as status.py

app = Flask(__name__)


def _redirect_werkzeug_to_file() -> None:
    """Send Werkzeug HTTP access logs to shared/logs/dashboard_access.log."""
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
    Glob all *_agent/data/state_*.json across the BurryBot repo root.
    Returns list of (name, path) sorted by mtime desc.
    """
    pattern = os.path.join(_BURRYBOT_ROOT, "*_agent", "data", "state_*.json")
    paths   = glob.glob(pattern)
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    results = []
    for path in paths:
        basename = os.path.basename(path)                      # state_foo.json
        name     = basename[len("state_"):-len(".json")]       # foo
        results.append((name, path))
    return results


def _find_state_path(name: str):
    """
    Find the state file for a given instance name across all agent data dirs.
    Returns the path, or None if not found.
    """
    pattern = os.path.join(_BURRYBOT_ROOT, "*_agent", "data", f"state_{name}.json")
    matches = glob.glob(pattern)
    if not matches:
        return None
    # If multiple matches (shouldn't happen), prefer most recently modified
    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return matches[0]


def _load_state(name: str):
    """
    Read state_<name>.json from whichever agent's data dir contains it.

    Returns (data_dict, is_live) where is_live is True if updated_at is recent.
    Returns (None, False) if file doesn't exist or is unreadable.
    """
    path = _find_state_path(name)
    if path is None:
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
    """Backwards-compat: tries state_default.json, falls back to most recent."""
    default_path = _find_state_path("default")
    if default_path:
        path = default_path
    else:
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

    path = _find_state_path(name)
    if path is None:
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
            "platform":        data.get("platform", "polymarket"),
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
                "win_rate_pct":     m.get("win_rate_pct", 0),
                "sharpe_ratio":     m.get("sharpe_ratio", 0),
                "max_drawdown_pct": m.get("max_drawdown_pct", 0),
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
      --bg: #000000; --surface: #111111; --border: #1e1e1e;
      --text: #ffffff; --muted: #707070; --green: #4af6c3;
      --red: #ff433d; --yellow: #fb8b1e; --blue: #0068ff;
      --purple: #b794f4; --accent: #ff8c00;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }

    header {
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 14px 24px; display: flex; align-items: center; justify-content: space-between;
    }
    header h1 { font-size: 16px; color: var(--accent); letter-spacing: 2px; text-transform: uppercase; }
    .back-link { color: var(--muted); text-decoration: none; font-size: 12px; margin-right: 12px; }
    .back-link:hover { color: var(--accent); }
    #status-pill {
      display: flex; align-items: center; gap: 8px;
      background: var(--bg); border: 1px solid var(--border);
      padding: 5px 12px; border-radius: 2px; font-size: 11px; color: var(--muted);
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green);
           animation: pulse 2s infinite; flex-shrink: 0; }
    .dot-dead { background: var(--red) !important; animation: none !important; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

    main { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; }

    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 2px; padding: 14px 16px;
    }
    .card .label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .8px; margin-bottom: 8px; }
    .card .value { font-size: 22px; font-weight: 700; }
    .card .sub   { font-size: 11px; color: var(--muted); margin-top: 4px; }
    .pos  { color: var(--green); }
    .neg  { color: var(--red); }
    .neu  { color: var(--text); }

    .mid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

    .panel {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 2px; padding: 16px;
    }
    .panel h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .8px;
                color: var(--muted); margin-bottom: 12px; }

    #chart-wrap { position: relative; height: 220px; }

    table { width: 100%; border-collapse: collapse; }
    th { font-size: 10px; text-transform: uppercase; letter-spacing: .6px;
         color: var(--muted); padding: 4px 8px; text-align: left;
         border-bottom: 1px solid var(--border); }
    td { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; vertical-align: top; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,.03); }

    .badge {
      display: inline-block; padding: 2px 7px; border-radius: 4px;
      font-size: 10px; font-weight: 700; letter-spacing: .5px;
    }
    .badge-buy  { background: rgba(74,246,195,.15); color: var(--green); }
    .badge-sell { background: rgba(255,67,61,.15);  color: var(--red); }
    .badge-hold { background: rgba(112,112,112,.15); color: var(--muted); }
    .badge-yes  { background: var(--green);  color: #000; }
    .badge-no   { background: var(--accent); color: #000; }

    .reason-text { color: var(--muted); font-size: 11px; margin-top: 2px; }

    #last-update { font-size: 10px; color: var(--muted); }

    .bottom { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

    .no-data { color: var(--muted); font-size: 12px; padding: 12px 0; text-align: center; }

    .progress-wrap { background: var(--bg); border-radius: 4px; height: 5px; margin-top: 8px; overflow: hidden; }
    .progress-bar  { height: 100%; background: var(--accent); border-radius: 4px; transition: width .5s; }

    #session-ended-banner {
      display: none;
      background: rgba(255,67,61,.10); border-bottom: 1px solid rgba(255,67,61,.35);
      color: var(--red); padding: 8px 24px; font-size: 12px; text-align: center;
      letter-spacing: .4px;
    }
  </style>
</head>
<body>
<div id="session-ended-banner">&#9888; Trader session ended — data is frozen</div>
<header>
  <div style="display:flex;align-items:center;">
    <a class="back-link" href="/">&#8592; Overview</a>
    <div>
      <h1>&#9889; BurryBot — __INSTANCE_NAME__</h1>
      <div id="header-meta" style="font-size:10px;color:var(--muted);margin-top:3px;">connecting…</div>
    </div>
  </div>
  <div style="display:flex;align-items:center;gap:12px;">
    <button id="finish-btn" style="display:none;background:none;border:1px solid #ff433d;color:#ff433d;cursor:pointer;font-family:inherit;font-size:11px;font-weight:700;letter-spacing:1px;padding:5px 12px;border-radius:2px;" onclick="finishSession()">FINISH SESSION</button>
    <div id="status-pill">
      <span id="last-update">connecting…</span>
      <span style="color:var(--border);margin:0 4px;">|</span>
      <span style="display:flex;align-items:center;gap:5px;font-size:10px;color:var(--muted);"><span class="dot" id="live-dot"></span>hb:<span id="hb-count">0</span></span>
    </div>
  </div>
</header>

<main>
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

  <div class="mid">
    <div class="panel">
      <h2>Equity Curve</h2>
      <div id="chart-wrap"><canvas id="equity-chart"></canvas></div>
    </div>
    <div class="panel" style="overflow:auto; max-height:290px;">
      <h2>Market Signals (latest)</h2>
      <table style="table-layout:fixed">
        <thead><tr><th>Market</th><th style="width:70px">Price</th><th style="width:42px">Side</th><th style="width:64px">Signal</th><th style="width:44px">Conf</th></tr></thead>
        <tbody id="signals-body"><tr><td colspan="5" class="no-data">waiting for first tick…</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="bottom">
    <div class="panel" style="overflow:auto;">
      <h2>Open Positions</h2>
      <table>
        <thead><tr><th>Market</th><th>Side</th><th>Shares</th><th>Avg $</th><th>Now $</th><th>PnL</th><th>% Portf</th></tr></thead>
        <tbody id="positions-body"><tr><td colspan="7" class="no-data">no open positions</td></tr></tbody>
      </table>
    </div>
    <div class="panel" style="overflow:auto;">
      <h2>Recent Trades</h2>
      <table>
        <thead><tr><th>Time</th><th>Market</th><th>Action</th><th>Side</th><th>Price</th><th>PnL</th></tr></thead>
        <tbody id="trades-body"><tr><td colspan="6" class="no-data">no trades yet</td></tr></tbody>
      </table>
    </div>
  </div>
</main>

<script>
const INSTANCE_NAME = '__INSTANCE_NAME__';

function showError(msg) {
  let el = document.getElementById('js-error-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'js-error-banner';
    el.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#e53e3e;color:#fff;' +
                       'padding:6px 14px;font-size:12px;z-index:9999;font-family:monospace;';
    document.body.prepend(el);
  }
  el.textContent = '&#9888; JS error: ' + msg;
}

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
          borderColor: '#ff8c00',
          backgroundColor: 'rgba(255,140,0,0.10)',
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
          ticks: { color: '#707070', font: { size: 10 }, callback: v => '$' + v.toFixed(2) },
          grid: { color: '#1a1a1a' }
        }
      }
    }
  });
} catch (e) {
  document.getElementById('chart-wrap').innerHTML =
    '<div style="color:var(--muted);text-align:center;padding:60px 0;font-size:12px;">' +
    'Chart unavailable (Chart.js failed to load)</div>';
}

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
    const s = String(iso).replace(/(\\..{3})\\d+/, '$1');
    const d = new Date(s.endsWith('Z') ? s : s + 'Z');
    if (!isFinite(d)) return iso;
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return String(iso); }
}

const STALE_SECONDS = 360;
let isLive = null;  // null so first setLiveStatus() call always runs
let heartbeat = 0;
let lastSuccessfulFetch = 0;

function setLiveStatus(live) {
  if (isLive === live) return;
  isLive = live;
  const dot       = document.getElementById('live-dot');
  const banner    = document.getElementById('session-ended-banner');
  const finishBtn = document.getElementById('finish-btn');
  if (live) {
    dot?.classList.remove('dot-dead');
    if (banner)    banner.style.display    = 'none';
    if (finishBtn) finishBtn.style.display = '';
  } else {
    dot?.classList.add('dot-dead');
    if (banner)    banner.style.display    = 'block';
    if (finishBtn) finishBtn.style.display = 'none';
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
      sigBody.innerHTML = data.market_signals.map(s => {
        const sSide = (s.outcome || 'YES').toUpperCase();
        return `<tr>
          <td style="overflow:hidden">
            <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                 title="${s.question || ''}">${(s.slug || '').replace(/-/g,' ')}</div>
            <div class="reason-text">${s.reason || ''}</div>
          </td>
          <td>${parseFloat(s.price).toFixed(4)}</td>
          <td><span class="badge badge-${sSide.toLowerCase()}">${sSide}</span></td>
          <td><span class="badge ${badgeClass(s.signal)}">${s.signal}</span></td>
          <td style="color:var(--muted)">${s.signal === 'HOLD' ? '—' : (parseFloat(s.confidence) * 100).toFixed(0) + '%'}</td>
        </tr>`;
      }).join('');
    } else {
      sigBody.innerHTML = '<tr><td colspan="5" class="no-data">waiting for first tick…</td></tr>';
    }

    const posBody    = document.getElementById('positions-body');
    const totalValue = parseFloat(data.portfolio?.total_value) || 1;
    if (data.positions && data.positions.length) {
      posBody.innerHTML = data.positions.map(pos => {
        const posValue = parseFloat(pos.shares) * parseFloat(pos.current_price);
        const pct      = (posValue / totalValue * 100).toFixed(1) + '%';
        const side = (pos.outcome || 'YES').toUpperCase();
        return `<tr>
          <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="${pos.market_slug || ''}">${(pos.market_slug || '').replace(/-/g,' ')}</td>
          <td><span class="badge badge-${side.toLowerCase()}">${side}</span></td>
          <td>${parseFloat(pos.shares).toFixed(0)}</td>
          <td>${parseFloat(pos.avg_cost).toFixed(4)}</td>
          <td>${parseFloat(pos.current_price).toFixed(4)}</td>
          <td class="${colorClass(pos.unrealised_pnl)}">${parseFloat(pos.unrealised_pnl) >= 0 ? '+' : ''}${parseFloat(pos.unrealised_pnl).toFixed(2)}</td>
          <td style="color:var(--muted)">${pct}</td>
        </tr>`;
      }).join('');
    } else {
      posBody.innerHTML = '<tr><td colspan="7" class="no-data">no open positions</td></tr>';
    }

    const trBody = document.getElementById('trades-body');
    if (data.recent_trades && data.recent_trades.length) {
      trBody.innerHTML = data.recent_trades.map(t => {
        const tSide = (t.outcome || 'YES').toUpperCase();
        return `<tr>
          <td>${timeLabel(t.timestamp)}</td>
          <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="${t.market_slug || ''}">${(t.market_slug || '').replace(/-/g,' ')}</td>
          <td><span class="badge ${badgeClass(t.action)}">${t.action}</span></td>
          <td><span class="badge badge-${tSide.toLowerCase()}">${tSide}</span></td>
          <td>${parseFloat(t.price).toFixed(4)}</td>
          <td class="${colorClass(t.pnl)}">${t.action === 'BUY' ? '—' : ((parseFloat(t.pnl) >= 0 ? '+' : '') + parseFloat(t.pnl).toFixed(2))}</td>
        </tr>`;
      }).join('');
    } else {
      trBody.innerHTML = '<tr><td colspan="6" class="no-data">no trades yet</td></tr>';
    }

    lastSuccessfulFetch = Date.now();

    const metaEl = document.getElementById('header-meta');
    if (metaEl && data.strategy) {
      const startStr   = data.session_start ? timeLabel(data.session_start) : '—';
      const platformStr = data.platform ? ` · ${data.platform}` : '';
      metaEl.textContent = data.strategy + '  ·  started ' + startStr + platformStr;
    }

    try {
      const updAt = new Date(String(data.updated_at || '').substring(0, 19) + 'Z');
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

async function finishSession() {
  const finishBtn = document.getElementById('finish-btn');
  if (finishBtn) { finishBtn.disabled = true; finishBtn.textContent = 'Sending…'; }
  try {
    const resp = await fetch('/api/finish/' + INSTANCE_NAME, { method: 'POST' });
    if (resp.ok) {
      if (finishBtn) finishBtn.textContent = 'Shutting down…';
    } else {
      if (finishBtn) { finishBtn.disabled = false; finishBtn.textContent = 'FINISH SESSION'; }
    }
  } catch (e) {
    if (finishBtn) { finishBtn.disabled = false; finishBtn.textContent = 'FINISH SESSION'; }
  }
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
      --bg: #000000; --surface: #111111; --border: #1e1e1e;
      --text: #ffffff; --muted: #707070; --green: #4af6c3;
      --red: #ff433d; --yellow: #fb8b1e; --blue: #0068ff;
      --purple: #b794f4; --accent: #ff8c00;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }

    header {
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 14px 24px; display: flex; align-items: center; justify-content: space-between;
    }
    header h1 { font-size: 16px; color: var(--accent); letter-spacing: 2px; text-transform: uppercase; }
    #header-right { display: flex; align-items: center; gap: 16px; }
    #instance-counts { font-size: 11px; color: var(--muted); }
    #instance-counts span { color: var(--text); }
    #last-refresh { font-size: 10px; color: var(--muted); }

    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green);
           animation: pulse 2s infinite; flex-shrink: 0; display: inline-block; }
    .dot-dead { background: var(--red) !important; animation: none !important; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

    main { padding: 24px; }

    #empty-state {
      display: none;
      text-align: center; color: var(--muted); padding: 80px 0; font-size: 14px;
    }
    #empty-state p { margin-top: 12px; font-size: 12px; }

    .instances-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 16px;
    }

    .section-wrap { margin-bottom: 36px; }
    .section-header {
      font-size: 10px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase;
      color: var(--accent); border-bottom: 1px solid var(--border);
      padding-bottom: 8px; margin-bottom: 16px;
    }

    .instance-card {
      display: block; text-decoration: none; color: inherit;
      background: #ffffff; border: 1px solid #e8e8e8;
      border-radius: 2px; padding: 16px;
      transition: border-color .15s, box-shadow .15s;
    }
    .instance-card:hover { border-color: var(--accent); box-shadow: 0 2px 8px rgba(255,140,0,0.15); }
    .instance-card.live  { border-left: 3px solid var(--accent); }
    .instance-card.dead  { opacity: 0.6; border-color: #e0e0e0; }

    .card-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 8px;
    }
    .card-title { display: flex; align-items: center; gap: 8px; }
    .card-name  { font-size: 14px; font-weight: 700; color: #111111; }
    .card-arrow { color: #aaaaaa; font-size: 16px; }

    /* Platform badge pill */
    .badge-platform {
      display: inline-block; padding: 2px 8px; border-radius: 2px;
      font-size: 9px; font-weight: 700; letter-spacing: .6px; text-transform: uppercase;
    }
    .badge-platform-polymarket { background: rgba(0,104,255,.1); color: #0068ff; }
    .badge-platform-kalshi     { background: rgba(74,246,195,.15); color: #1aad7a; }
    .badge-platform-unknown    { background: rgba(0,0,0,.07); color: #777777; }

    .card-meta { font-size: 11px; color: #888888; margin-bottom: 10px; }

    .card-stats {
      display: grid; grid-template-columns: repeat(4, 1fr);
      gap: 4px; margin-bottom: 10px;
    }
    .card-stat-label { font-size: 9px; color: #999999; text-transform: uppercase; letter-spacing: .6px; }
    .card-stat-value { font-size: 13px; font-weight: 700; margin-top: 2px; }
    .pos  { color: #1aad7a; }
    .neg  { color: var(--red); }
    .neu  { color: #111111; }

    .sparkline-wrap { position: relative; height: 70px; margin-bottom: 10px; }

    .card-footer { font-size: 11px; color: #888888; display: flex; flex-direction: column; gap: 3px; }
    .btn-delete {
      background: none; border: 1px solid #ffcccc; color: #aaaaaa;
      cursor: pointer; font-size: 11px; padding: 2px 8px; border-radius: 2px; line-height: 1.4;
      white-space: nowrap; flex-shrink: 0;
    }
    .btn-delete:hover { background: rgba(255,67,61,.08); color: var(--red); border-color: var(--red); }
    #sort-select {
      background: #111; color: #aaaaaa; border: 1px solid #333;
      padding: 4px 8px; font-size: 11px; font-family: inherit;
      border-radius: 2px; cursor: pointer;
    }
    #sort-select:focus { outline: none; border-color: var(--accent); }
    #launch-btn {
      background: var(--accent); color: #000; border: none;
      padding: 6px 14px; font-size: 11px; font-family: inherit;
      font-weight: 700; letter-spacing: 1px; cursor: pointer; border-radius: 2px;
    }
    #launch-btn:hover { background: #ffaa33; }
    .btn-delete-all {
      background: none; border: 1px solid #444; color: #888;
      cursor: pointer; font-size: 10px; font-family: inherit;
      padding: 2px 8px; border-radius: 2px; letter-spacing: .5px;
    }
    .btn-delete-all:hover { border-color: var(--red); color: var(--red); }
  </style>
</head>
<body>
<header>
  <div>
    <h1>&#9889; BurryBot — Multi-Instance Dashboard</h1>
  </div>
  <div id="header-right">
    <div id="instance-counts"><span id="live-count">0</span> live / <span id="total-count">0</span> total</div>
    <div id="last-refresh">—</div>
    <select id="sort-select">
      <option value="return">Sort: Return</option>
      <option value="portfolio">Sort: Portfolio</option>
      <option value="created">Sort: Created</option>
      <option value="name">Sort: Name</option>
    </select>
    <button id="launch-btn" onclick="location.href='/launch'">+ LAUNCH AGENT</button>
  </div>
</header>

<main>
  <div id="empty-state">
    <div style="font-size:32px;">&#128077;</div>
    <div>No running instances found</div>
    <p>Start a paper trader with:<br><code>python main.py --strategy momentum --mode paper --duration 60</code></p>
  </div>
  <div id="active-section" class="section-wrap" style="display:none">
    <div class="section-header">Active Sessions</div>
    <div id="active-grid" class="instances-grid"></div>
  </div>
  <div id="expired-section" class="section-wrap" style="display:none">
    <div class="section-header" style="display:flex;align-items:center;justify-content:space-between;">
      <span>Expired Sessions</span>
      <button class="btn-delete-all" onclick="deleteAllExpired()">DELETE ALL</button>
    </div>
    <div id="expired-grid" class="instances-grid"></div>
  </div>
</main>

<script>
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
    const s = String(iso).replace(/(\\..{3})\\d+/, '$1');
    const d = new Date(s.endsWith('Z') ? s : s + 'Z');
    if (!isFinite(d)) return iso;
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return String(iso); }
}
function platformBadgeClass(platform) {
  const p = (platform || 'unknown').toLowerCase();
  if (p === 'polymarket') return 'badge-platform badge-platform-polymarket';
  if (p === 'kalshi')     return 'badge-platform badge-platform-kalshi';
  return 'badge-platform badge-platform-unknown';
}

const instanceMap = new Map();

function createSparkChart(canvas, data) {
  return new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: data.map((_, i) => i),
      datasets: [{
        data: data,
        borderColor: '#ff8c00',
        backgroundColor: 'rgba(255,140,0,0.10)',
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

function createInstanceCard(inst, gridId) {
  const p    = inst.portfolio;
  const m    = inst.metrics;
  const live = inst.is_live;

  const a = document.createElement('a');
  a.className = 'instance-card' + (live ? ' live' : ' dead');
  a.href      = '/instance/' + inst.name;

  const startStr = inst.session_start ? timeLabel(inst.session_start) : '—';
  const retPct   = parseFloat(p.total_return_pct) || 0;
  const winRate  = parseFloat(m.win_rate_pct) || 0;
  const sharpe   = parseFloat(m.sharpe_ratio) || 0;
  const maxdd    = parseFloat(m.max_drawdown_pct) || 0;
  const tickInfo = live
    ? `Tick ${inst.tick} · ${fmtMin(inst.remaining_minutes)} remaining`
    : 'Session ended';
  const platform = inst.platform || 'polymarket';

  a.innerHTML = `
    <div class="card-header">
      <div class="card-title">
        <span class="dot ${live ? '' : 'dot-dead'}"></span>
        <span class="card-name">${inst.name}</span>
        <span class="${platformBadgeClass(platform)}">${platform}</span>
      </div>
      <div style="display:flex;align-items:center;gap:8px;">
        ${!live ? `<button class="btn-delete" onclick="deleteCard(event,'${inst.name}')">Delete</button>` : ''}
        <span class="card-arrow">&#8594;</span>
      </div>
    </div>
    <div class="card-meta">${inst.strategy}  ·  started ${startStr}</div>
    <div class="card-stats">
      <div>
        <div class="card-stat-label">Portfolio</div>
        <div class="card-stat-value neu" data-portfolio>${fmt$(p.total_value)}</div>
      </div>
      <div>
        <div class="card-stat-label">Return</div>
        <div class="card-stat-value ${colorClass(retPct)}" data-return>${fmtPct(retPct)}</div>
      </div>
      <div>
        <div class="card-stat-label">Sharpe</div>
        <div class="card-stat-value neu" data-sharpe>${isFinite(sharpe) ? sharpe.toFixed(2) : '—'}</div>
      </div>
      <div>
        <div class="card-stat-label">Drawdown</div>
        <div class="card-stat-value ${maxdd > 0 ? 'neg' : 'neu'}" data-drawdown>${fmtPct(maxdd)}</div>
      </div>
    </div>
    <div class="sparkline-wrap"><canvas></canvas></div>
    <div class="card-footer">
      <span data-tick>${tickInfo}</span>
      <span data-trades>${p.total_trades} trades (${p.sell_trades} sells) · Win ${winRate.toFixed(1)}%</span>
    </div>
  `;

  document.getElementById(gridId).appendChild(a);

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

  if (live) { card.classList.add('live'); card.classList.remove('dead'); }
  else       { card.classList.remove('live'); card.classList.add('dead'); }
  const dot = card.querySelector('.dot');
  if (dot) { live ? dot.classList.remove('dot-dead') : dot.classList.add('dot-dead'); }

  const retPct = parseFloat(p.total_return_pct) || 0;
  const retEl  = card.querySelector('[data-return]');
  if (retEl) { retEl.textContent = fmtPct(retPct); retEl.className = 'card-stat-value ' + colorClass(retPct); }

  const portEl = card.querySelector('[data-portfolio]');
  if (portEl) portEl.textContent = fmt$(p.total_value);

  const sharpe  = parseFloat(m.sharpe_ratio) || 0;
  const sharpeEl = card.querySelector('[data-sharpe]');
  if (sharpeEl) sharpeEl.textContent = isFinite(sharpe) ? sharpe.toFixed(2) : '—';

  const maxdd   = parseFloat(m.max_drawdown_pct) || 0;
  const ddEl    = card.querySelector('[data-drawdown]');
  if (ddEl) { ddEl.textContent = fmtPct(maxdd); ddEl.className = 'card-stat-value ' + (maxdd > 0 ? 'neg' : 'neu'); }

  const tickEl = card.querySelector('[data-tick]');
  if (tickEl) tickEl.textContent = live ? `Tick ${inst.tick} · ${fmtMin(inst.remaining_minutes)} remaining` : 'Session ended';

  const winRate  = parseFloat(m.win_rate_pct) || 0;
  const tradesEl = card.querySelector('[data-trades]');
  if (tradesEl) tradesEl.textContent = `${p.total_trades} trades (${p.sell_trades} sells) · Win ${winRate.toFixed(1)}%`;

  if (chart) {
    const d = inst.sparkline || [];
    chart.data.labels           = d.map((_, i) => i);
    chart.data.datasets[0].data = d;
    chart.update('none');
  }
}

async function pollInstances() {
  let instances;
  try {
    const res = await fetch('/api/instances?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) { scheduleNext(); return; }
    instances = await res.json();
  } catch { scheduleNext(); return; }

  const emptyEl      = document.getElementById('empty-state');
  const activeSec    = document.getElementById('active-section');
  const expiredSec   = document.getElementById('expired-section');
  const activeGrid   = document.getElementById('active-grid');
  const liveCountEl  = document.getElementById('live-count');
  const totalCountEl = document.getElementById('total-count');
  const lastRefEl    = document.getElementById('last-refresh');

  const liveInsts = instances.filter(i => i.is_live);
  const deadInsts = instances.filter(i => !i.is_live);

  // Sort active sessions per combo box selection
  const sortBy = (document.getElementById('sort-select') || {}).value || 'return';
  if (sortBy === 'portfolio') {
    liveInsts.sort((a, b) => (parseFloat(b.portfolio.total_value)||0) - (parseFloat(a.portfolio.total_value)||0));
  } else if (sortBy === 'created') {
    liveInsts.sort((a, b) => (a.session_start||'').localeCompare(b.session_start||''));
  } else if (sortBy === 'name') {
    liveInsts.sort((a, b) => (a.name||'').localeCompare(b.name||''));
  } else {
    liveInsts.sort((a, b) => (parseFloat(b.portfolio.total_return_pct)||0) - (parseFloat(a.portfolio.total_return_pct)||0));
  }

  // Create or update all cards
  for (const inst of instances) {
    const gridId = inst.is_live ? 'active-grid' : 'expired-grid';
    if (instanceMap.has(inst.name)) {
      updateInstanceCard(inst);
      // Move card to correct grid if live status changed
      const card = instanceMap.get(inst.name).card;
      const targetGrid = document.getElementById(gridId);
      if (card.parentElement !== targetGrid) targetGrid.appendChild(card);
    } else {
      createInstanceCard(inst, gridId);
    }
  }

  // Re-order active grid by return rank (appendChild moves existing elements)
  liveInsts.forEach(inst => {
    const entry = instanceMap.get(inst.name);
    if (entry) activeGrid.appendChild(entry.card);
  });

  emptyEl.style.display    = instances.length ? 'none' : 'block';
  activeSec.style.display  = liveInsts.length  ? '' : 'none';
  expiredSec.style.display = deadInsts.length  ? '' : 'none';

  if (liveCountEl)  liveCountEl.textContent  = liveInsts.length;
  if (totalCountEl) totalCountEl.textContent = instances.length;
  if (lastRefEl)    lastRefEl.textContent     = 'updated ' + new Date().toLocaleTimeString([], { hour:'2-digit', minute:'2-digit', second:'2-digit' });

  scheduleNext();
}

function scheduleNext() {
  setTimeout(pollInstances, 2000);
}

async function deleteCard(event, name) {
  event.preventDefault();
  event.stopPropagation();
  try {
    const res = await fetch('/api/delete/' + name, { method: 'POST' });
    if (res.ok) {
      const entry = instanceMap.get(name);
      if (entry) entry.card.remove();
      instanceMap.delete(name);
    }
  } catch (e) { /* ignore */ }
}

async function deleteAllExpired() {
  if (!confirm('Delete all expired sessions?')) return;
  try {
    const res = await fetch('/api/delete-all-expired', { method: 'POST' });
    if (res.ok) {
      // Remove all dead cards immediately
      for (const [name, entry] of instanceMap.entries()) {
        if (entry.card.classList.contains('dead')) {
          entry.card.remove();
          instanceMap.delete(name);
        }
      }
    }
  } catch (e) { /* ignore */ }
}

pollInstances();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Launch Agent page HTML
# ---------------------------------------------------------------------------

LAUNCH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>BurryBot — Launch Agent</title>
  <style>
    :root {
      --bg: #000000; --surface: #111111; --border: #1e1e1e;
      --text: #ffffff; --muted: #707070; --green: #4af6c3;
      --red: #ff433d; --accent: #ff8c00;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: var(--bg); color: var(--text); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }
    header {
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 14px 24px; display: flex; align-items: center;
    }
    header h1 { font-size: 16px; color: var(--accent); letter-spacing: 2px; text-transform: uppercase; }
    .back-link { color: var(--muted); text-decoration: none; font-size: 12px; margin-right: 16px; }
    .back-link:hover { color: var(--accent); }
    main { padding: 32px 24px; max-width: 540px; }
    .error-banner {
      background: rgba(255,67,61,.12); border: 1px solid rgba(255,67,61,.4);
      color: var(--red); padding: 8px 14px; margin-bottom: 20px;
      border-radius: 2px; font-size: 12px;
    }
    .form-group { margin-bottom: 18px; }
    label { display: block; font-size: 10px; color: var(--muted); text-transform: uppercase;
            letter-spacing: .8px; margin-bottom: 6px; }
    select, input[type="text"], input[type="datetime-local"] {
      width: 100%; background: var(--surface); color: var(--text);
      border: 1px solid var(--border); padding: 8px 10px;
      font-size: 13px; font-family: inherit; border-radius: 2px;
    }
    select:focus, input:focus { outline: none; border-color: var(--accent); }
    .checkbox-row { display: flex; align-items: center; gap: 8px; }
    .checkbox-row input[type="checkbox"] { width: auto; }
    .hidden { display: none; }
    .btn-submit {
      width: 100%; background: var(--accent); color: #000; border: none;
      padding: 10px; font-size: 13px; font-family: inherit;
      font-weight: 700; letter-spacing: 1px; cursor: pointer; border-radius: 2px;
      margin-top: 8px;
    }
    .btn-submit:hover { background: #ffaa33; }
    .cancel-link { display: block; text-align: center; margin-top: 14px;
                   color: var(--muted); text-decoration: none; font-size: 12px; }
    .cancel-link:hover { color: var(--text); }
  </style>
</head>
<body>
<header>
  <a class="back-link" href="/">&#8592; Overview</a>
  <h1>&#9889; BurryBot &#8212; Launch Agent</h1>
</header>
<main>
  __ERROR_BANNER__
  <form method="POST" action="/api/launch">
    <div class="form-group">
      <label>Strategy</label>
      <select name="strategy">
        <option value="momentum">momentum</option>
        <option value="mean_reversion">mean_reversion</option>
        <option value="rsi">rsi</option>
        <option value="random_baseline">random_baseline</option>
      </select>
    </div>
    <div class="form-group">
      <label>Mode</label>
      <select name="mode" id="mode-select" onchange="onModeChange()">
        <option value="paper">paper</option>
        <option value="backtest">backtest</option>
      </select>
    </div>
    <div class="form-group">
      <label>Markets</label>
      <select name="markets">
        <option value="5">5</option>
        <option value="10">10</option>
        <option value="20">20</option>
        <option value="30">30</option>
        <option value="50">50</option>
      </select>
    </div>
    <div class="form-group">
      <label>Starting Cash (USDC)</label>
      <select name="cash">
        <option value="500">500</option>
        <option value="1000" selected>1000</option>
        <option value="2000">2000</option>
        <option value="5000">5000</option>
      </select>
    </div>
    <div class="form-group paper-only" id="group-end-time">
      <label>Session End Time</label>
      <input type="datetime-local" name="end_time" id="end-time-input"/>
    </div>
    <div class="form-group paper-only" id="group-name">
      <label>Instance Name (optional)</label>
      <input type="text" name="name" placeholder="auto" maxlength="40"/>
    </div>
    <div class="form-group paper-only" id="group-market-type">
      <label>Market Type</label>
      <select name="market_type">
        <option value="standard">standard</option>
        <option value="5min">5min (BTC up/down)</option>
      </select>
    </div>
    <div class="form-group backtest-only hidden" id="group-no-fetch">
      <label>&nbsp;</label>
      <div class="checkbox-row">
        <input type="checkbox" name="no_fetch" id="no-fetch-cb"/>
        <label for="no-fetch-cb" style="text-transform:none;letter-spacing:0;font-size:12px;">Use cached data only (--no-fetch)</label>
      </div>
    </div>
    <button type="submit" class="btn-submit">LAUNCH</button>
  </form>
  <a class="cancel-link" href="/">CANCEL</a>
</main>
<script>
function onModeChange() {
  const mode = document.getElementById('mode-select').value;
  document.querySelectorAll('.paper-only').forEach(el => el.classList.toggle('hidden', mode !== 'paper'));
  document.querySelectorAll('.backtest-only').forEach(el => el.classList.toggle('hidden', mode !== 'backtest'));
}
// Pre-fill end time with +1 hour from now
(function() {
  const inp = document.getElementById('end-time-input');
  if (!inp) return;
  const d = new Date(Date.now() + 3600000);
  const pad = n => String(n).padStart(2, '0');
  inp.value = d.getFullYear() + '-' + pad(d.getMonth()+1) + '-' + pad(d.getDate()) +
              'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
})();
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


@app.route("/api/delete/<name>", methods=["POST"])
def api_delete(name: str):
    """Delete the state file for a named instance."""
    if not _valid_name(name):
        abort(400, "Invalid instance name")
    path = _find_state_path(name)
    if path is None:
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        os.remove(path)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/delete-all-expired", methods=["POST"])
def api_delete_all_expired():
    """Delete state files for all expired (stale) instances."""
    deleted = 0
    for name, path in _get_all_state_files():
        _, is_live = _load_state(name)
        if not is_live:
            try:
                os.remove(path)
                deleted += 1
            except OSError:
                pass
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/launch")
def launch_page():
    """Launch Agent form page."""
    error = request.args.get("error", "")
    if error == "invalid":
        banner = '<div class="error-banner">Invalid parameters. Please check your inputs.</div>'
    elif error == "past":
        banner = '<div class="error-banner">End time must be in the future.</div>'
    else:
        banner = ""
    html = LAUNCH_HTML.replace("__ERROR_BANNER__", banner)
    resp = Response(html, mimetype="text/html")
    return _no_cache(resp)


@app.route("/api/launch", methods=["POST"])
def api_launch():
    """Validate form and spawn a detached agent subprocess."""
    strategy     = request.form.get("strategy", "")
    mode         = request.form.get("mode", "paper")
    markets      = request.form.get("markets", "5")
    cash         = request.form.get("cash", "1000")
    end_time_str = request.form.get("end_time", "")
    name         = re.sub(r"[^a-zA-Z0-9_-]", "", request.form.get("name", ""))[:40]
    market_type  = request.form.get("market_type", "standard")
    no_fetch     = request.form.get("no_fetch") == "on"

    valid_strategies = {"momentum", "mean_reversion", "rsi", "random_baseline"}
    valid_modes      = {"paper", "backtest"}
    valid_markets    = {"5", "10", "20", "30", "50"}
    valid_cash       = {"500", "1000", "2000", "5000"}
    valid_mtypes     = {"standard", "5min"}

    if (strategy not in valid_strategies or mode not in valid_modes
            or markets not in valid_markets or cash not in valid_cash
            or market_type not in valid_mtypes):
        return redirect("/launch?error=invalid")

    duration_minutes = None
    if mode == "paper":
        try:
            end_dt = datetime.strptime(end_time_str, "%Y-%m-%dT%H:%M")
            now_dt = datetime.now()
            duration_minutes = int((end_dt - now_dt).total_seconds() / 60)
            if duration_minutes <= 0:
                return redirect("/launch?error=past")
        except (ValueError, TypeError):
            return redirect("/launch?error=invalid")

    cmd = [_PYTHON_PATH, "main.py",
           "--strategy", strategy, "--mode", mode,
           "--markets", markets, "--cash", cash]
    if mode == "paper":
        cmd += ["--duration", str(duration_minutes), "--market-type", market_type]
        if name:
            cmd += ["--name", name]
    elif mode == "backtest" and no_fetch:
        cmd += ["--no-fetch"]

    os.makedirs(_LOG_DIR, exist_ok=True)
    ts = int(time.time())
    log_path = os.path.join(_LOG_DIR, f"launch_{strategy}_{mode}_{ts}.log")
    with open(log_path, "w") as lf:
        subprocess.Popen(cmd, cwd=_AGENT_DIR, stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True)

    return redirect("/")


@app.route("/api/finish/<name>", methods=["POST"])
def api_finish(name: str):
    """Send SIGINT to a running paper trader to trigger graceful shutdown."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        return jsonify({"error": "invalid name"}), 400
    data, is_live = _load_state(name)
    if not data or not is_live:
        return jsonify({"error": "session not live"}), 404
    pid = data.get("pid")
    if not pid:
        return jsonify({"error": "no pid in state file"}), 400
    try:
        os.kill(pid, _signal.SIGINT)
        return jsonify({"ok": True})
    except ProcessLookupError:
        return jsonify({"error": "process not found"}), 404
    except PermissionError:
        return jsonify({"error": "permission denied"}), 403


# ---------------------------------------------------------------------------
# Public helper: start Flask in a daemon thread (called from agent main.py)
# ---------------------------------------------------------------------------

def start_in_thread(host: str = "127.0.0.1", port: int = 5000) -> None:
    """
    Launch the Flask dev server in a background daemon thread.

    Raises OSError if the port is already in use.
    """
    import socket
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
    time.sleep(0.3)
    print(f"\nDashboard running at → http://{host}:{port}")
    print("Open that URL in your browser. It refreshes every second.\n")


if __name__ == "__main__":
    # Allow running standalone: python shared/dashboard.py
    print("Starting dashboard server (standalone mode)...")
    print("Discovering state files from all *_agent/data/ directories...")
    _redirect_werkzeug_to_file()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
