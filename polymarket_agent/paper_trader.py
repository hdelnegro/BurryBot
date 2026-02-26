"""
paper_trader.py — Live paper trading loop.

"Paper trading" means:
  - We fetch REAL, LIVE prices from Polymarket every few minutes.
  - We run the strategy and simulate buy/sell decisions.
  - NO real money is used — trades are recorded in a virtual portfolio.

This is Phase 2: it lets you test strategies on live markets before
risking real money in Phase 3 (live trading).

Usage (via main.py):
  python main.py --strategy momentum --mode paper --markets 5 --duration 60
  python main.py --strategy mean_reversion --mode paper --duration 120

The session runs for --duration minutes, then prints the final results.
Press Ctrl+C at any time to stop early.
"""

import json
import os
import time
import signal
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd

from config import (
    PAPER_POLL_INTERVAL_SECONDS, DATA_DIR,
    MARKET_REFRESH_INTERVAL_TICKS, MAX_WATCHED_MARKETS,
)
from data_fetcher import fetch_markets, fetch_price_history
from models import Market, PriceBar, Signal
from portfolio import Portfolio
from risk_manager import RiskManager
from strategy_base import StrategyBase
import metrics as metrics_module


# ---------------------------------------------------------------------------
# Graceful Ctrl+C handler
# ---------------------------------------------------------------------------

_stop_requested = False

def _handle_sigint(signum, frame):
    """When user presses Ctrl+C, set the stop flag instead of crashing."""
    global _stop_requested
    print("\n\n[Paper Trader] Ctrl+C received — stopping after current tick...")
    _stop_requested = True


# ---------------------------------------------------------------------------
# Paper Trader
# ---------------------------------------------------------------------------

