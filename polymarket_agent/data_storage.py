"""
data_storage.py — Save and load data to/from CSV files.

Why cache data?
  - Fetching from the API takes 5-30 seconds per market.
  - Running the backtest many times (while tweaking a strategy) would
    be very slow if we re-downloaded everything each time.
  - CSVs let us reload in under a second.

File layout:
  data/markets.csv              — list of all fetched markets
  data/prices_{token_id}.csv    — price history for one token
"""

import os
import csv
from datetime import datetime
from typing import List, Optional

from config import DATA_DIR
from models import Market, PriceBar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    """Create the data/ directory if it doesn't already exist."""
    os.makedirs(DATA_DIR, exist_ok=True)


def _price_file_path(token_id: str) -> str:
    """Return the CSV file path for a given token's price history."""
    # Use first 16 chars of token_id as the filename to keep it readable
    safe_id = token_id.replace("0x", "")[:16]
    return os.path.join(DATA_DIR, f"prices_{safe_id}.csv")


# ---------------------------------------------------------------------------
# Markets: save and load
# ---------------------------------------------------------------------------

MARKETS_FILE = os.path.join(DATA_DIR, "markets.csv")

MARKET_FIELDS = [
    "condition_id", "question", "slug",
    "yes_token_id", "no_token_id",
    "end_date", "is_resolved", "outcome",
]


def save_markets(markets: List[Market]) -> None:
    """
    Write a list of Market objects to markets.csv.

    Overwrites the file completely each time (fresh snapshot).
    """
    _ensure_data_dir()

    with open(MARKETS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MARKET_FIELDS)
        writer.writeheader()
        for m in markets:
            writer.writerow({
                "condition_id": m.condition_id,
                "question":     m.question,
                "slug":         m.slug,
                "yes_token_id": m.yes_token_id,
                "no_token_id":  m.no_token_id,
                "end_date":     m.end_date or "",
                "is_resolved":  m.is_resolved,
                "outcome":      m.outcome or "",
            })

    print(f"Saved {len(markets)} markets to {MARKETS_FILE}")


def load_markets() -> List[Market]:
    """
    Read Market objects from markets.csv.

    Returns an empty list if the file doesn't exist yet.
    """
    if not os.path.exists(MARKETS_FILE):
        return []

    markets = []
    with open(MARKETS_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            market = Market(
                condition_id = row["condition_id"],
                question     = row["question"],
                slug         = row["slug"],
                yes_token_id = row["yes_token_id"],
                no_token_id  = row["no_token_id"],
                end_date     = row["end_date"] or None,
                is_resolved  = row["is_resolved"].lower() == "true",
                outcome      = row["outcome"] or None,
            )
            markets.append(market)

    print(f"Loaded {len(markets)} markets from cache.")
    return markets


def markets_cache_exists() -> bool:
    """Return True if a markets cache file already exists."""
    return os.path.exists(MARKETS_FILE)


# ---------------------------------------------------------------------------
# Price history: save and load
# ---------------------------------------------------------------------------

PRICE_FIELDS = ["token_id", "timestamp", "price"]


def save_price_history(token_id: str, bars: List[PriceBar]) -> None:
    """
    Write a list of PriceBar objects to a CSV file for the given token.
    """
    _ensure_data_dir()
    path = _price_file_path(token_id)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PRICE_FIELDS)
        writer.writeheader()
        for bar in bars:
            writer.writerow({
                "token_id":  bar.token_id,
                "timestamp": bar.timestamp.isoformat(),
                "price":     bar.price,
            })

    print(f"  Saved {len(bars)} price bars to {path}")


def load_price_history(token_id: str) -> List[PriceBar]:
    """
    Read PriceBar objects from the CSV cache for the given token.

    Returns an empty list if no cache exists yet.
    """
    path = _price_file_path(token_id)

    if not os.path.exists(path):
        return []

    bars = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bar = PriceBar(
                token_id  = row["token_id"],
                timestamp = datetime.fromisoformat(row["timestamp"]),
                price     = float(row["price"]),
            )
            bars.append(bar)

    return bars


def price_cache_exists(token_id: str) -> bool:
    """Return True if a price cache file exists for this token."""
    return os.path.exists(_price_file_path(token_id))
