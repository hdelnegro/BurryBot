"""
risk_manager.py — Trade gatekeeper.  Blocks trades that are too risky.

The risk manager sits between the strategy and the portfolio.
The strategy says "BUY" — the risk manager checks if it's safe.

Rules enforced:
  1. BUY:  A single trade cannot exceed 20% of total portfolio value.
  2. BUY:  Total open exposure after the trade cannot exceed 80% of portfolio value.
  3. SELL: Always allowed (reducing risk is always OK).
  4. HOLD: Always allowed (doing nothing is always OK).

These limits prevent the backtest from "all-in" betting, which would be
unrealistic for real trading.
"""

from typing import Dict, Optional, Tuple

from models import Signal
from portfolio import Portfolio
from config import MAX_POSITION_SIZE_FRACTION, MAX_TOTAL_EXPOSURE_FRACTION


class RiskManager:
    """
    Checks whether a proposed trade is within safe limits.

    Usage:
        allowed, size, reason = risk_manager.check_signal(signal, portfolio, current_prices)
        if allowed:
            portfolio.execute_buy(signal, market_slug, size, timestamp)
    """

    def __init__(self):
        self.max_position_fraction = MAX_POSITION_SIZE_FRACTION  # 20%
        self.max_exposure_fraction = MAX_TOTAL_EXPOSURE_FRACTION  # 80%

    def check_signal(
        self,
        signal:         Signal,
        portfolio:      Portfolio,
        current_prices: Dict[str, float],
    ) -> Tuple[bool, float, str]:
        """
        Decide whether a signal should be executed and how much to spend.

        Args:
            signal:          The signal from the strategy.
            portfolio:       Current portfolio state.
            current_prices:  Latest price for every token (for exposure calc).

        Returns:
            A 3-tuple:
              - allowed (bool):   True if the trade can proceed.
              - trade_size (float): USDC to spend on a BUY (0.0 for SELL/HOLD).
              - reason (str):     Explanation of the decision.
        """
        action = signal.action

        # HOLD — always allowed, nothing to check
        if action == "HOLD":
            return True, 0.0, "HOLD — no trade needed"

        # SELL — always allowed
        if action == "SELL":
            pos = portfolio.get_position(signal.token_id)
            if pos is None:
                return False, 0.0, "SELL blocked: we don't own this token"
            return True, 0.0, "SELL allowed"

        # BUY — apply risk rules
        if action == "BUY":
            total_value = portfolio.total_value(current_prices)

            # Guard against zero portfolio (should never happen, but be safe)
            if total_value <= 0:
                return False, 0.0, "BUY blocked: portfolio value is zero or negative"

            # Rule 1: max position size = 20% of portfolio
            max_trade_size = total_value * self.max_position_fraction

            # If we already own this token, only allow buying UP TO the cap
            existing_pos = portfolio.get_position(signal.token_id)
            existing_value = 0.0
            if existing_pos is not None:
                existing_price = current_prices.get(signal.token_id, existing_pos.avg_cost)
                existing_value = existing_pos.shares * existing_price

            available_for_this_position = max(0.0, max_trade_size - existing_value)

            if available_for_this_position <= 0:
                return (
                    False, 0.0,
                    f"BUY blocked: already at max position size "
                    f"(${existing_value:.2f} ≥ {self.max_position_fraction:.0%} of ${total_value:.2f})"
                )

            # Rule 2: total exposure after trade must be ≤ 80%
            current_exposure    = portfolio.get_total_exposure(current_prices)
            max_total_exposure  = total_value * self.max_exposure_fraction
            room_for_more       = max_total_exposure - current_exposure

            if room_for_more <= 0:
                return (
                    False, 0.0,
                    f"BUY blocked: total exposure ${current_exposure:.2f} already at "
                    f"{self.max_exposure_fraction:.0%} limit (${max_total_exposure:.2f})"
                )

            # Rule 3: we must have cash to spend
            if portfolio.cash <= 0:
                return False, 0.0, "BUY blocked: no cash available"

            # Final trade size = minimum of: position cap, exposure room, available cash
            trade_size = min(available_for_this_position, room_for_more, portfolio.cash)

            # Scale by strategy confidence (0.0–1.0)
            trade_size *= signal.confidence if signal.confidence > 0 else 0.5

            # Require at least $1.00 to bother trading
            if trade_size < 1.0:
                return False, 0.0, f"BUY blocked: trade size ${trade_size:.2f} too small (< $1.00)"

            return (
                True, trade_size,
                f"BUY approved: ${trade_size:.2f} "
                f"(confidence={signal.confidence:.0%}, "
                f"exposure={current_exposure:.2f}/{max_total_exposure:.2f})"
            )

        # Unknown action
        return False, 0.0, f"Unknown signal action: {action}"
