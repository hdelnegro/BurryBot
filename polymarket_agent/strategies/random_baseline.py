"""
strategies/random_baseline.py — Random trading strategy (performance benchmark).

Core idea:
  Buy and sell randomly.

Why do this?
  A random strategy represents the absolute floor of performance.
  If your smart strategy (momentum, mean-reversion) can't beat RANDOM,
  it's not working — you might as well flip a coin.

  This is called a "baseline" — it gives us something to compare against.

How it works:
  At each bar, randomly decide to BUY, SELL, or HOLD with configurable
  probabilities.  By default: 10% BUY, 10% SELL, 80% HOLD.
  (Low trade frequency = realistic; prediction markets have big spreads.)

Seeding:
  We use a fixed random seed so results are reproducible.
  The same run always produces the same random decisions.
"""

import random
from datetime import datetime

import pandas as pd

from models import Signal, Trade
from strategy_base import StrategyBase
from config import MIN_TRADEABLE_PRICE, MAX_TRADEABLE_PRICE


class RandomBaselineStrategy(StrategyBase):

    def setup(self, params: dict) -> None:
        """
        Set trade probabilities and random seed.

        params keys:
          buy_prob  — probability of BUY  at any bar (default 0.10 = 10%)
          sell_prob — probability of SELL at any bar (default 0.10 = 10%)
          seed      — random seed for reproducibility (default 42)
        """
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
        """
        Return a random BUY, SELL, or HOLD signal.
        """
        # Skip extreme prices (same rule as other strategies — fair comparison)
        if not (MIN_TRADEABLE_PRICE <= current_price <= MAX_TRADEABLE_PRICE):
            return Signal(
                action="HOLD", token_id=token_id, outcome="YES",
                price=current_price, confidence=0.0,
                reason="Price outside tradeable range",
            )

        # Roll the dice
        roll = random.random()  # uniform float between 0.0 and 1.0

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
