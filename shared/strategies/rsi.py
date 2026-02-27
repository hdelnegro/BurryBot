"""
shared/strategies/rsi.py — RSI (Relative Strength Index) strategy.

Core idea:
  BUY  when RSI < RSI_OVERSOLD  (default 30) — price fell too fast
  SELL when RSI > RSI_OVERBOUGHT (default 70) — price rose too fast

Works on any 0.0–1.0 probability market (Polymarket, Kalshi, etc.).
"""

from datetime import datetime

import pandas as pd

from shared.models import Signal, Trade
from shared.strategy_base import StrategyBase
from config import (
    RSI_PERIOD,
    RSI_OVERSOLD,
    RSI_OVERBOUGHT,
    MIN_TRADEABLE_PRICE,
    MAX_TRADEABLE_PRICE,
)


class RSIStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        self.period     = params.get("period",     RSI_PERIOD)
        self.oversold   = params.get("oversold",   RSI_OVERSOLD)
        self.overbought = params.get("overbought", RSI_OVERBOUGHT)
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
        delta    = prices.diff().dropna()
        gain     = delta.clip(lower=0)
        loss     = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1.0 / self.period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1.0 / self.period, adjust=False).mean().iloc[-1]
        if avg_loss < 1e-10:
            return 100.0
        rs  = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(rsi, 2)
