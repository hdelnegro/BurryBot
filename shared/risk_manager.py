"""
shared/risk_manager.py — Trade gatekeeper. Blocks trades that are too risky.

Platform-agnostic: works for any prediction market platform.
Each agent's config.py must define MAX_POSITION_SIZE_FRACTION and
MAX_TOTAL_EXPOSURE_FRACTION.
"""

from typing import Dict, Optional, Tuple

from shared.models import Signal
from shared.portfolio import Portfolio
from config import MAX_POSITION_SIZE_FRACTION, MAX_TOTAL_EXPOSURE_FRACTION


class RiskManager:
    """
    Checks whether a proposed trade is within safe limits.

    Rules:
      1. BUY:  A single trade cannot exceed 20% of total portfolio value.
      2. BUY:  Total open exposure after the trade cannot exceed 80%.
      3. SELL: Always allowed.
      4. HOLD: Always allowed.
    """

    def __init__(self):
        self.max_position_fraction = MAX_POSITION_SIZE_FRACTION
        self.max_exposure_fraction = MAX_TOTAL_EXPOSURE_FRACTION

    def check_signal(
        self,
        signal:         Signal,
        portfolio:      Portfolio,
        current_prices: Dict[str, float],
    ) -> Tuple[bool, float, str]:
        action = signal.action

        if action == "HOLD":
            return True, 0.0, "HOLD — no trade needed"

        if action == "SELL":
            pos = portfolio.get_position(signal.token_id)
            if pos is None:
                return False, 0.0, "SELL blocked: we don't own this token"
            return True, 0.0, "SELL allowed"

        if action == "BUY":
            total_value = portfolio.total_value(current_prices)

            if total_value <= 0:
                return False, 0.0, "BUY blocked: portfolio value is zero or negative"

            max_trade_size = total_value * self.max_position_fraction

            existing_pos   = portfolio.get_position(signal.token_id)
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

            current_exposure   = portfolio.get_total_exposure(current_prices)
            max_total_exposure = total_value * self.max_exposure_fraction
            room_for_more      = max_total_exposure - current_exposure

            if room_for_more <= 0:
                return (
                    False, 0.0,
                    f"BUY blocked: total exposure ${current_exposure:.2f} already at "
                    f"{self.max_exposure_fraction:.0%} limit (${max_total_exposure:.2f})"
                )

            if portfolio.cash <= 0:
                return False, 0.0, "BUY blocked: no cash available"

            trade_size = min(available_for_this_position, room_for_more, portfolio.cash)
            trade_size *= signal.confidence if signal.confidence > 0 else 0.5

            if trade_size < 1.0:
                return False, 0.0, f"BUY blocked: trade size ${trade_size:.2f} too small (< $1.00)"

            return (
                True, trade_size,
                f"BUY approved: ${trade_size:.2f} "
                f"(confidence={signal.confidence:.0%}, "
                f"exposure={current_exposure:.2f}/{max_total_exposure:.2f})"
            )

        return False, 0.0, f"Unknown signal action: {action}"
