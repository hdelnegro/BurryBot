"""
strategy_base.py — Abstract base class that every trading strategy must inherit.

"Abstract" means: this file defines WHAT methods a strategy must have,
but not HOW they work.  Each strategy fills in the "how."

By forcing every strategy to implement the same 3 methods, the backtest
engine can run any strategy without knowing anything specific about it.
"""

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from models import Signal, Trade


class StrategyBase(ABC):
    """
    Base class for all trading strategies.

    To create a new strategy:
      1. Create a new file in strategies/
      2. Write a class that inherits from StrategyBase:
             class MyStrategy(StrategyBase):
      3. Implement the 3 methods below.
      4. Register it in main.py's STRATEGY_MAP dictionary.

    That's it — the backtest engine handles everything else.
    """

    # ------------------------------------------------------------------
    # Method 1: setup
    # ------------------------------------------------------------------
    @abstractmethod
    def setup(self, params: dict) -> None:
        """
        Called ONCE before the backtest starts.

        Use this to store strategy parameters (like lookback windows)
        and initialise any internal state (like counters or lists).

        Args:
            params: A dictionary of settings, e.g. {"lookback": 10}
                    The backtest engine passes config.py values here.
        """
        ...

    # ------------------------------------------------------------------
    # Method 2: generate_signal
    # ------------------------------------------------------------------
    @abstractmethod
    def generate_signal(
        self,
        token_id:      str,
        price_history: pd.DataFrame,
        current_price: float,
        current_time:  datetime,
    ) -> Signal:
        """
        Called at every time step for every token.

        The engine gives you all price data UP TO (but NOT including) the
        current bar — this prevents "peeking into the future" (lookahead bias).

        Args:
            token_id:      The token being evaluated.
            price_history: A DataFrame with one column "price" and a datetime
                           index.  Rows are sorted oldest → newest.
                           The LAST row is the bar just before the current one.
            current_price: The price at the current bar (the one we're deciding on).
            current_time:  The datetime of the current bar.

        Returns:
            A Signal with action "BUY", "SELL", or "HOLD".
        """
        ...

    # ------------------------------------------------------------------
    # Method 3: on_trade_executed  (optional — has a default no-op)
    # ------------------------------------------------------------------
    def on_trade_executed(self, trade: Trade) -> None:
        """
        Called by the engine AFTER a trade is successfully executed.

        Most strategies don't need this — it's here for strategies that
        want to update internal state based on fills (e.g. momentum with
        position tracking).

        The default implementation does nothing.

        Args:
            trade: The completed Trade object (see models.py).
        """
        pass  # Do nothing by default

    # ------------------------------------------------------------------
    # Convenience property: strategy name for display
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        """Return the class name as a readable strategy name."""
        return self.__class__.__name__
