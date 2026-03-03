"""
overreaction_fade.py — Overreaction Fade strategy for prediction markets.

Core idea:
  Prediction market prices overshoot when new information arrives (news-driven overreaction).
  This strategy detects statistically unusual single-bar moves and fades them, expecting a
  partial reversion of 30-60% over the next 1-6 hours.

Unlike all existing strategies, this operates on RETURNS (bar-to-bar changes), not price
levels. It is immune to "stuck at a level" false signals that plague mean-reversion approaches.
"""

from datetime import datetime
import pandas as pd
from shared.models import Signal
from shared.strategy_base import StrategyBase
from config import (
    OVERREACTION_WINDOW,
    OVERREACTION_THRESHOLD,
    OVERREACTION_MIN_VOL,
    MIN_TRADEABLE_PRICE,
    MAX_TRADEABLE_PRICE,
)

_MIN_PRICE = max(MIN_TRADEABLE_PRICE, 0.10)  # tighter floor: avoid near-resolving markets
_MAX_PRICE = min(MAX_TRADEABLE_PRICE, 0.90)


class OverreactionFadeStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        self.window    = params.get("window",    OVERREACTION_WINDOW)
        self.threshold = params.get("threshold", OVERREACTION_THRESHOLD)
        self.min_vol   = params.get("min_vol",   OVERREACTION_MIN_VOL)
        print(
            f"[OverreactionFadeStrategy] window={self.window} bars  "
            f"threshold={self.threshold}σ  min_vol={self.min_vol}"
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

        # Need at least window + 2 bars to compute returns and last/current moves
        min_bars = self.window + 2
        if len(price_history) < min_bars:
            return hold(f"Not enough history ({len(price_history)} < {min_bars} bars)")

        if not (_MIN_PRICE <= current_price <= _MAX_PRICE):
            return hold(f"Price {current_price:.3f} outside tradeable range")

        prices  = price_history["price"]
        returns = prices.diff().dropna()

        rolling_std = returns.iloc[-self.window:].std()
        if rolling_std < self.min_vol:
            return hold(f"Market too flat (vol={rolling_std:.4f} < {self.min_vol})")

        # Two moves to examine
        last_move    = float(prices.iloc[-1] - prices.iloc[-2])   # last completed bar
        current_move = float(current_price   - prices.iloc[-1])   # current bar so far

        last_z    = last_move    / rolling_std  # signed z-score
        current_z = current_move / rolling_std

        def confidence(abs_z: float) -> float:
            severity = min(0.5, (abs_z - self.threshold) / self.threshold * 0.3)
            return round(0.5 + severity, 4)

        # --- Crash signals (BUY) ---
        # Case 1: last bar crashed AND current bar is not continuing the crash
        if last_z <= -self.threshold and current_z > -1.0:
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence(abs(last_z)),
                reason=(
                    f"Overreaction crash: last bar {last_move:+.4f} "
                    f"({last_z:.2f}σ), reversal underway ({current_move:+.4f})"
                ),
            )

        # Case 2: current bar is crashing right now
        if current_z <= -self.threshold:
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence(abs(current_z)),
                reason=(
                    f"Overreaction crash NOW: current move {current_move:+.4f} "
                    f"({current_z:.2f}σ) — fading at the low"
                ),
            )

        # --- Spike signals (SELL) ---
        # Case 3: last bar spiked AND current bar is not continuing the spike
        if last_z >= self.threshold and current_z < 1.0:
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence(abs(last_z)),
                reason=(
                    f"Overreaction spike: last bar {last_move:+.4f} "
                    f"({last_z:.2f}σ), pullback underway ({current_move:+.4f})"
                ),
            )

        # Case 4: current bar is spiking right now
        if current_z >= self.threshold:
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=confidence(abs(current_z)),
                reason=(
                    f"Overreaction spike NOW: current move {current_move:+.4f} "
                    f"({current_z:.2f}σ) — fading at the high"
                ),
            )

        return hold(
            f"No spike detected (last={last_z:.2f}σ, current={current_z:.2f}σ, "
            f"threshold=±{self.threshold}σ)"
        )
