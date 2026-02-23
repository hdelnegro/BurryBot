"""
strategies/mean_reversion.py — Mean-reversion trading strategy.

Core idea:
  "If the price has drifted far below its recent average (statistically
   unlikely), it will probably snap back up — so BUY."
  "If it has drifted far above the average, it will probably drop — SELL."

This is the opposite of momentum.  It exploits overreactions and
temporary mispricings in prediction markets.

How it works here:
  1. Compute the rolling mean and standard deviation over a WINDOW of bars.
  2. Calculate the Z-score: how many standard deviations away from the mean
     is the current price?
         Z = (current_price - mean) / std_dev
  3. If Z < -THRESHOLD  → price is unusually LOW  → BUY (expect snap back)
  4. If Z >  THRESHOLD  → price is unusually HIGH → SELL (expect drop)
  5. Otherwise → HOLD.

Z-score intuition:
  Z = -1.5 means the price is 1.5 standard deviations BELOW the mean.
  In a normal distribution, this happens only ~7% of the time — rare!
"""

from datetime import datetime

import pandas as pd
import numpy as np

from models import Signal, Trade
from strategy_base import StrategyBase
from config import (
    MEAN_REVERSION_Z_THRESHOLD,
    MEAN_REVERSION_WINDOW,
    MIN_TRADEABLE_PRICE,
    MAX_TRADEABLE_PRICE,
)


class MeanReversionStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        """Store the rolling window size and Z-score threshold."""
        self.window      = params.get("window",      MEAN_REVERSION_WINDOW)
        self.z_threshold = params.get("z_threshold", MEAN_REVERSION_Z_THRESHOLD)
        print(f"[MeanReversionStrategy] Window={self.window} bars, Z-threshold=±{self.z_threshold}")

    def generate_signal(
        self,
        token_id:      str,
        price_history: pd.DataFrame,
        current_price: float,
        current_time:  datetime,
    ) -> Signal:
        """
        Return BUY if Z-score is very negative (price far below mean),
        SELL if Z-score is very positive, HOLD otherwise.
        """
        # Need at least WINDOW bars to compute a meaningful mean
        if len(price_history) < self.window:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Not enough history ({len(price_history)} < {self.window} bars needed)",
            )

        # Skip extreme prices
        if not (MIN_TRADEABLE_PRICE <= current_price <= MAX_TRADEABLE_PRICE):
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Price {current_price:.3f} outside tradeable range",
            )

        # Compute rolling statistics over the last WINDOW bars
        recent = price_history["price"].iloc[-self.window:]
        mean   = recent.mean()
        std    = recent.std()

        # Avoid division by zero if price is completely flat
        if std < 1e-8:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason="Price is flat (std≈0), no signal possible",
            )

        # Z-score: how unusual is the current price?
        z_score = (current_price - mean) / std

        # Price is abnormally LOW → expect it to rise → BUY
        if z_score < -self.z_threshold:
            confidence = min(1.0, abs(z_score) / (self.z_threshold * 2))
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=(
                    f"Price {current_price:.3f} is {abs(z_score):.2f} std devs BELOW "
                    f"mean {mean:.3f} — expect reversion upward"
                ),
            )

        # Price is abnormally HIGH → expect it to fall → SELL
        if z_score > self.z_threshold:
            confidence = min(1.0, abs(z_score) / (self.z_threshold * 2))
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=(
                    f"Price {current_price:.3f} is {z_score:.2f} std devs ABOVE "
                    f"mean {mean:.3f} — expect reversion downward"
                ),
            )

        # Price is within normal range
        return Signal(
            action="HOLD", token_id=token_id, outcome="YES",
            price=current_price, confidence=0.0,
            reason=f"Z-score {z_score:.2f} within ±{self.z_threshold} — no trade",
        )
