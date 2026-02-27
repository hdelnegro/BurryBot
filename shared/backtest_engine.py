"""
shared/backtest_engine.py — The main simulation loop.

Platform-agnostic: steps through time bar by bar, calls the strategy,
checks the risk manager, and executes trades.
"""

from datetime import datetime
from typing import Dict, List

import pandas as pd

from shared.models import Market, PriceBar, Signal
from shared.portfolio import Portfolio
from shared.risk_manager import RiskManager
from shared.strategy_base import StrategyBase
from shared import metrics as metrics_module


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
        self.equity_curve: List[float] = []

    def run(
        self,
        markets:    List[Market],
        price_data: Dict[str, List[PriceBar]],
    ) -> dict:
        print(f"\nStarting backtest: {self.strategy.name}")
        print(f"Markets: {len(markets)} | Starting cash: ${self.portfolio.cash:.2f}\n")

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

        max_bars = max((len(df) for df in price_dfs.values()), default=0)
        if max_bars == 0:
            print("ERROR: No price data available. Cannot run backtest.")
            return {}

        print(f"Max time steps: {max_bars} bars per token")

        for bar_index in range(1, max_bars):
            current_prices: Dict[str, float] = {}

            for market in markets:
                token_id = market.yes_token_id
                df = price_dfs.get(token_id)

                if df is None or bar_index >= len(df):
                    continue

                current_time  = df.index[bar_index]
                current_price = float(df["price"].iloc[bar_index])
                current_prices[token_id] = current_price

                history = df.iloc[:bar_index]

                signal = self.strategy.generate_signal(
                    token_id      = token_id,
                    price_history = history,
                    current_price = current_price,
                    current_time  = current_time,
                )

                allowed, trade_size, reason = self.risk_manager.check_signal(
                    signal         = signal,
                    portfolio      = self.portfolio,
                    current_prices = current_prices,
                )

                if not allowed:
                    continue

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

            pv = self.portfolio.total_value(current_prices)
            self.equity_curve.append(pv)

        final_prices: Dict[str, float] = {}
        for token_id, df in price_dfs.items():
            if len(df) > 0:
                final_prices[token_id] = float(df["price"].iloc[-1])

        self._close_all_positions(final_prices)

        final_value = self.portfolio.total_value(final_prices)
        return metrics_module.compute_all_metrics(
            trades        = self.portfolio.trade_log,
            equity_curve  = self.equity_curve,
            starting_cash = self.portfolio.starting_cash,
            final_value   = final_value,
        )

    def _close_all_positions(self, final_prices: Dict[str, float]) -> None:
        open_tokens = list(self.portfolio.positions.keys())
        if not open_tokens:
            return

        print(f"\nClosing {len(open_tokens)} remaining open position(s) at final prices...")
        close_time = datetime.utcnow()

        for token_id in open_tokens:
            pos = self.portfolio.positions.get(token_id)
            if pos is None:
                continue

            price = final_prices.get(token_id, pos.avg_cost)

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