class PaperTrader:
    """
    Runs a strategy in paper-trading mode: real prices, simulated trades.

    Architecture:
      - On startup: fetch active markets and initial price history.
      - Every POLL_INTERVAL seconds:
          1. Fetch the latest price for each market token.
          2. Append it to the in-memory price history.
          3. Call strategy.generate_signal() with the updated history.
          4. Check risk manager.
          5. Execute simulated trade if approved.
          6. Record equity snapshot.
      - After --duration minutes (or Ctrl+C): print final metrics.
    """

    def __init__(
        self,
        strategy:        StrategyBase,
        portfolio:        Portfolio,
        risk_manager:     RiskManager,
        num_markets:      int = 5,
        duration_minutes: int = 60,
        instance_name:    str = None,
    ):
        self.strategy         = strategy
        self.portfolio        = portfolio
        self.risk_manager     = risk_manager
        self.num_markets      = num_markets
        self.duration_minutes = duration_minutes
        self.instance_name    = instance_name or "default"

        # In-memory price history for each token (built up over the session)
        # Dict: token_id → list of PriceBar
        self.price_history: Dict[str, List[PriceBar]] = {}

        # Markets we're watching
        self.markets: List[Market] = []

        # Equity snapshots over time
        self.equity_curve: List[float] = []

        # Track the tick count for display
        self.tick_count = 0

        # Session timing (set at run() time)
        self.session_start: Optional[datetime] = None
        self.session_end:   Optional[datetime] = None

        # Latest signal per market (for dashboard display)
        # Dict: token_id → {"slug", "question", "price", "signal", "reason", "confidence"}
        self.latest_signals: Dict[str, dict] = {}

        # token_ids already seen — prevents re-downloading history on refresh
        self._known_token_ids: Set[str] = set()

        # token_ids confirmed expired/resolved — never re-watch
        self._expired_token_ids: Set[str] = set()

    def run(self) -> dict:
        """
        Main loop: runs for self.duration_minutes, polling every POLL_INTERVAL seconds.

        Returns the final metrics dictionary.
        """
        global _stop_requested
        _stop_requested = False

        # Register graceful Ctrl+C handler
        signal.signal(signal.SIGINT, _handle_sigint)

        import sys as _sys
        print(f"\nPaper Trading Mode — {self.strategy.name}")
        print(f"Instance:  {self.instance_name}")
        print(f"Markets: {self.num_markets} | Duration: {self.duration_minutes} min")
        print(f"Poll interval: {PAPER_POLL_INTERVAL_SECONDS}s | Starting cash: ${self.portfolio.cash:.2f}")
        print(f"State file: data/state_{self.instance_name}.json")
        print("-" * 55)
        _sys.stdout.flush()

        # Step 1: Fetch active markets and initial price history
        print("\nFetching active markets...")
        self._refresh_markets(initial=True)
        if not self.markets:
            print("ERROR: No active markets found.")
            return {}

        # Step 3: Set session timing
        self.session_start = datetime.utcnow()
        end_time = self.session_start + timedelta(minutes=self.duration_minutes)
        self.session_end = end_time
        print(f"\nSession runs until {end_time.strftime('%H:%M:%S UTC')} "
              f"({self.duration_minutes} minutes from now)")
        print("Press Ctrl+C to stop early.\n")
        print("=" * 55)

        # Step 4: Main polling loop
        while datetime.utcnow() < end_time and not _stop_requested:
            # Periodically refresh market list to pick up new markets (e.g. hourly BTC up/down)
            if self.tick_count > 0 and self.tick_count % MARKET_REFRESH_INTERVAL_TICKS == 0:
                print("\n[Market Refresh] Re-fetching market list for new/expired markets...")
                sys.stdout.flush()
                self._refresh_markets(initial=False)

            self._run_tick()
            self._write_state()

            # Wait for the next poll (or exit if time is up / Ctrl+C)
            next_tick = datetime.utcnow() + timedelta(seconds=PAPER_POLL_INTERVAL_SECONDS)
            while datetime.utcnow() < next_tick and not _stop_requested:
                if datetime.utcnow() >= end_time:
                    break
                time.sleep(5)  # Check every 5 seconds for Ctrl+C

        # Step 5: Force-close remaining positions at last known prices
        final_prices = self._get_latest_prices()
        self._close_all_positions(final_prices)

        # Step 6: Compute and return metrics
        final_value = self.portfolio.total_value(final_prices)
        results = metrics_module.compute_all_metrics(
            trades       = self.portfolio.trade_log,
            equity_curve = self.equity_curve,
            starting_cash= self.portfolio.starting_cash,
            final_value  = final_value,
        )

        print("\nPaper trading session complete.")
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _refresh_markets(self, initial: bool = False) -> None:
        """
        Fetch the current top-volume active market list and update self.markets.

        On initial call: populates self.markets and loads price history for all.
        On subsequent calls (every MARKET_REFRESH_INTERVAL_TICKS ticks):
          - Detects newly opened markets → adds them and loads their history.
          - Detects expired/resolved markets → removes them and closes positions.
          - Respects MAX_WATCHED_MARKETS cap.

        This is critical for short-lived markets like "Bitcoin Up or Down - 2PM ET"
        which open and resolve every hour.
        """
        # Fetch more than we need so we have room to filter
        fetch_limit = max(self.num_markets * 2, MAX_WATCHED_MARKETS)
        fresh = fetch_markets(limit=fetch_limit, active_only=True)

        now_utc = datetime.now(timezone.utc)

        # Filter out already-expired markets and markets we've blacklisted
        def is_usable(m: Market) -> bool:
            if m.yes_token_id in self._expired_token_ids:
                return False
            if m.end_date:
                try:
                    end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                    if end <= now_utc:
                        return False
                except ValueError:
                    pass
            return True

        usable = [m for m in fresh if is_usable(m)]

        if initial:
            # First run — take the top N
            self.markets = usable[:self.num_markets]
            print(f"Watching {len(self.markets)} markets:")
            for m in self.markets:
                print(f"  - {m.question[:65]}")
            sys.stdout.flush()
            print("\nFetching initial price history...")
            sys.stdout.flush()
            self._load_history_for_markets(self.markets)
        else:
            # Refresh run — handle expired and add new
            self._handle_expired_markets(usable)

            current_ids = {m.yes_token_id for m in self.markets}
            new_markets  = [
                m for m in usable
                if m.yes_token_id not in current_ids
                and m.yes_token_id not in self._known_token_ids
            ]

            added = []
            for m in new_markets:
                if len(self.markets) >= MAX_WATCHED_MARKETS:
                    break
                self.markets.append(m)
                added.append(m)

            if added:
                print(f"  Added {len(added)} new market(s):")
                for m in added:
                    print(f"    + {m.question[:65]}")
                sys.stdout.flush()
                self._load_history_for_markets(added)
            else:
                print(f"  No new markets. Watching {len(self.markets)} markets.")
                sys.stdout.flush()

    def _handle_expired_markets(self, current_active: List[Market]) -> None:
        """
        Detect markets that have expired since the last refresh.

        A market is considered expired if:
          - Its end_date is in the past, OR
          - It no longer appears in the fresh active market list.

        For expired markets with open positions, we force-close at the last known price.
        """
        active_ids = {m.yes_token_id for m in current_active}
        now_utc    = datetime.now(timezone.utc)
        expired    = []

        for m in list(self.markets):
            is_gone = m.yes_token_id not in active_ids
            past_end = False
            if m.end_date:
                try:
                    end = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                    past_end = end <= now_utc
                except ValueError:
                    pass

            if is_gone or past_end:
                expired.append(m)

        if not expired:
            return

        print(f"  Expired/resolved {len(expired)} market(s):")
        final_prices = self._get_latest_prices()

        for m in expired:
            print(f"    ✗ {m.question[:65]}")
            self._expired_token_ids.add(m.yes_token_id)
            self.markets = [x for x in self.markets if x.yes_token_id != m.yes_token_id]

            # Force-close any open position
            pos = self.portfolio.positions.get(m.yes_token_id)
            if pos:
                price = final_prices.get(m.yes_token_id, pos.avg_cost)
                sell_signal = Signal(
                    action="SELL", token_id=m.yes_token_id, outcome=pos.outcome,
                    price=price, reason="Market expired — forced close", confidence=1.0,
                )
                trade = self.portfolio.execute_sell(
                    signal=sell_signal, market_slug=m.slug, timestamp=now_utc,
                )
                if trade:
                    pnl_sign = "+" if trade.pnl >= 0 else ""
                    print(f"      → Position closed: {pnl_sign}${trade.pnl:.2f} PnL")

            # Clean up history to save memory (keep last 100 bars)
            hist = self.price_history.get(m.yes_token_id, [])
            if len(hist) > 100:
                self.price_history[m.yes_token_id] = hist[-100:]

        sys.stdout.flush()

    def _load_history_for_markets(self, markets: List[Market]) -> None:
        """Load initial hourly price history for a list of markets."""
        import requests
        from config import CLOB_API_URL, REQUEST_TIMEOUT_SECONDS, PRICE_HISTORY_INTERVAL

        for market in markets:
            token_id = market.yes_token_id
            if token_id in self._known_token_ids:
                continue  # already have history

            try:
                response = requests.get(
                    CLOB_API_URL,
                    params={"market": token_id, "interval": PRICE_HISTORY_INTERVAL, "fidelity": 60},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                raw = response.json().get("history", [])
                bars = [
                    PriceBar(
                        token_id  = token_id,
                        timestamp = datetime.utcfromtimestamp(e["t"]),
                        price     = float(e["p"]),
                    )
                    for e in raw
                ]
                bars.sort(key=lambda b: b.timestamp)
            except Exception:
                bars = []

            self.price_history.setdefault(token_id, [])
            # Merge: only add bars newer than what we already have
            existing_ts = {b.timestamp for b in self.price_history[token_id]}
            new_bars = [b for b in bars if b.timestamp not in existing_ts]
            self.price_history[token_id].extend(new_bars)
            self.price_history[token_id].sort(key=lambda b: b.timestamp)

            n = len(self.price_history[token_id])
            tag = "(new)" if token_id not in self._known_token_ids else "(updated)"
            print(f"  {market.slug[:45]:45s} {tag}: {n} bars")
            sys.stdout.flush()

            self._known_token_ids.add(token_id)

    def _get_latest_prices(self) -> Dict[str, float]:
        """Return the most recent known price for each token."""
        prices = {}
        for token_id, bars in self.price_history.items():
            if bars:
                prices[token_id] = bars[-1].price
        return prices

    def _run_tick(self) -> None:
        """
        One polling cycle: fetch latest prices → run strategy → execute trades.
        """
        import sys

        self.tick_count += 1
        now = datetime.utcnow()
        print(f"\n{'='*55}")
        print(f"[Tick {self.tick_count}] {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"{'='*55}")
        sys.stdout.flush()

        # Fetch fresh current prices for each market
        current_prices: Dict[str, float] = {}

        for market in self.markets:
            token_id   = market.yes_token_id
            latest_bar = self._fetch_latest_price(token_id)

            existing = self.price_history.get(token_id, [])

            if latest_bar is None:
                # No new bar yet — price unchanged since last poll
                if existing:
                    current_prices[token_id] = existing[-1].price
                    print(f"  {market.slug[:38]:38s} | price={existing[-1].price:.4f} "
                          f"[no new bar]")
                else:
                    print(f"  {market.slug[:38]:38s} | no data")
                sys.stdout.flush()
                continue

            # Append new bar to in-memory history
            history = self.price_history.setdefault(token_id, [])
            prev_price = history[-1].price if history else latest_bar.price
            history.append(latest_bar)
            current_prices[token_id] = latest_bar.price

            # Price change indicator
            delta = latest_bar.price - prev_price
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
            print(f"  {market.slug[:38]:38s} | price={latest_bar.price:.4f} "
                  f"{arrow}{abs(delta):.4f} | {len(history)} bars")
            sys.stdout.flush()

            # Build DataFrame for strategy (no lookahead: exclude the bar just fetched)
            if len(history) < 2:
                print(f"    → Skipping: not enough history yet")
                sys.stdout.flush()
                continue

            df = pd.DataFrame(
                {"price": [b.price for b in history[:-1]]},
                index=[b.timestamp for b in history[:-1]],
            )
            df.index = pd.DatetimeIndex(df.index)

            # Ask the strategy what to do
            signal = self.strategy.generate_signal(
                token_id      = token_id,
                price_history = df,
                current_price = latest_bar.price,
                current_time  = latest_bar.timestamp,
            )

            # Always print the signal (including HOLD) so user can see reasoning
            action_icon = {"BUY": "🟢 BUY", "SELL": "🔴 SELL", "HOLD": "⬜ HOLD"}.get(
                signal.action, signal.action
            )
            print(f"    → Signal: {action_icon} | conf={signal.confidence:.0%} | "
                  f"{signal.reason}")
            sys.stdout.flush()

            # Record for dashboard
            self.latest_signals[token_id] = {
                "slug":       market.slug,
                "question":   market.question,
                "price":      latest_bar.price,
                "signal":     signal.action,
                "reason":     signal.reason,
                "confidence": signal.confidence,
                "updated_at": latest_bar.timestamp.isoformat(),
            }

            if signal.action == "HOLD":
                continue

            # Check risk manager
            allowed, trade_size, risk_reason = self.risk_manager.check_signal(
                signal         = signal,
                portfolio      = self.portfolio,
                current_prices = current_prices,
            )

            if not allowed:
                print(f"    → Risk manager: BLOCKED — {risk_reason}")
                sys.stdout.flush()
                continue

            print(f"    → Risk manager: APPROVED — {risk_reason}")

            # Execute simulated trade
            if signal.action == "BUY":
                trade = self.portfolio.execute_buy(
                    signal          = signal,
                    market_slug     = market.slug,
                    trade_size_usdc = trade_size,
                    timestamp       = now,
                )
                if trade:
                    print(f"    *** TRADE EXECUTED: BUY {trade.shares:.2f} shares "
                          f"@ ${trade.price:.4f}  |  cost=${trade.total_cost:.2f}  "
                          f"|  fee=${trade.fee:.4f}")
                    self.strategy.on_trade_executed(trade)

            elif signal.action == "SELL":
                trade = self.portfolio.execute_sell(
                    signal      = signal,
                    market_slug = market.slug,
                    timestamp   = now,
                )
                if trade:
                    pnl_sign = "+" if trade.pnl >= 0 else ""
                    print(f"    *** TRADE EXECUTED: SELL {trade.shares:.2f} shares "
                          f"@ ${trade.price:.4f}  |  PnL={pnl_sign}${trade.pnl:.2f}")
                    self.strategy.on_trade_executed(trade)

            sys.stdout.flush()

        # Summary line after processing all markets
        total_val = self.portfolio.total_value(current_prices)
        self.equity_curve.append(total_val)
        open_pos  = len(self.portfolio.positions)
        total_trades = len(self.portfolio.trade_log)
        print(f"\n  ── Portfolio snapshot ──────────────────────────────")
        print(f"  Cash:       ${self.portfolio.cash:>10.2f}")
        print(f"  Positions:  {open_pos} open")
        for tid, pos in self.portfolio.positions.items():
            cur_price = current_prices.get(tid, pos.avg_cost)
            unreal_pnl = (cur_price - pos.avg_cost) * pos.shares
            sign = "+" if unreal_pnl >= 0 else ""
            print(f"    {pos.market_slug[:35]:35s} | {pos.shares:.1f} shares "
                  f"@ avg ${pos.avg_cost:.4f} | unrealised PnL {sign}${unreal_pnl:.2f}")
        print(f"  Total value: ${total_val:>9.2f}  |  trades so far: {total_trades}")
        print(f"  ────────────────────────────────────────────────────")
        sys.stdout.flush()

    def _fetch_latest_price(self, token_id: str) -> Optional[PriceBar]:
        """
        Fetch the most recent price for a token.

        Uses interval=1d with fidelity=1 (1-minute bars for the last day).
        This gives us the freshest price without downloading the entire history.
        We only return the bar if its timestamp is newer than the last bar we
        already have (to avoid duplicate entries).
        """
        try:
            import requests
            from config import CLOB_API_URL, REQUEST_TIMEOUT_SECONDS

            response = requests.get(
                CLOB_API_URL,
                params={"market": token_id, "interval": "1d", "fidelity": 1},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            history = data.get("history", [])

            if not history:
                return None

            last = history[-1]
            new_bar = PriceBar(
                token_id  = token_id,
                timestamp = datetime.utcfromtimestamp(last["t"]),
                price     = float(last["p"]),
            )

            # Only return the bar if it's newer than what we already have
            existing = self.price_history.get(token_id, [])
            if existing and new_bar.timestamp <= existing[-1].timestamp:
                return None  # Not a new bar yet — no change since last poll

            return new_bar

        except Exception:
            return None

    def _write_state(self) -> None:
        """
        Write the current session state to data/state.json.

        The dashboard reads this file every few seconds to refresh the display.
        We write atomically (temp file + rename) so the dashboard never reads
        a half-written file.
        """
        now = datetime.utcnow()
        elapsed = (now - self.session_start).total_seconds() / 60 if self.session_start else 0
        remaining = max(0, (self.session_end - now).total_seconds() / 60) if self.session_end else 0

        # Current prices from latest known bars
        current_prices = self._get_latest_prices()

        # Portfolio snapshot
        total_val = self.portfolio.total_value(current_prices)
        pct_return = (total_val - self.portfolio.starting_cash) / self.portfolio.starting_cash * 100

        # Open positions
        positions_list = []
        for tid, pos in self.portfolio.positions.items():
            cur_price  = current_prices.get(tid, pos.avg_cost)
            unreal_pnl = (cur_price - pos.avg_cost) * pos.shares
            positions_list.append({
                "market_slug":   pos.market_slug,
                "outcome":       pos.outcome,
                "shares":        round(pos.shares, 4),
                "avg_cost":      round(pos.avg_cost, 6),
                "current_price": round(cur_price, 6),
                "unrealised_pnl": round(unreal_pnl, 4),
                "opened_at":     pos.opened_at.isoformat(),
            })

        # Recent trades (last 20)
        trades_list = []
        for t in self.portfolio.trade_log[-20:]:
            trades_list.append({
                "trade_id":   t.trade_id,
                "market_slug": t.market_slug,
                "action":     t.action,
                "outcome":    t.outcome,
                "shares":     round(t.shares, 4),
                "price":      round(t.price, 6),
                "total_cost": round(t.total_cost, 4),
                "pnl":        round(t.pnl, 4),
                "timestamp":  t.timestamp.isoformat(),
            })

        # Compute live metrics
        import metrics as metrics_module
        sell_trades = [t for t in self.portfolio.trade_log if t.action == "SELL"]
        sharpe   = metrics_module.compute_sharpe_ratio(self.equity_curve)
        max_dd   = metrics_module.compute_max_drawdown(self.equity_curve)
        win_rate = metrics_module.compute_win_rate(self.portfolio.trade_log)

        state = {
            "updated_at":       now.isoformat(),
            "instance_name":    self.instance_name,
            "tick":             self.tick_count,
            "strategy":         self.strategy.name,
            "duration_minutes": self.duration_minutes,
            "elapsed_minutes":  round(elapsed, 1),
            "remaining_minutes": round(remaining, 1),
            "session_start":    self.session_start.isoformat() if self.session_start else None,
            "session_end":      self.session_end.isoformat()   if self.session_end   else None,
            "portfolio": {
                "cash":          round(self.portfolio.cash, 4),
                "total_value":   round(total_val, 4),
                "starting_cash": self.portfolio.starting_cash,
                "total_return_pct": round(pct_return, 4),
                "open_positions": len(self.portfolio.positions),
                "total_trades":  len(self.portfolio.trade_log),
                "sell_trades":   len(sell_trades),
            },
            "metrics": {
                "sharpe_ratio":    round(sharpe, 4),
                "max_drawdown_pct": round(max_dd, 4),
                "win_rate_pct":    round(win_rate, 4),
            },
            "equity_curve":  [round(v, 4) for v in self.equity_curve],
            "positions":     positions_list,
            "recent_trades": list(reversed(trades_list)),
            "market_signals": list(self.latest_signals.values()),
        }

        os.makedirs(DATA_DIR, exist_ok=True)
        state_filename = f"state_{self.instance_name}.json"
        tmp_path   = os.path.join(DATA_DIR, state_filename + ".tmp")
        state_path = os.path.join(DATA_DIR, state_filename)
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, state_path)

    def _close_all_positions(self, final_prices: Dict[str, float]) -> None:
        """Liquidate all remaining positions at end of session."""
        open_tokens = list(self.portfolio.positions.keys())
        if not open_tokens:
            return

        print(f"\nClosing {len(open_tokens)} open position(s) at session end...")
        close_time = datetime.utcnow()

        for token_id in open_tokens:
            pos = self.portfolio.positions.get(token_id)
            if pos is None:
                continue

            price = final_prices.get(token_id, pos.avg_cost)

            sell_signal = Signal(
                action="SELL", token_id=token_id, outcome=pos.outcome,
                price=price, reason="Session ended — forced liquidation", confidence=1.0,
            )
            self.portfolio.execute_sell(
                signal=sell_signal, market_slug=pos.market_slug, timestamp=close_time,
            )
