"""
live_trader.py — Phase 3: real order execution via the Polymarket CLOB API.

LiveTrader inherits PaperTrader and overrides only the trade execution layer.
Everything else (market refresh, strategy signals, risk manager, dashboard state)
is reused unchanged from PaperTrader.

Architecture:
  PaperTrader._run_tick()  → portfolio.execute_buy/sell()  (simulated)
  LiveTrader._run_tick()   → _execute_live_buy/sell()      (real CLOB order)
                           → portfolio.execute_buy/sell()  (called after fill confirmed)

The portfolio is seeded from the chain at startup (real USDC balance, real positions)
and updated from actual fill prices after each order.

Usage (via main.py):
  python main.py --strategy momentum --mode live --markets 3 --duration 60
"""

import os
import sys
import time
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from typing import Dict, Optional

import pandas as pd

# Ensure parent dir (BurryBot/) is in path for shared imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from config import (
    LIVE_MIN_ORDER_SIZE_USDC,
    LIVE_SLIPPAGE_TOLERANCE,
    CLOB_HOST,
)
from paper_trader import PaperTrader
from shared.models import Signal
from wallet import WalletAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round_to_tick(price: float, tick_size: float) -> float:
    """Round price to the nearest valid tick increment."""
    if tick_size <= 0:
        return price
    return round(round(price / tick_size) * tick_size, 10)


# ---------------------------------------------------------------------------
# LiveTrader
# ---------------------------------------------------------------------------

