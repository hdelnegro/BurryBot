"""
shared/strategies/mean_reversion.py — Mean-reversion trading strategy.

Core idea:
  "If the price has drifted far below its recent average (statistically
   unlikely), it will probably snap back up — so BUY."

Works on any 0.0–1.0 probability market (Polymarket, Kalshi, etc.).
"""

from datetime import datetime

import pandas as pd
import numpy as np

from shared.models import Signal, Trade
from shared.strategy_base import StrategyBase
from config import (
    MEAN_REVERSION_Z_THRESHOLD,
    MEAN_REVERSION_WINDOW,
    MIN_TRADEABLE_PRICE,
    MAX_TRADEABLE_PRICE,
)


class MeanReversionStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
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
        if len(price_history) < self.window:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Not enough history ({len(price_history)} < {self.window} bars needed)",
            )

        if not (MIN_TRADEABLE_PRICE <= current_price <= MAX_TRADEABLE_PRICE):
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Price {current_price:.3f} outside tradeable range",
            )

        recent = price_history["price"].iloc[-self.window:]
        mean   = recent.mean()
        std    = recent.std()

        if std < 1e-8:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason="Price is flat (std≈0), no signal possible",
            )

        z_score = (current_price - mean) / std

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

        return Signal(
            action="HOLD", token_id=token_id, outcome="YES",
            price=current_price, confidence=0.0,
            reason=f"Z-score {z_score:.2f} within ±{self.z_threshold} — no trade",
        )
