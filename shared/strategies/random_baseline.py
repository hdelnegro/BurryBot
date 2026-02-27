"""
shared/strategies/random_baseline.py — Random trading strategy (performance benchmark).

A random strategy represents the absolute floor of performance.
If your smart strategy can't beat RANDOM, it's not working.

Works on any 0.0–1.0 probability market (Polymarket, Kalshi, etc.).
"""

import random
from datetime import datetime

import pandas as pd

from shared.models import Signal, Trade
from shared.strategy_base import StrategyBase
from config import MIN_TRADEABLE_PRICE, MAX_TRADEABLE_PRICE


class RandomBaselineStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        self.buy_prob  = params.get("buy_prob",  0.10)
        self.sell_prob = params.get("sell_prob", 0.10)
        seed           = params.get("seed",      42)
        random.seed(seed)
        print(
            f"[RandomBaseline] buy_prob={self.buy_prob:.0%}, "
            f"sell_prob={self.sell_prob:.0%}, seed={seed}"
        )

    def generate_signal(
        self,
        token_id:      str,
        price_history: pd.DataFrame,
        current_price: float,
        current_time:  datetime,
    ) -> Signal:
        if not (MIN_TRADEABLE_PRICE <= current_price <= MAX_TRADEABLE_PRICE):
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason="Price outside tradeable range",
            )

        roll = random.random()

        if roll < self.buy_prob:
            return Signal(
                action="BUY", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.5,
                reason="Random BUY (no logic — baseline benchmark)",
            )

        if roll < self.buy_prob + self.sell_prob:
            return Signal(
                action="SELL", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.5,
                reason="Random SELL (no logic — baseline benchmark)",
            )

        return Signal(
            action="HOLD", token_id=token_id, outcome="YES",
            price=current_price, confidence=0.0,
            reason="Random HOLD",
        )
