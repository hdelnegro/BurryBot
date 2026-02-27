"""
shared/models.py — Platform-agnostic data definitions for BurryBot agents.

These dataclasses are shared across all trading platform agents
(polymarket_agent/, kalshi_agent/, etc.).
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
    Represents one prediction market.

    Field mapping per platform:
      Polymarket: condition_id, yes_token_id/no_token_id (hex), slug, end_date
      Kalshi:     condition_id=ticker, yes_token_id=ticker, no_token_id=ticker+"_no",
                  slug=ticker, end_date=close_time
    """
    condition_id: str        # Unique market identifier
    question: str            # The plain-English question
    slug: str                # URL-friendly / short name
    yes_token_id: str        # Primary token ID (YES side)
    no_token_id: str         # Secondary token ID (NO side)
    end_date: Optional[str]  # When the market closes (ISO date string or None)
    is_resolved: bool        # True if the market already has a winner
    outcome: Optional[str]   # "Yes" / "No" / None (None if unresolved)
    platform: str = "polymarket"  # Which platform this market belongs to


# ---------------------------------------------------------------------------
# PriceBar
# ---------------------------------------------------------------------------
@dataclass
class PriceBar:
    """
    One price observation for a token at a specific moment in time.

    Prices are probabilities: 0.0 = 0% chance, 1.0 = 100% chance.
    Both Polymarket and Kalshi use this 0.0–1.0 scale.
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
    """
    action: str      # "BUY", "SELL", or "HOLD"
    token_id: str    # Which token to buy or sell
    outcome: str     # "YES" or "NO" — which side of the market
    price: float     # The current price when this signal was generated
    reason: str      # Human-readable explanation
    confidence: float  # 0.0–1.0 — how confident the strategy is


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """
    A holding we currently own in our portfolio.
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
