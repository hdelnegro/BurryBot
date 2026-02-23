"""
portfolio.py — Tracks cash, positions, and calculates total portfolio value.

Think of this as the financial ledger / brokerage account for the backtest.

Key responsibilities:
  - Keep track of how much cash we have (starts at DEFAULT_STARTING_CASH)
  - Keep track of all open positions (tokens we currently hold)
  - Execute BUY and SELL orders, updating cash and positions accordingly
  - Record every trade in a permanent log
  - Calculate total portfolio value at any moment
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from models import Position, Trade, Signal
from config import DEFAULT_STARTING_CASH, TRADE_FEE_RATE


class Portfolio:
    """
    Simulated brokerage account for backtesting.

    All currency values are in USDC (USD-equivalent).
    """

    def __init__(self, starting_cash: float = DEFAULT_STARTING_CASH):
        """
        Create a fresh portfolio with the given starting cash and no positions.

        Args:
            starting_cash: How much USDC we start with.
        """
        self.cash: float = starting_cash
        self.starting_cash: float = starting_cash

        # Open positions: keyed by token_id → Position
        self.positions: Dict[str, Position] = {}

        # Complete history of all executed trades
        self.trade_log: List[Trade] = []

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    def get_position(self, token_id: str) -> Optional[Position]:
        """Return the open position for a token, or None if we don't own it."""
        return self.positions.get(token_id)

    def get_position_value(self, token_id: str, current_price: float) -> float:
        """
        Return the current market value of a position.

        Value = shares_held × current_price
        """
        pos = self.positions.get(token_id)
        if pos is None:
            return 0.0
        return pos.shares * current_price

    def get_total_exposure(self, current_prices: Dict[str, float]) -> float:
        """
        Return the total current market value of ALL open positions.

        Args:
            current_prices: dict of token_id → current price
        """
        total = 0.0
        for token_id, pos in self.positions.items():
            price = current_prices.get(token_id, pos.avg_cost)
            total += pos.shares * price
        return total

    def total_value(self, current_prices: Dict[str, float]) -> float:
        """
        Return total portfolio value = cash + value of all open positions.

        Args:
            current_prices: dict of token_id → current price
        """
        return self.cash + self.get_total_exposure(current_prices)

    # ------------------------------------------------------------------
    # Execute a BUY
    # ------------------------------------------------------------------

    def execute_buy(
        self,
        signal: Signal,
        market_slug: str,
        trade_size_usdc: float,
        timestamp: datetime,
    ) -> Optional[Trade]:
        """
        Buy as many shares as `trade_size_usdc` USDC can buy at the signal price.

        Args:
            signal:          The BUY signal from the strategy.
            market_slug:     Human-readable market name (for logging).
            trade_size_usdc: How many USDC to spend (before fees).
            timestamp:       Current backtest time.

        Returns:
            The completed Trade object, or None if the trade could not execute
            (e.g., insufficient cash).
        """
        price = signal.price
        if price <= 0:
            return None

        # Calculate fee and total cost
        fee        = trade_size_usdc * TRADE_FEE_RATE
        total_cost = trade_size_usdc + fee

        # Check we have enough cash
        if total_cost > self.cash:
            print(
                f"  [Portfolio] BUY rejected: need ${total_cost:.2f} "
                f"but only have ${self.cash:.2f}"
            )
            return None

        # How many shares does that buy?
        shares = trade_size_usdc / price

        # Deduct cash
        self.cash -= total_cost

        # Update or create the position
        token_id = signal.token_id
        if token_id in self.positions:
            # We already own some — update average cost
            pos = self.positions[token_id]
            total_shares    = pos.shares + shares
            total_spent     = pos.shares * pos.avg_cost + trade_size_usdc
            pos.avg_cost    = total_spent / total_shares
            pos.shares      = total_shares
        else:
            # New position
            self.positions[token_id] = Position(
                token_id    = token_id,
                outcome     = signal.outcome,
                market_slug = market_slug,
                shares      = shares,
                avg_cost    = price,
                opened_at   = timestamp,
            )

        # Create trade record
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
            pnl        = 0.0,  # PnL is only calculated on SELL
        )
        self.trade_log.append(trade)
        return trade

    # ------------------------------------------------------------------
    # Execute a SELL
    # ------------------------------------------------------------------

    def execute_sell(
        self,
        signal: Signal,
        market_slug: str,
        timestamp: datetime,
    ) -> Optional[Trade]:
        """
        Sell ALL shares of the token in `signal`.

        We always sell the full position (simpler; avoids partial-fill complexity).

        Args:
            signal:      The SELL signal from the strategy.
            market_slug: Human-readable market name.
            timestamp:   Current backtest time.

        Returns:
            The completed Trade object, or None if we don't own this token.
        """
        token_id = signal.token_id
        pos = self.positions.get(token_id)

        if pos is None:
            # Nothing to sell — strategy says SELL but we don't own it
            return None

        price      = signal.price
        proceeds   = pos.shares * price
        fee        = proceeds * TRADE_FEE_RATE
        net_proceeds = proceeds - fee

        # Calculate profit or loss
        cost_basis = pos.shares * pos.avg_cost
        pnl        = net_proceeds - cost_basis

        # Add net proceeds back to cash
        self.cash += net_proceeds

        # Remove the position
        del self.positions[token_id]

        # Create trade record
        trade = Trade(
            trade_id    = str(uuid.uuid4())[:8],
            token_id    = token_id,
            market_slug = market_slug,
            action      = "SELL",
            outcome     = pos.outcome,
            shares      = pos.shares,
            price       = price,
            fee         = fee,
            total_cost  = -net_proceeds,  # negative = we received money
            timestamp   = timestamp,
            pnl         = pnl,
        )
        self.trade_log.append(trade)
        return trade

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self, current_prices: Dict[str, float]) -> dict:
        """Return a snapshot of the current portfolio state."""
        return {
            "cash":          self.cash,
            "open_positions": len(self.positions),
            "total_exposure": self.get_total_exposure(current_prices),
            "total_value":    self.total_value(current_prices),
            "total_trades":   len(self.trade_log),
        }