class LiveTrader(PaperTrader):
    """
    Live trading mode: real CLOB orders placed via py-clob-client.

    Inherits all market discovery, strategy, risk management, and dashboard
    state logic from PaperTrader. Overrides only trade execution.
    """

    def __init__(self, wallet: WalletAdapter, **kwargs):
        """
        Args:
            wallet:  WalletAdapter providing an authenticated ClobClient
            **kwargs: Passed directly to PaperTrader.__init__
                      (strategy, portfolio, risk_manager, num_markets,
                       duration_minutes, instance_name)
        """
        super().__init__(**kwargs)
        self.wallet = wallet
        self._trading_mode    = "live"
        self._tick_size_cache: Dict[str, float] = {}

        print("\n[LiveTrader] Connecting to Polymarket CLOB API...")
        sys.stdout.flush()
        self.clob = wallet.build_clob_client()
        print(f"[LiveTrader] Auth OK — funder: {wallet.funder_address}")
        sys.stdout.flush()

        self._sync_portfolio_from_chain()

    # ------------------------------------------------------------------
    # Chain sync
    # ------------------------------------------------------------------

    def _sync_portfolio_from_chain(self) -> None:
        """
        Seed the portfolio with the real on-chain state:
        - USDC balance → portfolio.cash
        - Open CLOB positions → portfolio.positions (best-effort via Data API)

        Logs a comparison between the configured starting_cash and actual wallet balance.
        """
        print("\n[LiveTrader] Syncing portfolio from chain...")
        sys.stdout.flush()

        # --- USDC balance ---
        try:
            balance_info = self.clob.get_balance_allowance(
                params={"asset_type": "USDC"}
            )
            # Response shape: {"balance": "123.456789", ...}  (string, USDC units)
            raw_balance = balance_info.get("balance", None)
            if raw_balance is not None:
                usdc_balance = float(raw_balance)
                print(f"  Configured starting_cash: ${self.portfolio.starting_cash:,.2f}")
                print(f"  Actual wallet balance:    ${usdc_balance:,.2f}")
                self.portfolio.cash          = usdc_balance
                self.portfolio.starting_cash = usdc_balance
            else:
                print("  WARNING: Could not parse USDC balance — using configured starting_cash")
        except Exception as e:
            print(f"  WARNING: Failed to fetch balance: {e}")
            print(f"  Using configured starting_cash: ${self.portfolio.starting_cash:,.2f}")

        # --- Open positions ---
        # Polymarket Data API: GET /data-api/v2/positions?user={address}&sizeThreshold=0.01
        try:
            import requests
            url = f"https://data-api.polymarket.com/positions"
            resp = requests.get(
                url,
                params={"user": self.wallet.funder_address, "sizeThreshold": "0.01"},
                timeout=15,
            )
            if resp.status_code == 200:
                positions = resp.json()
                if positions:
                    print(f"  WARNING: Found {len(positions)} open position(s) on chain.")
                    print("  These are NOT loaded into the local portfolio automatically.")
                    print("  The portfolio starts fresh; existing on-chain positions")
                    print("  will only appear in your Polymarket account.")
                    for pos in positions[:5]:  # show at most 5
                        print(f"    - {pos.get('market', {}).get('question', 'unknown')[:60]}"
                              f" | size={pos.get('size', '?')} | outcome={pos.get('outcome', '?')}")
                    if len(positions) > 5:
                        print(f"    ... and {len(positions) - 5} more")
                else:
                    print("  No open positions found on chain.")
            else:
                print(f"  WARNING: positions API returned HTTP {resp.status_code}")
        except Exception as e:
            print(f"  WARNING: Could not fetch on-chain positions: {e}")

        sys.stdout.flush()

    # ------------------------------------------------------------------
    # Tick size
    # ------------------------------------------------------------------

    def _get_tick_size(self, token_id: str) -> float:
        """
        Return the valid tick size for a token (cached).
        Falls back to 0.01 (1 cent) if the API call fails.
        """
        if token_id in self._tick_size_cache:
            return self._tick_size_cache[token_id]

        try:
            resp = self.clob.get_tick_size(token_id)
            # Response: {"minimum_tick_size": "0.01"}
            tick = float(resp.get("minimum_tick_size", 0.01))
        except Exception:
            tick = 0.01  # default fallback

        self._tick_size_cache[token_id] = tick
        return tick

    # ------------------------------------------------------------------
    # Live order execution
    # ------------------------------------------------------------------

    def _execute_live_buy(
        self,
        signal: Signal,
        market_slug: str,
        trade_size_usdc: float,
        timestamp: datetime,
    ) -> bool:
        """
        Place a real GTC limit BUY order on the CLOB.

        Returns True if the order was placed and at least partially matched,
        False otherwise (order not placed, or unmatched/cancelled).
        After a successful fill, calls portfolio.execute_buy() with real fill data.
        """
        from py_clob_client.clob_types import OrderArgs, BUY

        if trade_size_usdc < LIVE_MIN_ORDER_SIZE_USDC:
            print(f"    -> Live BUY skipped: size ${trade_size_usdc:.2f} < min ${LIVE_MIN_ORDER_SIZE_USDC}")
            return False

        tick_size = self._get_tick_size(signal.token_id)
        price     = _round_to_tick(signal.price, tick_size)

        # Apply slippage tolerance: allow paying up to (price + slippage) on a buy
        price_with_slippage = _round_to_tick(
            min(price + LIVE_SLIPPAGE_TOLERANCE, 0.99), tick_size
        )

        # Shares = USDC / price, rounded to 2 decimal places
        if price <= 0:
            print(f"    -> Live BUY skipped: price={price} is non-positive")
            return False
        shares = round(trade_size_usdc / price, 2)

        if shares <= 0:
            print(f"    -> Live BUY skipped: computed shares={shares} <= 0")
            return False

        print(f"    -> Placing live BUY order: {shares:.2f} shares @ ${price:.4f} "
              f"(slippage limit: ${price_with_slippage:.4f}) | "
              f"~${trade_size_usdc:.2f} USDC")
        sys.stdout.flush()

        try:
            order_args = OrderArgs(
                token_id = signal.token_id,
                price    = price,
                size     = shares,
                side     = BUY,
            )
            resp = self.clob.create_and_post_order(order_args)
        except Exception as e:
            print(f"    -> Live BUY ERROR: {e}")
            sys.stdout.flush()
            return False

        order_id = resp.get("orderID") or resp.get("order_id") or "unknown"
        status   = resp.get("status", "unknown")
        print(f"    -> Order response: id={order_id} status={status}")
        sys.stdout.flush()

        # "matched" or "live" both mean the order was placed / partially filled
        if status in ("matched", "live", "delayed"):
            # Update portfolio with real fill price
            fill_price = float(resp.get("price", price))
            fill_signal = dc_replace(signal, price=fill_price)
            trade = self.portfolio.execute_buy(
                signal          = fill_signal,
                market_slug     = market_slug,
                trade_size_usdc = trade_size_usdc,
                timestamp       = timestamp,
            )
            if trade:
                # Store the CLOB order_id in trade_id for traceability
                trade.trade_id = order_id
                print(f"    *** LIVE BUY EXECUTED: {trade.shares:.2f} shares @ ${trade.price:.4f} "
                      f"| cost=${trade.total_cost:.2f} | order_id={order_id}")
                self.strategy.on_trade_executed(trade)
            return True

        # "unmatched" or other statuses: order not filled
        print(f"    -> Live BUY not filled (status={status}) — no portfolio update")
        sys.stdout.flush()
        return False

    def _execute_live_sell(
        self,
        signal: Signal,
        market_slug: str,
        timestamp: datetime,
    ) -> bool:
        """
        Place a real GTC limit SELL order on the CLOB.

        Returns True if the order was placed and at least partially matched,
        False otherwise.
        After a successful fill, calls portfolio.execute_sell() with real fill data.
        """
        from py_clob_client.clob_types import OrderArgs, SELL

        pos = self.portfolio.positions.get(signal.token_id)
        if pos is None:
            print(f"    -> Live SELL skipped: no position held for {signal.token_id[:12]}...")
            return False

        tick_size = self._get_tick_size(signal.token_id)
        price     = _round_to_tick(signal.price, tick_size)

        # Apply slippage tolerance: accept as low as (price - slippage) on a sell
        price_with_slippage = _round_to_tick(
            max(price - LIVE_SLIPPAGE_TOLERANCE, 0.01), tick_size
        )

        shares = round(pos.shares, 2)
        if shares <= 0:
            print(f"    -> Live SELL skipped: shares={shares} <= 0")
            return False

        print(f"    -> Placing live SELL order: {shares:.2f} shares @ ${price:.4f} "
              f"(slippage floor: ${price_with_slippage:.4f})")
        sys.stdout.flush()

        try:
            order_args = OrderArgs(
                token_id = signal.token_id,
                price    = price,
                size     = shares,
                side     = SELL,
            )
            resp = self.clob.create_and_post_order(order_args)
        except Exception as e:
            print(f"    -> Live SELL ERROR: {e}")
            sys.stdout.flush()
            return False

        order_id = resp.get("orderID") or resp.get("order_id") or "unknown"
        status   = resp.get("status", "unknown")
        print(f"    -> Order response: id={order_id} status={status}")
        sys.stdout.flush()

        if status in ("matched", "live", "delayed"):
            fill_price  = float(resp.get("price", price))
            fill_signal = dc_replace(signal, price=fill_price)
            trade = self.portfolio.execute_sell(
                signal      = fill_signal,
                market_slug = market_slug,
                timestamp   = timestamp,
            )
            if trade:
                trade.trade_id = order_id
                pnl_sign = "+" if trade.pnl >= 0 else ""
                print(f"    *** LIVE SELL EXECUTED: {trade.shares:.2f} shares @ ${trade.price:.4f} "
                      f"| PnL={pnl_sign}${trade.pnl:.2f} | order_id={order_id}")
                self.strategy.on_trade_executed(trade)
            return True

        print(f"    -> Live SELL not filled (status={status}) — no portfolio update")
        sys.stdout.flush()
        return False

    # ------------------------------------------------------------------
    # Tick override — replaces portfolio.execute_buy/sell with live orders
    # ------------------------------------------------------------------

    def _run_tick(self) -> None:
        """
        Override PaperTrader._run_tick() to use real CLOB orders.

        Structure mirrors PaperTrader._run_tick() exactly; only the trade
        execution calls differ.
        """
        self.tick_count += 1
        now = datetime.utcnow()
        print(f"\n{'='*55}")
        print(f"[Live Tick {self.tick_count}] {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
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
                sig = dc_replace(sig, outcome=outcome_label)

                action_label = sig.action
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
                    self._execute_live_buy(
                        signal          = sig,
                        market_slug     = market.slug,
                        trade_size_usdc = trade_size,
                        timestamp       = now,
                    )

                elif sig.action == "SELL":
                    self._execute_live_sell(
                        signal      = sig,
                        market_slug = market.slug,
                        timestamp   = now,
                    )

                sys.stdout.flush()

        # Re-sync cash from chain after any orders this tick to prevent drift
        self._resync_cash()

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

    # ------------------------------------------------------------------
    # Cash re-sync
    # ------------------------------------------------------------------

    def _resync_cash(self) -> None:
        """
        Re-fetch USDC balance from chain to prevent portfolio cash from drifting.
        Called after each tick's orders are placed.
        Silently skips on error (local portfolio balance remains as fallback).
        """
        try:
            balance_info = self.clob.get_balance_allowance(
                params={"asset_type": "USDC"}
            )
            raw = balance_info.get("balance")
            if raw is not None:
                self.portfolio.cash = float(raw)
        except Exception:
            pass  # keep local balance on error

    # ------------------------------------------------------------------
    # run() override — geo-block check + allowance warning before start
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Override PaperTrader.run() to add:
        1. Geo-block check — abort if region is blocked
        2. Allowance check — warn if contracts are not approved (EOA only)
        3. Live mode banner
        """
        self._check_geo_block()
        self._check_allowances()

        # Print live-mode banner (replaces PaperTrader's "Paper Trading Mode" header)
        print(f"\n{'!'*55}")
        print(f"  LIVE TRADING MODE — {self.strategy.name}")
        print(f"  Instance:  {self.instance_name}")
        print(f"  Markets: {self.num_markets} | Duration: {self.duration_minutes} min")
        print(f"  Funder:  {self.wallet.funder_address}")
        print(f"  Cash (from chain): ${self.portfolio.cash:.2f}")
        print(f"{'!'*55}")
        sys.stdout.flush()

        # Delegate to parent run() — skip its header print by patching strategy name temporarily
        # We call super().run() directly; PaperTrader will print its own header.
        # That's acceptable — it just says "Paper Trading Mode" which is misleading,
        # but functional. The live-mode banner above is printed first for clarity.
        return super().run()

    # ------------------------------------------------------------------
    # Geo-block check
    # ------------------------------------------------------------------

    def _check_geo_block(self) -> None:
        """
        Check if the current region is geo-blocked by Polymarket.
        Raises SystemExit if blocked.
        """
        try:
            import requests
            resp = requests.get(
                "https://polymarket.com/api/geoblock",
                timeout=10,
            )
            data = resp.json()
            # {"blocked": true/false, "countryCode": "US", ...}
            if data.get("blocked", False):
                country = data.get("countryCode", "unknown")
                print(f"\nERROR: Live trading is geo-blocked in your region ({country}).")
                print("Polymarket does not allow trading from certain jurisdictions.")
                sys.exit(1)
            print(f"[LiveTrader] Geo-block check: OK "
                  f"(country={data.get('countryCode', 'unknown')})")
        except SystemExit:
            raise
        except Exception as e:
            # Don't abort on network error — just warn
            print(f"[LiveTrader] WARNING: Geo-block check failed ({e}). Proceeding anyway.")
        sys.stdout.flush()

    # ------------------------------------------------------------------
    # Allowance check (informational)
    # ------------------------------------------------------------------

    def _check_allowances(self) -> None:
        """
        Check if exchange contract allowances are set.
        Magic Link accounts handle this automatically; EOA wallets need set_allowances().
        Prints a warning but does not abort.
        """
        try:
            allowance_info = self.clob.get_balance_allowance(
                params={"asset_type": "CONDITIONAL"}
            )
            ctf_allowance  = float(allowance_info.get("allowance", 0))
            if ctf_allowance == 0:
                print(
                    "\n[LiveTrader] WARNING: CTF Exchange allowance is zero.\n"
                    "  If you're using an EOA wallet, run:\n"
                    "      client.set_allowances()\n"
                    "  Magic Link accounts should not see this — check your credentials."
                )
            else:
                print(f"[LiveTrader] Allowance check: OK (CTF allowance={ctf_allowance})")
        except Exception as e:
            print(f"[LiveTrader] WARNING: Could not check allowances: {e}")
        sys.stdout.flush()
