"""
copy_trader.py — Mirror trades from a target Polymarket wallet.

Polls the Polymarket Data API every COPY_POLL_INTERVAL_SECONDS and
executes paper (or live) trades whenever the target wallet places new orders.

Usage (via main.py):
  python main.py --mode copy --copy-address 0x... --copy-size 10 --duration 60
  python main.py --mode copy --copy-address 0x... --copy-size 10 --mode live --duration 60
"""

import os
import sys
import signal
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from shared.models import Signal
from shared.portfolio import Portfolio
from shared.risk_manager import RiskManager
from shared.strategy_base import StrategyBase
from paper_trader import PaperTrader


# ---------------------------------------------------------------------------
# No-op strategy — CopyTrader generates signals externally, not via strategy
# ---------------------------------------------------------------------------

class _NoOpStrategy(StrategyBase):
    name = "CopyTrader"

    def setup(self, params: dict) -> None:
        pass

    def generate_signal(self, market, price_bars, portfolio, risk_manager, timestamp):
        return Signal(
            action="HOLD", token_id=market.yes_token_id, outcome="YES",
            price=0.0, reason="noop", confidence=0.0,
        )


# ---------------------------------------------------------------------------
# Stop-flag (mirrors paper_trader.py pattern)
# ---------------------------------------------------------------------------

_stop_requested = False


def _handle_sigint(signum, frame):
    global _stop_requested
    _stop_requested = True
    print("\n[Ctrl+C] Stopping after current poll...")


# ---------------------------------------------------------------------------
# CopyTrader
# ---------------------------------------------------------------------------

