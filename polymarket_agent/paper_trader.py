"""
paper_trader.py — Live paper trading loop for Polymarket.

"Paper trading" means:
  - We fetch REAL, LIVE prices from Polymarket every few minutes.
  - We run the strategy and simulate buy/sell decisions.
  - NO real money is used — trades are recorded in a virtual portfolio.

Usage (via main.py):
  python main.py --strategy momentum --mode paper --markets 5 --duration 60
  python main.py --strategy mean_reversion --mode paper --duration 120
"""

import json
import os
import sys
import time
import signal
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

import pandas as pd

# Ensure parent dir (BurryBot/) is in path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from config import (
    PAPER_POLL_INTERVAL_SECONDS, DATA_DIR,
    MARKET_REFRESH_INTERVAL_TICKS, MAX_WATCHED_MARKETS,
)
from data_fetcher import fetch_markets, fetch_price_history
from shared.models import Market, PriceBar, Signal
from shared.portfolio import Portfolio
from shared.risk_manager import RiskManager
from shared.strategy_base import StrategyBase
from shared import metrics as metrics_module


# ---------------------------------------------------------------------------
# Graceful Ctrl+C handler
# ---------------------------------------------------------------------------

_stop_requested = False

def _handle_sigint(signum, frame):
    global _stop_requested
    print("\n\n[Paper Trader] Ctrl+C received — stopping after current tick...")
    _stop_requested = True


# ---------------------------------------------------------------------------
# Paper Trader
# ---------------------------------------------------------------------------

