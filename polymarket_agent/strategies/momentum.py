"""
strategies/momentum.py — Momentum trading strategy.

Core idea:
  "If the price has been going UP consistently for the last N bars,
   it will probably keep going up — so BUY."
  "If it has been going DOWN — SELL (or don't hold)."

This is one of the oldest and most studied strategies in finance.
It works because markets often trend due to information arriving gradually.

How it works here:
  1. Look at the last LOOKBACK price bars.
  2. Count how many consecutive bars moved up vs. down.
  3. If all bars moved UP  → BUY signal.
  4. If all bars moved DOWN → SELL signal (if we own it).
  5. Otherwise → HOLD.

Limitation: Momentum works badly at price reversals (tops and bottoms).
"""

from datetime import datetime

import pandas as pd

from models import Signal, Trade
from strategy_base import StrategyBase
from config import MOMENTUM_LOOKBACK, MIN_TRADEABLE_PRICE, MAX_TRADEABLE_PRICE


class MomentumStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        """
        Store the lookback window (how many bars to inspect for trend).
        Default comes from config.py but can be overridden via --params.
        """
        self.lookback = params.get("lookback", MOMENTUM_LOOKBACK)
        print(f"[MomentumStrategy] Lookback window = {self.lookback} bars")

    def generate_signal(
        self,
        token_id:      str,
        price_history: pd.DataFrame,
        current_price: float,
        current_time:  datetime,
    ) -> Signal:
        """
        Return BUY if trending up for `lookback` bars, SELL if trending down,
        HOLD otherwise.
        """
        # Always return HOLD if we don't have enough history yet
        if len(price_history) < self.lookback:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Not enough history ({len(price_history)} < {self.lookback} bars needed)",
            )

        # Skip extreme prices — near-certain or near-zero markets are illiquid
        if not (MIN_TRADEABLE_PRICE <= current_price <= MAX_TRADEABLE_PRICE):
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Price {current_price:.3f} outside tradeable range",
            )

        # Take the last N prices (the "lookback window")
        recent_prices = price_history["price"].iloc[-self.lookback:].tolist()

        # Calculate bar-by-bar changes: +1 if up, -1 if down, 0 if flat
        moves = []
        for i in range(1, len(recent_prices)):
            diff = recent_prices[i] - recent_prices[i - 1]
            if diff > 0:
                moves.append(1)
            elif diff < 0:
                moves.append(-1)
            else:
                moves.append(0)

        # Count consecutive ups vs downs
        up_count   = sum(1 for m in moves if m > 0)
        down_count = sum(1 for m in moves if m < 0)
        total      = len(moves)

        # Strong uptrend: majority of moves were positive
        if up_count == total:
            confidence = min(1.0, up_count / total)
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=f"Strong uptrend: {up_count}/{total} bars moved up",
            )

        # Strong downtrend: majority of moves were negative
        if down_count == total:
            confidence = min(1.0, down_count / total)
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=f"Strong downtrend: {down_count}/{total} bars moved down",
            )

        # Mixed signal — do nothing
        return Signal(
            action="HOLD", token_id=token_id, outcome="YES",
            price=current_price, confidence=0.0,
            reason=f"Mixed trend: {up_count} up, {down_count} down out of {total} bars",
        )

    def on_trade_executed(self, trade: Trade) -> None:
        """Log each executed trade (optional — for debugging)."""
        pass  # Could print trade details here if you want verbose output