class CopyTrader(PaperTrader):
    """
    Monitors a target Polymarket wallet and mirrors every new trade.

    Paper mode (default): trades are simulated against a virtual portfolio.
    Live mode:            real GTC limit orders are placed on the CLOB.

    Args:
        copy_address:     Polymarket proxy-wallet address to mirror (0x...).
        copy_size_usdc:   Fixed USDC amount to spend on each copied BUY.
        cash:             Starting virtual cash (paper mode only; live mode
                          seeds from on-chain balance).
        duration_minutes: How long to run the session.
        instance_name:    State-file identifier (default: copy_<addr prefix>).
        wallet:           WalletAdapter for live mode (None → paper mode).
    """

    def __init__(
        self,
        copy_address: str,
        copy_size_usdc: float = 10.0,
        sizing_mode: str = "fixed",
        cash: float = 1000.0,
        duration_minutes: int = 60,
        instance_name: Optional[str] = None,
        wallet=None,
    ):
        portfolio    = Portfolio(starting_cash=cash)
        risk_manager = RiskManager()
        strategy     = _NoOpStrategy()

        name = instance_name or f"copy_{copy_address[2:10].lower()}"

        super().__init__(
            strategy         = strategy,
            portfolio        = portfolio,
            risk_manager     = risk_manager,
            num_markets      = 0,
            duration_minutes = duration_minutes,
            instance_name    = name,
        )

        self.copy_address    = copy_address.lower()
        self.copy_size_usdc  = copy_size_usdc
        self.sizing_mode     = sizing_mode  # "fixed" or "risk_manager"
        self._seen_tx:        Set[str]   = set()
        self._price_cache:    Dict[str, float] = {}
        self._copied_trades:  List[dict] = []

        # Live mode setup (lazy py_clob_client import mirrors live_trader.py)
        if wallet is not None:
            self._trading_mode     = "live"
            self.wallet            = wallet
            self._tick_size_cache: Dict[str, float] = {}
            print("\n[CopyTrader] Connecting to Polymarket CLOB API...")
            sys.stdout.flush()
            self.clob = wallet.build_clob_client()
            print(f"[CopyTrader] Auth OK — funder: {wallet.funder_address}")
            sys.stdout.flush()
            self._sync_balance_from_chain()
        else:
            self._trading_mode = "paper"
            self.clob          = None

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> dict:
        from config import COPY_POLL_INTERVAL_SECONDS
        import data_fetcher

        global _stop_requested
        _stop_requested = False
        signal.signal(signal.SIGINT, _handle_sigint)

        mode_label = "Live" if self._trading_mode == "live" else "Paper"
        print(f"\nCopy Trading ({mode_label}) — mirroring {self.copy_address[:10]}…")
        print(f"Instance:        {self.instance_name}")
        print(f"Size per trade:  ${self.copy_size_usdc:.2f} USDC")
        print(f"Poll interval:   {COPY_POLL_INTERVAL_SECONDS}s")
        print(f"Duration:        {self.duration_minutes} min")
        print(f"Starting cash:   ${self.portfolio.cash:.2f}")
        print("-" * 55)
        sys.stdout.flush()

        self.session_start = datetime.utcnow()
        self.session_end   = self.session_start + timedelta(minutes=self.duration_minutes)
        self._write_state(status="starting")

        # Seed seen_tx_hashes — skip historical trades on startup
        print("\nSeeding historical trades (will not be copied)…")
        try:
            history = data_fetcher.fetch_user_activity(self.copy_address, limit=100)
            for tx in history:
                self._seen_tx.add(tx["transactionHash"])
            print(f"  Skipped {len(history)} existing trade(s).")
        except Exception as e:
            print(f"  Warning: could not seed history ({e})")

        self._write_state(status="running")
        print(f"\nWatching for new trades. Session ends {self.session_end.strftime('%H:%M:%S UTC')}.")
        print("=" * 55)
        sys.stdout.flush()

        while datetime.utcnow() < self.session_end and not _stop_requested:
            try:
                self._poll_and_copy(data_fetcher)
            except Exception as e:
                print(f"  [poll error] {e}")

            self.tick_count += 1
            self.equity_curve.append(self.portfolio.total_value(self._price_cache))
            self._write_state()

            next_tick = datetime.utcnow() + timedelta(seconds=COPY_POLL_INTERVAL_SECONDS)
            while datetime.utcnow() < next_tick and not _stop_requested:
                if datetime.utcnow() >= self.session_end:
                    break
                time.sleep(5)

        # Close all positions and finalise
        prices = self._get_latest_prices()
        self._close_all_positions(prices)
        final_value = self.portfolio.total_value(prices)

        from shared import metrics as metrics_module
        results = metrics_module.compute_all_metrics(
            trades        = self.portfolio.trade_log,
            equity_curve  = self.equity_curve,
            starting_cash = self.portfolio.starting_cash,
            final_value   = final_value,
        )
        self._write_state(status="finished")
        return results

    # ------------------------------------------------------------------
    # Copy logic
    # ------------------------------------------------------------------

    def _poll_and_copy(self, data_fetcher) -> None:
        """Fetch recent activity and execute any new trades."""
        trades = data_fetcher.fetch_user_activity(self.copy_address)
        new_trades = [t for t in trades if t["transactionHash"] not in self._seen_tx]
        if not new_trades:
            return

        # Process oldest first so we respect chronological order
        for trade in reversed(new_trades):
            self._seen_tx.add(trade["transactionHash"])
            self._execute_copy_trade(trade, data_fetcher)

    def _execute_copy_trade(self, trade: dict, data_fetcher) -> None:
        """Mirror a single trade from the target wallet."""
        token_id = trade["asset"]
        side     = trade["side"]   # "BUY" or "SELL"
        slug     = trade.get("slug", "unknown")
        outcome  = trade.get("outcome", "?")
        title    = trade.get("title", slug)[:40]

        # Fetch current midprice so our order reflects the live market
        price = data_fetcher.fetch_token_midprice(token_id)
        if price is None or not (0.01 <= price <= 0.99):
            print(f"  [skip] {slug} [{outcome}]: price {price} out of tradeable range")
            return

        self._price_cache[token_id] = price
        now = datetime.utcnow()

        if side == "BUY":
            self._copy_buy(token_id, slug, outcome, title, price, now)
        elif side == "SELL":
            self._copy_sell(token_id, slug, outcome, price, now)

        # Record for state display
        self._record_copy(trade, side, price)

    def _copy_buy(
        self,
        token_id: str,
        slug: str,
        outcome: str,
        title: str,
        price: float,
        now: datetime,
    ) -> None:
        signal = Signal(
            action="BUY", token_id=token_id, outcome=outcome,
            price=price, reason=f"copy:{self.copy_address[:8]}", confidence=1.0,
        )

        if self.sizing_mode == "risk_manager":
            allowed, trade_size, reason = self.risk_manager.check_signal(
                signal, self.portfolio, self._price_cache
            )
            if not allowed:
                print(f"  [skip BUY] {slug} [{outcome}]: {reason}")
                return
        else:
            trade_size = self.copy_size_usdc
            if self.portfolio.cash < trade_size:
                print(f"  [skip BUY] {slug} [{outcome}]: insufficient cash "
                      f"(${self.portfolio.cash:.2f} < ${trade_size:.2f})")
                return

        if self._trading_mode == "live":
            ok = self._execute_live_copy_buy(signal, slug, now, trade_size)
        else:
            trade = self.portfolio.execute_buy(signal=signal, market_slug=slug, trade_size_usdc=trade_size, timestamp=now)
            ok = trade is not None

        if ok:
            shares = round(trade_size / price, 4)
            print(f"  COPY BUY  {slug[:32]} [{outcome}] @ {price:.4f}  "
                  f"${trade_size:.2f} → {shares} shares")

    def _copy_sell(
        self,
        token_id: str,
        slug: str,
        outcome: str,
        price: float,
        now: datetime,
    ) -> None:
        pos = self.portfolio.positions.get(token_id)
        if not pos:
            print(f"  [skip SELL] {slug} [{outcome}]: no position held")
            return

        signal = Signal(
            action="SELL", token_id=token_id, outcome=outcome,
            price=price, reason=f"copy:{self.copy_address[:8]}", confidence=1.0,
        )

        if self._trading_mode == "live":
            ok = self._execute_live_copy_sell(signal, slug, pos.shares, now)
        else:
            trade = self.portfolio.execute_sell(signal=signal, market_slug=slug, timestamp=now)
            ok = trade is not None

        if ok:
            print(f"  COPY SELL {slug[:32]} [{outcome}] @ {price:.4f}  "
                  f"{pos.shares:.4f} shares")

    def _record_copy(self, original: dict, action: str, our_price: float) -> None:
        self._copied_trades.append({
            "timestamp":       datetime.utcnow().isoformat(),
            "action":          action,
            "slug":            original.get("slug", ""),
            "outcome":         original.get("outcome", ""),
            "their_price":     round(original.get("price", 0), 6),
            "their_size_usdc": round(original.get("usdcSize", 0), 4),
            "our_price":       round(our_price, 6),
            "tx":              original.get("transactionHash", "")[:20] + "…",
        })

    # ------------------------------------------------------------------
    # Live order execution
    # ------------------------------------------------------------------

    def _get_tick_size(self, token_id: str) -> float:
        if token_id in self._tick_size_cache:
            return self._tick_size_cache[token_id]
        try:
            resp = self.clob.get_tick_size(token_id)
            tick = float(resp.get("minimum_tick_size", 0.01))
        except Exception:
            tick = 0.01
        self._tick_size_cache[token_id] = tick
        return tick

    def _execute_live_copy_buy(self, signal: Signal, market_slug: str, timestamp: datetime, trade_size_usdc: float) -> bool:
        from config import LIVE_SLIPPAGE_TOLERANCE, LIVE_MIN_ORDER_SIZE_USDC
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
        except ImportError as e:
            print(f"  [live] py_clob_client not available: {e}")
            return False

        if trade_size_usdc < LIVE_MIN_ORDER_SIZE_USDC:
            print(f"  [live] skip BUY: size ${trade_size_usdc:.2f} < min ${LIVE_MIN_ORDER_SIZE_USDC:.2f}")
            return False

        tick  = self._get_tick_size(signal.token_id)
        price = round(min(0.99, signal.price + LIVE_SLIPPAGE_TOLERANCE), 2)
        price = round(round(price / tick) * tick, 6)
        size  = round(trade_size_usdc / price, 4)

        try:
            order_args = OrderArgs(
                token_id=signal.token_id,
                price=price,
                size=size,
                side="BUY",
            )
            resp = self.clob.create_and_post_order(order_args)
            order_id = resp.get("orderID", "?")
            print(f"  [live] BUY order placed: {order_id}  price={price}  size={size}")
            trade = self.portfolio.execute_buy(signal=signal, market_slug=market_slug, trade_size_usdc=trade_size_usdc, timestamp=timestamp)
            return trade is not None
        except Exception as e:
            print(f"  [live] BUY failed: {e}")
            return False

    def _execute_live_copy_sell(
        self, signal: Signal, market_slug: str, shares: float, timestamp: datetime
    ) -> bool:
        from config import LIVE_SLIPPAGE_TOLERANCE, LIVE_MIN_ORDER_SIZE_USDC
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
        except ImportError as e:
            print(f"  [live] py_clob_client not available: {e}")
            return False

        tick  = self._get_tick_size(signal.token_id)
        price = round(max(0.01, signal.price - LIVE_SLIPPAGE_TOLERANCE), 2)
        price = round(round(price / tick) * tick, 6)

        if shares * price < LIVE_MIN_ORDER_SIZE_USDC:
            print(f"  [live] skip SELL: value ${shares * price:.2f} < min ${LIVE_MIN_ORDER_SIZE_USDC:.2f}")
            return False

        try:
            order_args = OrderArgs(
                token_id=signal.token_id,
                price=price,
                size=shares,
                side="SELL",
            )
            resp = self.clob.create_and_post_order(order_args)
            order_id = resp.get("orderID", "?")
            print(f"  [live] SELL order placed: {order_id}  price={price}  size={shares}")
            trade = self.portfolio.execute_sell(signal=signal, market_slug=market_slug, timestamp=timestamp)
            return trade is not None
        except Exception as e:
            print(f"  [live] SELL failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Live balance sync
    # ------------------------------------------------------------------

    def _sync_balance_from_chain(self) -> None:
        print("\n[CopyTrader] Syncing USDC balance from chain…")
        try:
            info = self.clob.get_balance_allowance(params={"asset_type": "USDC"})
            raw  = info.get("balance")
            if raw is not None:
                balance = float(raw)
                print(f"  Wallet balance: ${balance:,.2f} USDC")
                self.portfolio.cash          = balance
                self.portfolio.starting_cash = balance
            else:
                print("  WARNING: Could not read balance — using configured starting cash")
        except Exception as e:
            print(f"  WARNING: Balance sync failed: {e}")
        sys.stdout.flush()

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def _get_latest_prices(self) -> Dict[str, float]:
        """Return cached prices for positions we hold."""
        return {tid: self._price_cache[tid]
                for tid in self.portfolio.positions
                if tid in self._price_cache}

    def _extra_state_fields(self) -> dict:
        return {
            "copy_address":   self.copy_address,
            "copy_size_usdc": self.copy_size_usdc if self.sizing_mode == "fixed" else None,
            "sizing_mode":    self.sizing_mode,
            "copied_trades":  self._copied_trades[-20:],
        }
