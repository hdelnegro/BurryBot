"""
shared/strategies/momentum.py — Momentum trading strategy.

Core idea:
  "If the price has been going UP consistently for the last N bars,
   it will probably keep going up — so BUY."
  "If it has been going DOWN — SELL."

Works on any 0.0–1.0 probability market (Polymarket, Kalshi, etc.).
"""

from datetime import datetime

import pandas as pd

from shared.models import Signal, Trade
from shared.strategy_base import StrategyBase
from config import MOMENTUM_LOOKBACK, MIN_TRADEABLE_PRICE, MAX_TRADEABLE_PRICE


class MomentumStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        self.lookback = params.get("lookback", MOMENTUM_LOOKBACK)
        print(f"[MomentumStrategy] Lookback window = {self.lookback} bars")

    def generate_signal(
        self,
        token_id:      str,
        price_history: pd.DataFrame,
        current_price: float,
        current_time:  datetime,
    ) -> Signal:
        if len(price_history) < self.lookback:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Not enough history ({len(price_history)} < {self.lookback} bars needed)",
            )

        if not (MIN_TRADEABLE_PRICE <= current_price <= MAX_TRADEABLE_PRICE):
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Price {current_price:.3f} outside tradeable range",
            )

        recent_prices = price_history["price"].iloc[-self.lookback:].tolist()

        moves = []
        for i in range(1, len(recent_prices)):
            diff = recent_prices[i] - recent_prices[i - 1]
            if diff > 0:
                moves.append(1)
            elif diff < 0:
                moves.append(-1)
            else:
                moves.append(0)

        up_count   = sum(1 for m in moves if m > 0)
        down_count = sum(1 for m in moves if m < 0)
        total      = len(moves)

        if up_count == total:
            confidence = min(1.0, up_count / total)
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=f"Strong uptrend: {up_count}/{total} bars moved up",
            )

        if down_count == total:
            confidence = min(1.0, down_count / total)
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=f"Strong downtrend: {down_count}/{total} bars moved down",
            )

        return Signal(
            action="HOLD", token_id=token_id, outcome="YES",
            price=current_price, confidence=0.0,
            reason=f"Mixed trend: {up_count} up, {down_count} down out of {total} bars",
        )

    def on_trade_executed(self, trade: Trade) -> None:
        pass
