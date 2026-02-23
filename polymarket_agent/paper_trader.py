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

import time
import signal
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from config import PAPER_POLL_INTERVAL_SECONDS
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
    ):
        self.strategy         = strategy
        self.portfolio        = portfolio
        self.risk_manager     = risk_manager
        self.num_markets      = num_markets
        self.duration_minutes = duration_minutes

        # In-memory price history for each token (built up over the session)
        # Dict: token_id → list of PriceBar
        self.price_history: Dict[str, List[PriceBar]] = {}

        # Markets we're watching
        self.markets: List[Market] = []

        # Equity snapshots over time
        self.equity_curve: List[float] = []

        # Track the tick count for display
        self.tick_count = 0

    def run(self) -> dict:
        """
        Main loop: runs for self.duration_minutes, polling every POLL_INTERVAL seconds.

        Returns the final metrics dictionary.
        """
        global _stop_requested
        _stop_requested = False

        # Register graceful Ctrl+C handler
        signal.signal(signal.SIGINT, _handle_sigint)

        print(f"\nPaper Trading Mode — {self.strategy.name}")
        print(f"Markets: {self.num_markets} | Duration: {self.duration_minutes} min")
        print(f"Poll interval: {PAPER_POLL_INTERVAL_SECONDS}s | Starting cash: ${self.portfolio.cash:.2f}")
        print("-" * 55)

        # Step 1: Fetch active markets
        print("\nFetching active markets...")
        self.markets = fetch_markets(limit=self.num_markets, active_only=True)
        if not self.markets:
            print("ERROR: No active markets found.")
            return {}

        print(f"Watching {len(self.markets)} markets:")
        for m in self.markets:
            print(f"  - {m.question[:60]}")

        # Step 2: Fetch initial price history to warm up the strategy
        print("\nFetching initial price history...")
        self._load_initial_history()

        # Step 3: Set session end time
        end_time = datetime.utcnow() + timedelta(minutes=self.duration_minutes)
        print(f"\nSession runs until {end_time.strftime('%H:%M:%S UTC')} "
              f"({self.duration_minutes} minutes from now)")
        print("Press Ctrl+C to stop early.\n")
        print("=" * 55)

        # Step 4: Main polling loop
        while datetime.utcnow() < end_time and not _stop_requested:
            self._run_tick()

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

    def _load_initial_history(self) -> None:
        """
        Fetch historical price bars so the strategy has context from the start.

        We use fidelity=60 (hourly bars) for the initial load — enough resolution
        for the strategy to compute trends, without downloading thousands of bars.
        """
        import requests
        from config import CLOB_API_URL, REQUEST_TIMEOUT_SECONDS, PRICE_HISTORY_INTERVAL

        for market in self.markets:
            token_id = market.yes_token_id
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

            if bars:
                self.price_history[token_id] = bars
                print(f"  {market.slug[:40]}: {len(bars)} historical bars loaded")
            else:
                self.price_history[token_id] = []
                print(f"  {market.slug[:40]}: no history (fresh market)")

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
        self.tick_count += 1
        now = datetime.utcnow()
        print(f"\n[Tick {self.tick_count}] {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        # Fetch fresh current prices for each market
        current_prices: Dict[str, float] = {}

        for market in self.markets:
            token_id   = market.yes_token_id
            latest_bar = self._fetch_latest_price(token_id)

            if latest_bar is None:
                # No new data yet — use last known price
                existing = self.price_history.get(token_id, [])
                if existing:
                    current_prices[token_id] = existing[-1].price
                continue

            # Append new bar to history
            history = self.price_history.setdefault(token_id, [])
            history.append(latest_bar)
            current_prices[token_id] = latest_bar.price

            print(f"  {market.slug[:35]:35s} | price={latest_bar.price:.3f} | "
                  f"history={len(history)} bars")

            # Build DataFrame for strategy (no lookahead: exclude the bar we just fetched)
            if len(history) < 2:
                continue

            df = pd.DataFrame(
                {"price": [b.price for b in history[:-1]]},
                index=[b.timestamp for b in history[:-1]],
            )
            df.index = pd.DatetimeIndex(df.index)

            # Ask the strategy
            signal = self.strategy.generate_signal(
                token_id      = token_id,
                price_history = df,
                current_price = latest_bar.price,
                current_time  = latest_bar.timestamp,
            )

            if signal.action == "HOLD":
                continue

            # Check risk manager
            allowed, trade_size, reason = self.risk_manager.check_signal(
                signal         = signal,
                portfolio      = self.portfolio,
                current_prices = current_prices,
            )

            if not allowed:
                print(f"    Blocked: {reason}")
                continue

            # Execute simulated trade
            print(f"    SIGNAL: {signal.action} | {signal.reason}")
            if signal.action == "BUY":
                trade = self.portfolio.execute_buy(
                    signal          = signal,
                    market_slug     = market.slug,
                    trade_size_usdc = trade_size,
                    timestamp       = now,
                )
                if trade:
                    print(f"    TRADE: Bought {trade.shares:.2f} shares @ ${trade.price:.3f} "
                          f"(cost=${trade.total_cost:.2f})")
                    self.strategy.on_trade_executed(trade)

            elif signal.action == "SELL":
                trade = self.portfolio.execute_sell(
                    signal      = signal,
                    market_slug = market.slug,
                    timestamp   = now,
                )
                if trade:
                    pnl_sign = "+" if trade.pnl >= 0 else ""
                    print(f"    TRADE: Sold {trade.shares:.2f} shares @ ${trade.price:.3f} "
                          f"(PnL={pnl_sign}${trade.pnl:.2f})")
                    self.strategy.on_trade_executed(trade)

        # Record portfolio value after this tick
        total_val = self.portfolio.total_value(current_prices)
        self.equity_curve.append(total_val)
        print(f"  Portfolio: cash=${self.portfolio.cash:.2f} | "
              f"positions={len(self.portfolio.positions)} | "
              f"total=${total_val:.2f}")

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
