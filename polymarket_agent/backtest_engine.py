"""
backtest_engine.py — The main simulation loop.

This is the "heart" of the system.  It steps through time bar by bar,
calls the strategy, checks the risk manager, and executes trades.

Flow:
  for each market:
    for each time bar i:
      history = price_data[:i]      ← only past data (no peeking!)
      signal  = strategy.generate_signal(...)
      if BUY or SELL:
        allowed, size, reason = risk_manager.check_signal(...)
        if allowed:
          portfolio.execute_buy/sell(...)
      equity_curve.append(portfolio.total_value(...))

After the loop:
  metrics.compute_all_metrics(trades, equity_curve)
"""

from typing import Dict, List

import pandas as pd

from models import Market, PriceBar, Signal
from portfolio import Portfolio
from risk_manager import RiskManager
from strategy_base import StrategyBase
import metrics as metrics_module


class BacktestEngine:
    """
    Runs a backtest for one strategy across a set of markets.

    Usage:
        engine = BacktestEngine(strategy, portfolio, risk_manager)
        results = engine.run(markets, price_data)
        metrics.print_results(results, strategy.name)
    """

    def __init__(
        self,
        strategy:     StrategyBase,
        portfolio:    Portfolio,
        risk_manager: RiskManager,
    ):
        self.strategy     = strategy
        self.portfolio    = portfolio
        self.risk_manager = risk_manager

        # Equity curve: one entry per bar across all markets
        # (we record after processing each market's bar)
        self.equity_curve: List[float] = []

    def run(
        self,
        markets:    List[Market],
        price_data: Dict[str, List[PriceBar]],
    ) -> dict:
        """
        Execute the full backtest.

        Args:
            markets:    List of Market objects to simulate.
            price_data: Dict of token_id → list of PriceBar objects.
                        Only YES token data is required for now.

        Returns:
            A metrics dictionary (from metrics.compute_all_metrics).
        """
        print(f"\nStarting backtest: {self.strategy.name}")
        print(f"Markets: {len(markets)} | Starting cash: ${self.portfolio.cash:.2f}\n")

        # Build a DataFrame for each token so we can use pandas slicing
        # Dict: token_id → DataFrame with datetime index and "price" column
        price_dfs: Dict[str, pd.DataFrame] = {}
        for token_id, bars in price_data.items():
            if not bars:
                continue
            df = pd.DataFrame(
                {"price": [b.price for b in bars]},
                index=[b.timestamp for b in bars],
            )
            df.index = pd.DatetimeIndex(df.index)
            df.sort_index(inplace=True)
            price_dfs[token_id] = df

        # Determine the maximum number of bars across all tokens
        # (different markets have different amounts of history)
        max_bars = max((len(df) for df in price_dfs.values()), default=0)
        if max_bars == 0:
            print("ERROR: No price data available. Cannot run backtest.")
            return {}

        print(f"Max time steps: {max_bars} bars per token")

        # ----------------------------------------------------------------
        # Main simulation loop: iterate bar by bar
        # ----------------------------------------------------------------
        for bar_index in range(1, max_bars):
            # At each bar, collect the current price of every open position
            # so we can calculate portfolio value and risk exposure.
            current_prices: Dict[str, float] = {}

            for market in markets:
                token_id = market.yes_token_id
                df = price_dfs.get(token_id)

                if df is None or bar_index >= len(df):
                    continue  # no data for this market at this time

                # Current bar info
                current_time  = df.index[bar_index]
                current_price = float(df["price"].iloc[bar_index])

                current_prices[token_id] = current_price

                # Price history UP TO (but not including) the current bar
                history = df.iloc[:bar_index]

                # Ask the strategy what to do
                signal = self.strategy.generate_signal(
                    token_id      = token_id,
                    price_history = history,
                    current_price = current_price,
                    current_time  = current_time,
                )

                # Ask the risk manager if the trade is safe
                allowed, trade_size, reason = self.risk_manager.check_signal(
                    signal         = signal,
                    portfolio      = self.portfolio,
                    current_prices = current_prices,
                )

                if not allowed:
                    continue

                # Execute the trade
                if signal.action == "BUY":
                    trade = self.portfolio.execute_buy(
                        signal          = signal,
                        market_slug     = market.slug,
                        trade_size_usdc = trade_size,
                        timestamp       = current_time,
                    )
                    if trade:
                        self.strategy.on_trade_executed(trade)

                elif signal.action == "SELL":
                    trade = self.portfolio.execute_sell(
                        signal      = signal,
                        market_slug = market.slug,
                        timestamp   = current_time,
                    )
                    if trade:
                        self.strategy.on_trade_executed(trade)

            # Record equity after processing all markets for this bar
            pv = self.portfolio.total_value(current_prices)
            self.equity_curve.append(pv)

        # ----------------------------------------------------------------
        # Force-close all remaining positions at final prices
        # ----------------------------------------------------------------
        final_prices: Dict[str, float] = {}
        for token_id, df in price_dfs.items():
            if len(df) > 0:
                final_prices[token_id] = float(df["price"].iloc[-1])

        self._close_all_positions(final_prices)

        # ----------------------------------------------------------------
        # Compute and return metrics
        # ----------------------------------------------------------------
        final_value = self.portfolio.total_value(final_prices)

        return metrics_module.compute_all_metrics(
            trades        = self.portfolio.trade_log,
            equity_curve  = self.equity_curve,
            starting_cash = self.portfolio.starting_cash,
            final_value   = final_value,
        )

    def _close_all_positions(self, final_prices: Dict[str, float]) -> None:
        """
        Liquidate all remaining open positions at the end of the backtest.

        This is needed so the final portfolio value reflects reality:
        if we're holding tokens, they have a value — we "cash out" at the
        last known price.
        """
        open_tokens = list(self.portfolio.positions.keys())

        if not open_tokens:
            return

        print(f"\nClosing {len(open_tokens)} remaining open position(s) at final prices...")

        from datetime import datetime
        close_time = datetime.utcnow()

        for token_id in open_tokens:
            pos = self.portfolio.positions.get(token_id)
            if pos is None:
                continue

            price = final_prices.get(token_id, pos.avg_cost)

            # Build a synthetic SELL signal at the final price
            from models import Signal
            sell_signal = Signal(
                action     = "SELL",
                token_id   = token_id,
                outcome    = pos.outcome,
                price      = price,
                reason     = "End of backtest — forced liquidation",
                confidence = 1.0,
            )

            self.portfolio.execute_sell(
                signal      = sell_signal,
                market_slug = pos.market_slug,
                timestamp   = close_time,
            )
