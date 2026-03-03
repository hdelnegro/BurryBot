"""
btc_5min_momentum.py — BTC 5-minute streak momentum strategy.

Designed specifically for Polymarket's btc-updown-5m binary markets.

Core idea:
  BTC exhibits short-term momentum at 5-minute scales. When recent resolved
  markets show a streak (e.g., 3 consecutive UP results), the next market has
  a mild positive bias. The strategy fades mispricing: it buys when the market
  price underestimates the streak's implied probability, and sells when it
  overestimates.

Two operating modes:
  WARM (cross-market history): streak-based momentum on resolved market outcomes.
    Each bar = closing price of a previous resolved market. std > 0.35 (bimodal).
  COLD (intra-market history): slope-based drift on live tick prices within the
    current 5-min window. std < 0.35 (prices evolving around 0.4-0.6).

Unlike RSI/mean_reversion/overreaction_fade — all designed for long-duration
prediction markets — this strategy operates on the binary outcome structure
of 5-minute markets and BTC's short-term directional persistence.
"""

import numpy as np
from datetime import datetime
import pandas as pd
from shared.models import Signal
from shared.strategy_base import StrategyBase
from config import (
    FIVE_MIN_BTC_LOOKBACK,
    FIVE_MIN_BTC_DECAY,
    FIVE_MIN_BTC_MIN_EDGE,
    FIVE_MIN_BTC_RESOLUTION_HIGH,
    FIVE_MIN_BTC_RESOLUTION_LOW,
    FIVE_MIN_BTC_INTRABAR_LOOKBACK,
    FIVE_MIN_BTC_SLOPE_MULTIPLIER,
)

_CROSS_MARKET_STD_THRESHOLD = 0.35   # std above this → we have resolved market bars
_PRICE_FLOOR = 0.05
_PRICE_CEIL  = 0.95


class BtcFiveMinMomentumStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        self.lookback          = params.get("lookback",          FIVE_MIN_BTC_LOOKBACK)
        self.decay             = params.get("decay",             FIVE_MIN_BTC_DECAY)
        self.min_edge          = params.get("min_edge",          FIVE_MIN_BTC_MIN_EDGE)
        self.resolution_high   = params.get("resolution_high",   FIVE_MIN_BTC_RESOLUTION_HIGH)
        self.resolution_low    = params.get("resolution_low",    FIVE_MIN_BTC_RESOLUTION_LOW)
        self.intrabar_lookback = params.get("intrabar_lookback", FIVE_MIN_BTC_INTRABAR_LOOKBACK)
        self.slope_multiplier  = params.get("slope_multiplier",  FIVE_MIN_BTC_SLOPE_MULTIPLIER)
        print(
            f"[BtcFiveMinMomentumStrategy] lookback={self.lookback}  "
            f"decay={self.decay}  min_edge={self.min_edge}  "
            f"resolution={self.resolution_low}/{self.resolution_high}"
        )

    def generate_signal(
        self,
        token_id:      str,
        price_history: pd.DataFrame,
        current_price: float,
        current_time:  datetime,
    ) -> Signal:

        def hold(reason: str) -> Signal:
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0, reason=reason,
            )

        if len(price_history) < 2:
            return hold(f"Not enough history ({len(price_history)} bars)")

        if not (_PRICE_FLOOR <= current_price <= _PRICE_CEIL):
            return hold(f"Price {current_price:.3f} near resolution — skipping")

        prices = price_history["price"]
        is_cross_market = prices.std() > _CROSS_MARKET_STD_THRESHOLD

        if is_cross_market:
            edge, reason = self._streak_signal(prices, current_price)
        else:
            edge, reason = self._drift_signal(prices, current_price)

        def confidence(abs_edge: float) -> float:
            return round(min(1.0, 0.5 + (abs_edge - self.min_edge) / 0.25 * 0.5), 4)

        if edge > self.min_edge:
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence(edge),
                reason=reason,
            )
        elif edge < -self.min_edge:
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence(-edge),
                reason=reason,
            )
        return hold(reason)

    def _streak_signal(self, prices: pd.Series, current_price: float):
        """WARM state: exponentially-weighted streak on resolved market closing bars."""
        recent = prices.iloc[-self.lookback:]
        outcomes = recent.apply(
            lambda p: 1 if p > self.resolution_high else (-1 if p < self.resolution_low else 0)
        ).values
        n = len(outcomes)
        weights = np.array([self.decay ** (n - 1 - i) for i in range(n)])
        score = float((outcomes * weights).sum() / weights.sum())   # [-1, 1]
        p_up = 0.5 + score * 0.40
        edge = p_up - current_price
        decisive = int((outcomes != 0).sum())
        direction = "UP" if score > 0 else "DOWN"
        reason = (
            f"Cross-market streak: score={score:+.3f} ({decisive}/{n} decisive bars, "
            f"bias={direction}) -> p_up={p_up:.3f} vs market={current_price:.3f}, "
            f"edge={edge:+.3f}"
        )
        return edge, reason

    def _drift_signal(self, prices: pd.Series, current_price: float):
        """COLD state: linear drift on intra-market tick bars."""
        recent = prices.iloc[-self.intrabar_lookback:]
        if len(recent) < 2:
            return 0.0, "Not enough intra-market bars for drift"
        x = np.arange(len(recent), dtype=float)
        slope = float(np.polyfit(x, recent.values, 1)[0])
        edge = slope * self.slope_multiplier
        direction = "rising" if slope > 0 else "falling"
        reason = (
            f"Intra-market drift: slope={slope:+.4f}/bar ({direction}), "
            f"scaled edge={edge:+.3f} vs min_edge={self.min_edge:.2f}"
        )
        return edge, reason
