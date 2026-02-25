"""
strategies/rsi.py — RSI (Relative Strength Index) strategy.

Core idea:
  RSI measures how fast and how far price has moved recently.
  A score near 0 means the price has fallen very rapidly (oversold) —
  likely to bounce back up.  A score near 100 means it has risen very
  rapidly (overbought) — likely to pull back.

  BUY  when RSI < RSI_OVERSOLD  (default 30) — price fell too fast
  SELL when RSI > RSI_OVERBOUGHT (default 70) — price rose too fast
  HOLD otherwise

How RSI is calculated (Wilder's method):
  1. Compute bar-by-bar price changes.
  2. Separate into gains (positive changes) and losses (absolute negative changes).
  3. Smooth both with an exponential moving average (α = 1 / period).
  4. RS = avg_gain / avg_loss
  5. RSI = 100 − (100 / (1 + RS))

  RSI of 30 → price is in the bottom ~7% of recent momentum (rare downside)
  RSI of 70 → price is in the top ~7% of recent momentum (rare upside)

Confidence scaling:
  The further RSI is past the threshold, the stronger the confidence.
  RSI = 10 on a buy → very high confidence.
  RSI = 29 on a buy → just crossed, low confidence.
"""

from datetime import datetime

import pandas as pd

from models import Signal, Trade
from strategy_base import StrategyBase
from config import (
    RSI_PERIOD,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    MIN_TRADEABLE_PRICE,
    MAX_TRADEABLE_PRICE,
)


class RSIStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        self.period      = params.get("period",     RSI_PERIOD)
        self.oversold    = params.get("oversold",   RSI_OVERSOLD)
        self.overbought  = params.get("overbought", RSI_OVERBOUGHT)
        print(
            f"[RSIStrategy] Period={self.period} bars  |  "
            f"Oversold<{self.oversold}  Overbought>{self.overbought}"
        )

    def generate_signal(
        self,
        token_id:      str,
        price_history: pd.DataFrame,
        current_price: float,
        current_time:  datetime,
    ) -> Signal:
        # Need at least period + 1 bars: one extra to compute the first delta
        min_bars = self.period + 1
        if len(price_history) < min_bars:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Not enough history ({len(price_history)} < {min_bars} bars needed)",
            )

        if not (MIN_TRADEABLE_PRICE <= current_price <= MAX_TRADEABLE_PRICE):
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason=f"Price {current_price:.3f} outside tradeable range",
            )

        rsi = self._compute_rsi(price_history["price"])

        if rsi < self.oversold:
            # How far past the threshold? Scale 0→1 as RSI goes from oversold down to 0.
            confidence = min(1.0, (self.oversold - rsi) / self.oversold)
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=f"RSI={rsi:.1f} below oversold threshold {self.oversold} — expect rebound",
            )

        if rsi > self.overbought:
            confidence = min(1.0, (rsi - self.overbought) / (100.0 - self.overbought))
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence,
                reason=f"RSI={rsi:.1f} above overbought threshold {self.overbought} — expect pullback",
            )

        return Signal(
            action="HOLD", token_id=token_id, outcome="YES",
            price=current_price, confidence=0.0,
            reason=f"RSI={rsi:.1f} — within neutral zone ({self.oversold}–{self.overbought})",
        )

    def _compute_rsi(self, prices: pd.Series) -> float:
        """
        Compute RSI using Wilder's exponential smoothing.

        Uses ewm(alpha=1/period, adjust=False) which exactly replicates
        Wilder's original smoothing formula.
        """
        delta = prices.diff().dropna()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)

        avg_gain = gain.ewm(alpha=1.0 / self.period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1.0 / self.period, adjust=False).mean().iloc[-1]

        if avg_loss < 1e-10:
            return 100.0  # no losses at all → maximum overbought

        rs  = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(rsi, 2)
