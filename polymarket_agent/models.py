"""
models.py — Data definitions for the Polymarket backtesting agent.

These are simple "containers" (dataclasses) that hold information as it flows
through the system.  Think of each one like a row in a spreadsheet.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------
@dataclass
class Market:
    """
    Represents one Polymarket prediction market.

    Example: "Will Bitcoin be above $100k on Dec 31 2025?"
    - YES token → token_id = "0xabc..."
    - NO  token → token_id = "0xdef..."
    """
    condition_id: str        # Unique ID Polymarket uses to identify this market
    question: str            # The plain-English question
    slug: str                # URL-friendly name, e.g. "btc-above-100k-dec-2025"
    yes_token_id: str        # Token ID for the YES outcome
    no_token_id: str         # Token ID for the NO  outcome
    end_date: Optional[str]  # When the market closes (ISO date string or None)
    is_resolved: bool        # True if the market already has a winner
    outcome: Optional[str]   # "Yes" / "No" / None (None if unresolved)


# ---------------------------------------------------------------------------
# PriceBar
# ---------------------------------------------------------------------------
@dataclass
class PriceBar:
    """
    One price observation for a token at a specific moment in time.

    Prices on Polymarket are probabilities: 0.0 = 0% chance, 1.0 = 100% chance.
    For example, price=0.72 means "the market thinks there's a 72% chance."
    """
    token_id: str       # Which token this price belongs to
    timestamp: datetime # When this price was recorded
    price: float        # Price between 0.0 and 1.0


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------
@dataclass
class Signal:
    """
    What a strategy "says to do" at a given moment.

    The backtest engine reads this and decides whether to actually execute the trade.
    """
    action: str      # "BUY", "SELL", or "HOLD"
    token_id: str    # Which token to buy or sell
    outcome: str     # "YES" or "NO" — which side of the market
    price: float     # The current price when this signal was generated
    reason: str      # Human-readable explanation, e.g. "Momentum up for 5 bars"
    confidence: float  # 0.0–1.0 — how confident the strategy is (affects position sizing)


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """
    A holding we currently own in our portfolio.

    When we BUY, a Position is created.  When we SELL it all, it is removed.
    """
    token_id: str       # Which token we hold
    outcome: str        # "YES" or "NO"
    market_slug: str    # Human-readable market name
    shares: float       # How many shares (tokens) we own
    avg_cost: float     # Average price we paid per share
    opened_at: datetime # When we first bought into this position


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    """
    A record of one completed buy or sell.

    Every trade is logged here permanently so we can calculate performance metrics.
    """
    trade_id: str        # Unique ID (generated automatically)
    token_id: str        # Which token was traded
    market_slug: str     # Human-readable market name
    action: str          # "BUY" or "SELL"
    outcome: str         # "YES" or "NO"
    shares: float        # How many shares changed hands
    price: float         # Price per share at execution
    fee: float           # Transaction fee (small percentage, simulated)
    total_cost: float    # Total cash spent (BUY) or received (SELL) including fee
    timestamp: datetime  # When the trade happened
    pnl: float           # Profit or loss on this trade (0.0 for BUY trades)