class PaperTrader:
    """
    Runs a strategy in paper-trading mode: real Polymarket prices, simulated trades.
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

        self.price_history: Dict[str, List[PriceBar]] = {}
        self.markets: List[Market] = []
        self.equity_curve: List[float] = []
        self.tick_count = 0

        self.session_start: Optional[datetime] = None
        self.session_end:   Optional[datetime] = None

        self.latest_signals: Dict[str, dict] = {}
        self._known_token_ids: Set[str] = set()
        self._expired_token_ids: Set[str] = set()

    def run(self) -> dict:
        global _stop_requested
        _stop_requested = False

        signal.signal(signal.SIGINT, _handle_sigint)

        print(f"\nPaper Trading Mode — {self.strategy.name}")
        print(f"Instance:  {self.instance_name}")
        print(f"Markets: {self.num_markets} | Duration: {self.duration_minutes} min")
        print(f"Poll interval: {PAPER_POLL_INTERVAL_SECONDS}s | Starting cash: ${self.portfolio.cash:.2f}")
        print(f"State file: data/state_{self.instance_name}.json")
        print("-" * 55)
        sys.stdout.flush()

        print("\nFetching active markets...")
        self._refresh_markets(initial=True)
        if not self.markets:
            print("ERROR: No active markets found.")
            return {}

        self.session_start = datetime.utcnow()
        end_time = self.session_start + timedelta(minutes=self.duration_minutes)
        self.session_end = end_time
        print(f"\nSession runs until {end_time.strftime('%H:%M:%S UTC')} "
              f"({self.duration_minutes} minutes from now)")
        print("Press Ctrl+C to stop early.\n")
        print("=" * 55)

        while datetime.utcnow() < end_time and not _stop_requested:
            if self.tick_count > 0 and self.tick_count % MARKET_REFRESH_INTERVAL_TICKS == 0:
                print("\n[Market Refresh] Re-fetching market list for new/expired markets...")
                sys.stdout.flush()
                self._refresh_markets(initial=False)

            self._run_tick()
            self._write_state()

            next_tick = datetime.utcnow() + timedelta(seconds=PAPER_POLL_INTERVAL_SECONDS)
            while datetime.utcnow() < next_tick and not _stop_requested:
                if datetime.utcnow() >= end_time:
                    break
                time.sleep(5)

        final_prices = self._get_latest_prices()
        self._close_all_positions(final_prices)

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
        fetch_limit = max(self.num_markets * 2, MAX_WATCHED_MARKETS)
        fresh = fetch_markets(limit=fetch_limit, active_only=True)

        now_utc = datetime.now(timezone.utc)

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
            self.markets = usable[:self.num_markets]
            print(f"Watching {len(self.markets)} markets:")
            for m in self.markets:
                print(f"  - {m.question[:65]}")
            sys.stdout.flush()
            print("\nFetching initial price history...")
            sys.stdout.flush()
            self._load_history_for_markets(self.markets)
        else:
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
        active_ids = {m.yes_token_id for m in current_active}
        now_utc    = datetime.now(timezone.utc)
        expired    = []

        for m in list(self.markets):
            is_gone  = m.yes_token_id not in active_ids
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
            print(f"    x {m.question[:65]}")
            self._expired_token_ids.add(m.yes_token_id)
            if m.no_token_id:
                self._expired_token_ids.add(m.no_token_id)
            self.markets = [x for x in self.markets if x.yes_token_id != m.yes_token_id]

            for token_id in [m.yes_token_id, m.no_token_id]:
                if not token_id:
                    continue
                pos = self.portfolio.positions.get(token_id)
                if pos:
                    price = final_prices.get(token_id, pos.avg_cost)
                    sell_signal = Signal(
                        action="SELL", token_id=token_id, outcome=pos.outcome,
                        price=price, reason="Market expired — forced close", confidence=1.0,
                    )
                    trade = self.portfolio.execute_sell(
                        signal=sell_signal, market_slug=m.slug, timestamp=now_utc,
                    )
                    if trade:
                        pnl_sign = "+" if trade.pnl >= 0 else ""
                        print(f"      -> Position closed ({pos.outcome}): {pnl_sign}${trade.pnl:.2f} PnL")

            for token_id in [m.yes_token_id, m.no_token_id]:
                if not token_id:
                    continue
                hist = self.price_history.get(token_id, [])
                if len(hist) > 100:
                    self.price_history[token_id] = hist[-100:]

        sys.stdout.flush()

    def _load_history_for_markets(self, markets: List[Market]) -> None:
        """Load initial hourly price history for a list of markets (YES and NO tokens)."""
        import requests
        from config import CLOB_API_URL, REQUEST_TIMEOUT_SECONDS, PRICE_HISTORY_INTERVAL

        for market in markets:
            for token_id, outcome_label in [
                (market.yes_token_id, "YES"),
                (market.no_token_id,  "NO"),
            ]:
                if not token_id or token_id in self._known_token_ids:
                    continue

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
                existing_ts = {b.timestamp for b in self.price_history[token_id]}
                new_bars = [b for b in bars if b.timestamp not in existing_ts]
                self.price_history[token_id].extend(new_bars)
                self.price_history[token_id].sort(key=lambda b: b.timestamp)

                n = len(self.price_history[token_id])
                print(f"  {market.slug[:42]:42s} [{outcome_label}]: {n} bars")
                sys.stdout.flush()

                self._known_token_ids.add(token_id)

    def _get_latest_prices(self) -> Dict[str, float]:
        prices = {}
        for token_id, bars in self.price_history.items():
            if bars:
                prices[token_id] = bars[-1].price
        return prices

    def _run_tick(self) -> None:
        from dataclasses import replace as dc_replace

        self.tick_count += 1
        now = datetime.utcnow()
        print(f"\n{'='*55}")
        print(f"[Tick {self.tick_count}] {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"{'='*55}")
        sys.stdout.flush()

        current_prices: Dict[str, float] = {}

        for market in self.markets:
            for token_id, outcome_label in [
                (market.yes_token_id, "YES"),
                (market.no_token_id,  "NO"),
            ]:
                if not token_id:
                    continue

                latest_bar = self._fetch_latest_price(token_id)
                existing   = self.price_history.get(token_id, [])

                if latest_bar is None:
                    if existing:
                        current_prices[token_id] = existing[-1].price
                        print(f"  {market.slug[:35]:35s} [{outcome_label}] | "
                              f"price={existing[-1].price:.4f} [no new bar]")
                    else:
                        print(f"  {market.slug[:35]:35s} [{outcome_label}] | no data")
                    sys.stdout.flush()
                    continue

                history = self.price_history.setdefault(token_id, [])
                prev_price = history[-1].price if history else latest_bar.price
                history.append(latest_bar)
                current_prices[token_id] = latest_bar.price

                delta = latest_bar.price - prev_price
                arrow = "^" if delta > 0 else ("v" if delta < 0 else "-")
                print(f"  {market.slug[:35]:35s} [{outcome_label}] | "
                      f"price={latest_bar.price:.4f} {arrow}{abs(delta):.4f} | {len(history)} bars")
                sys.stdout.flush()

                if len(history) < 2:
                    print(f"    -> Skipping: not enough history yet")
                    sys.stdout.flush()
                    continue

                df = pd.DataFrame(
                    {"price": [b.price for b in history[:-1]]},
                    index=[b.timestamp for b in history[:-1]],
                )
                df.index = pd.DatetimeIndex(df.index)

                sig = self.strategy.generate_signal(
                    token_id      = token_id,
                    price_history = df,
                    current_price = latest_bar.price,
                    current_time  = latest_bar.timestamp,
                )

                # Override outcome to match the token side we're analyzing
                sig = dc_replace(sig, outcome=outcome_label)

                action_label = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD"}.get(sig.action, sig.action)
                print(f"    -> Signal: {action_label} | conf={sig.confidence:.0%} | {sig.reason}")
                sys.stdout.flush()

                self.latest_signals[token_id] = {
                    "slug":       market.slug,
                    "question":   market.question,
                    "price":      latest_bar.price,
                    "signal":     sig.action,
                    "outcome":    outcome_label,
                    "reason":     sig.reason,
                    "confidence": sig.confidence,
                    "updated_at": latest_bar.timestamp.isoformat(),
                }

                if sig.action == "HOLD":
                    continue

                # Block BUY if we already hold the opposite side of this market
                if sig.action == "BUY":
                    opposite = market.no_token_id if token_id == market.yes_token_id else market.yes_token_id
                    if opposite and opposite in self.portfolio.positions:
                        print(f"    -> Blocked: already hold opposite side of this market")
                        sys.stdout.flush()
                        continue

                allowed, trade_size, risk_reason = self.risk_manager.check_signal(
                    signal         = sig,
                    portfolio      = self.portfolio,
                    current_prices = current_prices,
                )

                if not allowed:
                    print(f"    -> Risk manager: BLOCKED — {risk_reason}")
                    sys.stdout.flush()
                    continue

                print(f"    -> Risk manager: APPROVED — {risk_reason}")

                if sig.action == "BUY":
                    trade = self.portfolio.execute_buy(
                        signal          = sig,
                        market_slug     = market.slug,
                        trade_size_usdc = trade_size,
                        timestamp       = now,
                    )
                    if trade:
                        print(f"    *** TRADE EXECUTED: BUY {trade.shares:.2f} shares "
                              f"@ ${trade.price:.4f}  |  cost=${trade.total_cost:.2f}  "
                              f"|  fee=${trade.fee:.4f}")
                        self.strategy.on_trade_executed(trade)

                elif sig.action == "SELL":
                    trade = self.portfolio.execute_sell(
                        signal      = sig,
                        market_slug = market.slug,
                        timestamp   = now,
                    )
                    if trade:
                        pnl_sign = "+" if trade.pnl >= 0 else ""
                        print(f"    *** TRADE EXECUTED: SELL {trade.shares:.2f} shares "
                              f"@ ${trade.price:.4f}  |  PnL={pnl_sign}${trade.pnl:.2f}")
                        self.strategy.on_trade_executed(trade)

                sys.stdout.flush()

        total_val = self.portfolio.total_value(current_prices)
        self.equity_curve.append(total_val)
        open_pos     = len(self.portfolio.positions)
        total_trades = len(self.portfolio.trade_log)
        print(f"\n  -- Portfolio snapshot ------------------------------------------")
        print(f"  Cash:       ${self.portfolio.cash:>10.2f}")
        print(f"  Positions:  {open_pos} open")
        for tid, pos in self.portfolio.positions.items():
            cur_price  = current_prices.get(tid, pos.avg_cost)
            unreal_pnl = (cur_price - pos.avg_cost) * pos.shares
            sign = "+" if unreal_pnl >= 0 else ""
            print(f"    {pos.market_slug[:35]:35s} | {pos.shares:.1f} shares "
                  f"@ avg ${pos.avg_cost:.4f} | unrealised PnL {sign}${unreal_pnl:.2f}")
        print(f"  Total value: ${total_val:>9.2f}  |  trades so far: {total_trades}")
        print(f"  ----------------------------------------------------------------")
        sys.stdout.flush()

    def _fetch_latest_price(self, token_id: str) -> Optional[PriceBar]:
        try:
            import requests
            from config import CLOB_API_URL, REQUEST_TIMEOUT_SECONDS

            response = requests.get(
                CLOB_API_URL,
                params={"market": token_id, "interval": "1d", "fidelity": 1},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data    = response.json()
            history = data.get("history", [])

            if not history:
                return None

            last = history[-1]
            new_bar = PriceBar(
                token_id  = token_id,
                timestamp = datetime.utcfromtimestamp(last["t"]),
                price     = float(last["p"]),
            )

            existing = self.price_history.get(token_id, [])
            if existing and new_bar.timestamp <= existing[-1].timestamp:
                return None

            return new_bar

        except Exception:
            return None

    def _write_state(self) -> None:
        """
        Write current session state to data/state_<name>.json atomically.
        Dashboard reads this file to refresh the display.
        """
        now = datetime.utcnow()
        elapsed   = (now - self.session_start).total_seconds() / 60 if self.session_start else 0
        remaining = max(0, (self.session_end - now).total_seconds() / 60) if self.session_end else 0

        current_prices = self._get_latest_prices()
        total_val = self.portfolio.total_value(current_prices)
        pct_return = (total_val - self.portfolio.starting_cash) / self.portfolio.starting_cash * 100

        positions_list = []
        for tid, pos in self.portfolio.positions.items():
            cur_price  = current_prices.get(tid, pos.avg_cost)
            unreal_pnl = (cur_price - pos.avg_cost) * pos.shares
            positions_list.append({
                "market_slug":    pos.market_slug,
                "outcome":        pos.outcome,
                "shares":         round(pos.shares, 4),
                "avg_cost":       round(pos.avg_cost, 6),
                "current_price":  round(cur_price, 6),
                "unrealised_pnl": round(unreal_pnl, 4),
                "opened_at":      pos.opened_at.isoformat(),
            })

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

        sell_trades = [t for t in self.portfolio.trade_log if t.action == "SELL"]
        sharpe   = metrics_module.compute_sharpe_ratio(self.equity_curve)
        max_dd   = metrics_module.compute_max_drawdown(self.equity_curve)
        win_rate = metrics_module.compute_win_rate(self.portfolio.trade_log)

        state = {
            "updated_at":        now.isoformat(),
            "instance_name":     self.instance_name,
            "platform":          "polymarket",
            "pid":               os.getpid(),
            "tick":              self.tick_count,
            "strategy":          self.strategy.name,
            "duration_minutes":  self.duration_minutes,
            "elapsed_minutes":   round(elapsed, 1),
            "remaining_minutes": round(remaining, 1),
            "session_start":     self.session_start.isoformat() if self.session_start else None,
            "session_end":       self.session_end.isoformat()   if self.session_end   else None,
            "portfolio": {
                "cash":             round(self.portfolio.cash, 4),
                "total_value":      round(total_val, 4),
                "starting_cash":    self.portfolio.starting_cash,
                "total_return_pct": round(pct_return, 4),
                "open_positions":   len(self.portfolio.positions),
                "total_trades":     len(self.portfolio.trade_log),
                "sell_trades":      len(sell_trades),
            },
            "metrics": {
                "sharpe_ratio":     round(sharpe, 4),
                "max_drawdown_pct": round(max_dd, 4),
                "win_rate_pct":     round(win_rate, 4),
            },
            "equity_curve":   [round(v, 4) for v in self.equity_curve],
            "positions":      positions_list,
            "recent_trades":  list(reversed(trades_list)),
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


# ---------------------------------------------------------------------------
# 5-minute BTC up/down paper trader
# ---------------------------------------------------------------------------

class FiveMinPaperTrader(PaperTrader):
    """
    Paper trader specialised for Polymarket's 5-minute BTC up/down markets.

    Key differences from PaperTrader:
    - Polls every 30 seconds (not 5 minutes)
    - Market is identified by computing the current 5-min interval slug from the clock
    - Transitions to the next market automatically every 5 minutes
    - Strategy runs on a cross-market synthetic history (one PriceBar per resolved market)
      for both the Up (YES) and Down (NO) tokens
    - Force-exits any open position on either side 30 seconds before each market closes
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current_interval_start: int = 0
        self._cross_market_history: List[PriceBar] = []
        self._cross_market_history_no: List[PriceBar] = []
        self._current_market: Optional[Market] = None

    # ------------------------------------------------------------------
    # run() — 30-second poll loop
    # ------------------------------------------------------------------

    def run(self) -> dict:
        global _stop_requested
        _stop_requested = False

        signal.signal(signal.SIGINT, _handle_sigint)

        from config import (
            FIVE_MIN_POLL_INTERVAL_SECONDS,
            FIVE_MIN_MARKET_REFRESH_TICKS,
        )

        print(f"\n5-Minute Market Mode — {self.strategy.name}")
        print(f"Instance:  {self.instance_name}")
        print(f"Poll interval: {FIVE_MIN_POLL_INTERVAL_SECONDS}s | Duration: {self.duration_minutes} min")
        print(f"State file: data/state_{self.instance_name}.json")
        print("-" * 55)
        sys.stdout.flush()

        self._refresh_markets(initial=True)
        if not self.markets:
            print("ERROR: No active 5-minute market found. Markets may be between intervals.")
            return {}

        self.session_start = datetime.utcnow()
        end_time = self.session_start + timedelta(minutes=self.duration_minutes)
        self.session_end = end_time
        print(f"\nSession runs until {end_time.strftime('%H:%M:%S UTC')} "
              f"({self.duration_minutes} minutes from now)")
        print("Press Ctrl+C to stop early.\n")
        print("=" * 55)

        while datetime.utcnow() < end_time and not _stop_requested:
            if self.tick_count > 0 and self.tick_count % FIVE_MIN_MARKET_REFRESH_TICKS == 0:
                self._refresh_markets(initial=False)

            self._run_5min_tick()
            self._write_state()

            next_tick = datetime.utcnow() + timedelta(seconds=FIVE_MIN_POLL_INTERVAL_SECONDS)
            while datetime.utcnow() < next_tick and not _stop_requested:
                if datetime.utcnow() >= end_time:
                    break
                time.sleep(5)

        final_prices = self._get_latest_prices()
        self._close_all_positions(final_prices)

        final_value = self.portfolio.total_value(final_prices)
        results = metrics_module.compute_all_metrics(
            trades        = self.portfolio.trade_log,
            equity_curve  = self.equity_curve,
            starting_cash = self.portfolio.starting_cash,
            final_value   = final_value,
        )
        print("\nPaper trading session complete.")
        return results

    # ------------------------------------------------------------------
    # _refresh_markets() — detect interval change, fetch new market
    # ------------------------------------------------------------------

    def _refresh_markets(self, initial: bool = False) -> None:
        """Override: fetch the current 5-min market by slug, detect market transition."""
        from data_fetcher import fetch_current_5min_market
        from config import FIVE_MIN_INTERVAL_SECONDS

        current_interval = int(time.time()) // FIVE_MIN_INTERVAL_SECONDS * FIVE_MIN_INTERVAL_SECONDS

        if current_interval == self._current_interval_start and not initial:
            return  # Same market, no change

        # Market changed (or first call): record previous market's closing prices
        if not initial and self._current_market:
            for token_id, hist_list, label in [
                (self._current_market.yes_token_id, self._cross_market_history,    "btc_5min_cross_market"),
                (self._current_market.no_token_id,  self._cross_market_history_no, "btc_5min_cross_market_no"),
            ]:
                if not token_id:
                    continue
                prev_bars = self.price_history.get(token_id, [])
                if prev_bars:
                    hist_list.append(PriceBar(
                        token_id  = label,
                        timestamp = prev_bars[-1].timestamp,
                        price     = prev_bars[-1].price,
                    ))
            print(f"  [5min] Market closed. Cross-market history: "
                  f"{len(self._cross_market_history)} Up / "
                  f"{len(self._cross_market_history_no)} Down bars")

        market = fetch_current_5min_market()
        if market is None or market.is_resolved:
            print("  WARNING: 5-min market not yet available or already resolved. "
                  "Retrying next tick.")
            return

        self._current_interval_start = current_interval
        self._current_market = market
        self.markets = [market]

        if market.yes_token_id not in self._known_token_ids:
            self._load_history_for_markets([market])

        print(f"  [5min] Tracking market: {market.question[:70]}")
        print(f"  [5min] End: {market.end_date}  |  Up: {market.yes_token_id[:12]}...  "
              f"|  Down: {market.no_token_id[:12] if market.no_token_id else 'n/a'}...")
        sys.stdout.flush()

    # ------------------------------------------------------------------
    # _run_5min_tick() — single tick: force-exit check + strategy signal
    # ------------------------------------------------------------------

    def _run_5min_tick(self) -> None:
        """
        Single tick for 5-min mode.

        1. Check if we're within EXIT_BUFFER_SECONDS of market close → force-sell both sides.
        2. Otherwise run strategy on BOTH Up (yes) and Down (no) tokens:
           - Up  token uses the cross-market synthetic history (falls back to intra-market).
           - Down token uses its own intra-market history only.
           Outcome is overridden to YES/NO per token; BUY is blocked if the opposite
           side is already held.
        """
        from dataclasses import replace as dc_replace
        from config import FIVE_MIN_INTERVAL_SECONDS, FIVE_MIN_EXIT_BUFFER_SECONDS

        self.tick_count += 1
        now = datetime.utcnow()
        print(f"\n{'='*55}")
        print(f"[5min Tick {self.tick_count}] {now.strftime('%H:%M:%S UTC')}")

        seconds_into_interval = int(time.time()) % FIVE_MIN_INTERVAL_SECONDS
        seconds_remaining = FIVE_MIN_INTERVAL_SECONDS - seconds_into_interval
        print(f"  Interval: {seconds_into_interval}s elapsed | {seconds_remaining}s remaining")
        sys.stdout.flush()

        # ---- pre-resolution force-exit: close any open position on either side ----
        if seconds_remaining <= FIVE_MIN_EXIT_BUFFER_SECONDS and self.markets:
            current_prices = self._get_latest_prices()
            market = self.markets[0]
            for token_id, side_label in [
                (market.yes_token_id, "Up"),
                (market.no_token_id,  "Down"),
            ]:
                if not token_id:
                    continue
                pos = self.portfolio.positions.get(token_id)
                if pos:
                    price = current_prices.get(token_id, pos.avg_cost)
                    print(f"  [5min] Pre-resolution exit [{side_label}]: "
                          f"{seconds_remaining}s left — force-selling")
                    sell_signal = Signal(
                        action     = "SELL",
                        token_id   = token_id,
                        outcome    = pos.outcome,
                        price      = price,
                        reason     = f"Pre-resolution exit ({seconds_remaining}s left)",
                        confidence = 1.0,
                    )
                    trade = self.portfolio.execute_sell(
                        signal=sell_signal, market_slug=market.slug, timestamp=now,
                    )
                    if trade:
                        pnl_sign = "+" if trade.pnl >= 0 else ""
                        print(f"    -> Closed [{side_label}]: {pnl_sign}${trade.pnl:.2f} PnL")
            # Skip strategy signal this tick; let market transition on next refresh
            total_val = self.portfolio.total_value(self._get_latest_prices())
            self.equity_curve.append(total_val)
            return

        if not self.markets:
            return

        market = self.markets[0]
        current_prices: Dict[str, float] = {}

        for token_id, outcome_label, side_label in [
            (market.yes_token_id, "YES", "Up"),
            (market.no_token_id,  "NO",  "Down"),
        ]:
            if not token_id:
                continue

            # ---- fetch latest intra-market price bar ----
            latest_bar = self._fetch_latest_price(token_id)
            if latest_bar:
                history = self.price_history.setdefault(token_id, [])
                prev_price = history[-1].price if history else latest_bar.price
                history.append(latest_bar)
                delta = latest_bar.price - prev_price
                arrow = "^" if delta > 0 else ("v" if delta < 0 else "-")
                print(f"  {side_label} token: {latest_bar.price:.4f} {arrow}{abs(delta):.4f} "
                      f"| {len(history)} intra-market bars")
                current_price = latest_bar.price
            else:
                intra = self.price_history.get(token_id, [])
                current_price = intra[-1].price if intra else 0.5
                print(f"  {side_label} token: {current_price:.4f} [no new bar]")
            sys.stdout.flush()

            current_prices[token_id] = current_price

            # ---- choose history for strategy ----
            # Both tokens use their own cross-market synthetic series (one closing price
            # per resolved market), falling back to intra-market during the warmup period.
            cross = self._cross_market_history if outcome_label == "YES" else self._cross_market_history_no
            history_to_use = cross if len(cross) >= 2 else self.price_history.get(token_id, [])

            if len(history_to_use) < 2:
                print(f"    -> Waiting for history ({len(history_to_use)} bars)")
                sys.stdout.flush()
                continue

            df = pd.DataFrame(
                {"price": [b.price for b in history_to_use[:-1]]},
                index=[b.timestamp for b in history_to_use[:-1]],
            )
            df.index = pd.DatetimeIndex(df.index)

            sig = self.strategy.generate_signal(
                token_id      = token_id,
                price_history = df,
                current_price = current_price,
                current_time  = now,
            )

            sig = dc_replace(sig, outcome=outcome_label)

            print(f"    -> Signal [{outcome_label}]: {sig.action} | "
                  f"conf={sig.confidence:.0%} | {sig.reason}")
            sys.stdout.flush()

            self.latest_signals[token_id] = {
                "slug":       market.slug,
                "question":   market.question,
                "price":      current_price,
                "signal":     sig.action,
                "outcome":    outcome_label,
                "reason":     sig.reason,
                "confidence": sig.confidence,
                "updated_at": now.isoformat(),
            }

            if sig.action == "HOLD":
                continue

            # Block BUY if we already hold the opposite side of this market
            if sig.action == "BUY":
                opposite = market.no_token_id if token_id == market.yes_token_id else market.yes_token_id
                if opposite and opposite in self.portfolio.positions:
                    print(f"    -> Blocked: already hold opposite side of this market")
                    sys.stdout.flush()
                    continue

            allowed, trade_size, risk_reason = self.risk_manager.check_signal(
                signal         = sig,
                portfolio      = self.portfolio,
                current_prices = current_prices,
            )
            if allowed:
                print(f"    -> Risk manager: APPROVED — {risk_reason}")
                if sig.action == "BUY":
                    trade = self.portfolio.execute_buy(
                        signal          = sig,
                        market_slug     = market.slug,
                        trade_size_usdc = trade_size,
                        timestamp       = now,
                    )
                    if trade:
                        print(f"    *** BUY [{outcome_label}] {trade.shares:.2f} shares "
                              f"@ ${trade.price:.4f}")
                        self.strategy.on_trade_executed(trade)
                elif sig.action == "SELL":
                    trade = self.portfolio.execute_sell(
                        signal      = sig,
                        market_slug = market.slug,
                        timestamp   = now,
                    )
                    if trade:
                        pnl_sign = "+" if trade.pnl >= 0 else ""
                        print(f"    *** SELL [{outcome_label}] {trade.shares:.2f} shares "
                              f"@ ${trade.price:.4f} | PnL={pnl_sign}${trade.pnl:.2f}")
                        self.strategy.on_trade_executed(trade)
            else:
                print(f"    -> Risk manager: BLOCKED — {risk_reason}")
            sys.stdout.flush()

        total_val = self.portfolio.total_value(current_prices)
        self.equity_curve.append(total_val)
