"""
status.py — Snapshot of the currently running paper trading session.

Usage (from polymarket_agent/):
  python status.py

No venv needed — only uses Python stdlib.
"""

import json
import os
import sys
from datetime import datetime, timezone

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "state.json")

# ── ANSI colours ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def col(text, *codes):
    return "".join(codes) + str(text) + RESET

def sign(v):
    return ("+" if v >= 0 else "") + f"{v:.2f}"

def fmt_remaining(minutes):
    if minutes <= 0:
        return col("FINISHED", RED, BOLD)
    h, m = divmod(int(minutes), 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)

# ── Load ─────────────────────────────────────────────────────────────────────
if not os.path.exists(STATE_FILE):
    print(col("No agent running.", RED, BOLD) + " (no state.json found)")
    sys.exit(0)

with open(STATE_FILE) as f:
    d = json.load(f)

p   = d.get("portfolio", {})
m   = d.get("metrics", {})
pos = d.get("positions", [])
trades = d.get("recent_trades", [])
signals = d.get("market_signals", [])

# Updated-at age — stale if older than 10 minutes (2× poll interval)
STALE_AFTER_SECONDS = 360  # 1 missed poll (5 min) + 1 min grace
try:
    updated = datetime.fromisoformat(d["updated_at"])
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_sec = (datetime.now(timezone.utc) - updated).total_seconds()
    age_str = f"{int(age_sec)}s ago"
except Exception:
    age_sec = STALE_AFTER_SECONDS + 1
    age_str = d.get("updated_at", "?")

if age_sec > STALE_AFTER_SECONDS:
    print(col("No agent running.", RED, BOLD) +
          f" (last state.json is {int(age_sec)}s old — session ended or crashed)")
    sys.exit(0)

# ── Print ─────────────────────────────────────────────────────────────────────
W = 56
print()
print(col("─" * W, CYAN))
print(col(f"  BurryBot — Paper Trading Status", BOLD, CYAN))
print(col("─" * W, CYAN))

print(f"  Strategy  : {col(d.get('strategy', '?'), BOLD)}"
      f"   Tick: {col(d.get('tick', '?'), BOLD)}")
print(f"  Updated   : {col(d.get('updated_at','?')[:19], DIM)}  ({age_str})")
print(f"  Remaining : {fmt_remaining(d.get('remaining_minutes', 0))}"
      f"  /  {d.get('duration_minutes','?')} min total")

print(col("─" * W, DIM))

# Portfolio
total_val   = p.get("total_value", 0)
start_cash  = p.get("starting_cash", 1000)
ret_pct     = p.get("total_return_pct", 0)
ret_col     = GREEN if ret_pct >= 0 else RED
print(f"  Portfolio : {col(f'${total_val:.2f}', BOLD)}  "
      f"(started ${start_cash:.2f}  return {col(sign(ret_pct)+'%', ret_col, BOLD)})")
print(f"  Cash      : ${p.get('cash', 0):.2f}   "
      f"Open positions: {p.get('open_positions', 0)}   "
      f"Trades: {p.get('total_trades', 0)}")

print(col("─" * W, DIM))

# Metrics
sharpe = m.get("sharpe_ratio", 0)
dd     = m.get("max_drawdown_pct", 0)
wr     = m.get("win_rate_pct", 0)
print(f"  Sharpe    : {sharpe:.4f}   "
      f"Max drawdown: {col(sign(dd)+'%', RED if dd > 0 else RESET)}   "
      f"Win rate: {wr:.1f}%")

print(col("─" * W, DIM))

# Open positions
if pos:
    print(f"  {col('Open Positions:', BOLD)}")
    for p_ in pos:
        pnl = p_.get("unrealised_pnl", 0)
        pnl_col = GREEN if pnl >= 0 else RED
        slug = p_.get("market_slug", "?")[:38]
        print(f"    {slug:<38s}  {p_.get('shares',0):.0f} sh"
              f"  PnL {col(sign(pnl), pnl_col)}")
else:
    print(f"  {col('Open Positions:', BOLD)} none")

print(col("─" * W, DIM))

# Recent trades (last 5)
if trades:
    print(f"  {col('Recent Trades:', BOLD)}")
    for t in trades[:5]:
        pnl   = t.get("pnl", 0)
        act   = t.get("action", "?")
        act_c = GREEN if act == "BUY" else RED
        pnl_s = "—" if act == "BUY" else col(sign(pnl), GREEN if pnl >= 0 else RED)
        ts    = t.get("timestamp", "")[:16].replace("T", " ")
        slug  = t.get("market_slug", "?")[:30]
        print(f"    {ts}  {col(act, act_c, BOLD):<4s}  {slug:<30s}  @ ${t.get('price',0):.4f}  PnL {pnl_s}")
else:
    print(f"  {col('Recent Trades:', BOLD)} none yet")

print(col("─" * W, DIM))

# Market signals summary
buys  = sum(1 for s in signals if s.get("signal") == "BUY")
sells = sum(1 for s in signals if s.get("signal") == "SELL")
holds = sum(1 for s in signals if s.get("signal") == "HOLD")
print(f"  Signals (last tick) : "
      f"{col(str(buys)+' BUY', GREEN)}  "
      f"{col(str(sells)+' SELL', RED)}  "
      f"{col(str(holds)+' HOLD', DIM)}  "
      f"across {len(signals)} markets")

print(col("─" * W, CYAN))
print()
