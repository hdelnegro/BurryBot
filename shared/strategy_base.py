"""
shared/strategy_base.py — Abstract base class that every trading strategy must inherit.

By forcing every strategy to implement the same methods, the backtest
engine and paper trader can run any strategy without knowing anything
specific about it.
"""

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from shared.models import Signal, Trade


class StrategyBase(ABC):
    """
    Base class for all trading strategies.

    To create a new strategy:
      1. Create a new file in the agent's strategies/ or shared/strategies/
      2. Write a class that inherits from StrategyBase
      3. Implement the 3 methods below
      4. Register it in the agent's main.py STRATEGY_MAP
    """

    @abstractmethod
    def setup(self, params: dict) -> None:
        """
        Called ONCE before the backtest/paper trading starts.

        Args:
            params: A dictionary of settings, e.g. {"lookback": 10}
        """
        ...

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

        Args:
            token_id:      The token being evaluated.
            price_history: DataFrame with "price" column and datetime index,
                           sorted oldest → newest. Last row is bar before current.
            current_price: The price at the current bar.
            current_time:  The datetime of the current bar.

        Returns:
            A Signal with action "BUY", "SELL", or "HOLD".
        """
        ...

    def on_trade_executed(self, trade: Trade) -> None:
        """
        Called after a trade is successfully executed.

        Default implementation does nothing. Override to track fills.
        """
        pass

    @property
    def name(self) -> str:
        """Return the class name as a readable strategy name."""
        return self.__class__.__name__
