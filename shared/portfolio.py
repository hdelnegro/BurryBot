"""
shared/portfolio.py — Tracks cash, positions, and calculates total portfolio value.

Platform-agnostic: works identically for Polymarket, Kalshi, and future platforms.
Each agent's config.py must define DEFAULT_STARTING_CASH and TRADE_FEE_RATE.
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from shared.models import Position, Trade, Signal
from config import DEFAULT_STARTING_CASH, TRADE_FEE_RATE


class Portfolio:
    """
    Simulated brokerage account for backtesting and paper trading.

    All currency values are in USDC (USD-equivalent).
    """

    def __init__(self, starting_cash: float = DEFAULT_STARTING_CASH):
        self.cash: float = starting_cash
        self.starting_cash: float = starting_cash
        self.positions: Dict[str, Position] = {}
        self.trade_log: List[Trade] = []

    def get_position(self, token_id: str) -> Optional[Position]:
        """Return the open position for a token, or None if we don't own it."""
        return self.positions.get(token_id)

    def get_position_value(self, token_id: str, current_price: float) -> float:
        pos = self.positions.get(token_id)
        if pos is None:
            return 0.0
        return pos.shares * current_price

    def get_total_exposure(self, current_prices: Dict[str, float]) -> float:
        total = 0.0
        for token_id, pos in self.positions.items():
            price = current_prices.get(token_id, pos.avg_cost)
            total += pos.shares * price
        return total

    def total_value(self, current_prices: Dict[str, float]) -> float:
        return self.cash + self.get_total_exposure(current_prices)

    def execute_buy(
        self,
        signal: Signal,
        market_slug: str,
        trade_size_usdc: float,
        timestamp: datetime,
    ) -> Optional[Trade]:
        price = signal.price
        if price <= 0:
            return None

        fee        = trade_size_usdc * TRADE_FEE_RATE
        total_cost = trade_size_usdc + fee

        if total_cost > self.cash:
            print(
                f"  [Portfolio] BUY rejected: need ${total_cost:.2f} "
                f"but only have ${self.cash:.2f}"
            )
            return None

        shares = trade_size_usdc / price
        self.cash -= total_cost

        token_id = signal.token_id
        if token_id in self.positions:
            pos = self.positions[token_id]
            total_shares = pos.shares + shares
            total_spent  = pos.shares * pos.avg_cost + trade_size_usdc
            pos.avg_cost = total_spent / total_shares
            pos.shares   = total_shares
        else:
            self.positions[token_id] = Position(
                token_id    = token_id,
                outcome     = signal.outcome,
                market_slug = market_slug,
                shares      = shares,
                avg_cost    = price,
                opened_at   = timestamp,
            )

        trade = Trade(
            trade_id   = str(uuid.uuid4())[:8],
            token_id   = token_id,
            market_slug= market_slug,
            action     = "BUY",
            outcome    = signal.outcome,
            shares     = shares,
            price      = price,
            fee        = fee,
            total_cost = total_cost,
            timestamp  = timestamp,
            pnl        = 0.0,
        )
        self.trade_log.append(trade)
        return trade

    def execute_sell(
        self,
        signal: Signal,
        market_slug: str,
        timestamp: datetime,
    ) -> Optional[Trade]:
        token_id = signal.token_id
        pos = self.positions.get(token_id)

        if pos is None:
            return None

        price        = signal.price
        proceeds     = pos.shares * price
        fee          = proceeds * TRADE_FEE_RATE
        net_proceeds = proceeds - fee
        cost_basis   = pos.shares * pos.avg_cost
        pnl          = net_proceeds - cost_basis

        self.cash += net_proceeds
        del self.positions[token_id]

        trade = Trade(
            trade_id    = str(uuid.uuid4())[:8],
            token_id    = token_id,
            market_slug = market_slug,
            action      = "SELL",
            outcome     = pos.outcome,
            shares      = pos.shares,
            price       = price,
            fee         = fee,
            total_cost  = -net_proceeds,
            timestamp   = timestamp,
            pnl         = pnl,
        )
        self.trade_log.append(trade)
        return trade

    def summary(self, current_prices: Dict[str, float]) -> dict:
        return {
            "cash":           self.cash,
            "open_positions": len(self.positions),
            "total_exposure": self.get_total_exposure(current_prices),
            "total_value":    self.total_value(current_prices),
            "total_trades":   len(self.trade_log),
        }
