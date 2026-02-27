"""
kalshi_agent/data_storage.py — Save and load data to/from CSV files.

Identical logic to polymarket_agent/data_storage.py — pure CSV I/O,
no API knowledge. The ticker plays the role of token_id throughout.

File layout:
  data/markets.csv             — list of all fetched markets
  data/prices_{ticker}.csv     — price history for one market
"""

import os
import csv
from datetime import datetime
from typing import List, Optional

from config import DATA_DIR
from shared.models import Market, PriceBar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _price_file_path(token_id: str) -> str:
    """Return the CSV file path for a given ticker's price history."""
    safe_id = token_id.replace("/", "_").replace("\\", "_")[:40]
    return os.path.join(DATA_DIR, f"prices_{safe_id}.csv")


# ---------------------------------------------------------------------------
# Markets: save and load
# ---------------------------------------------------------------------------

MARKETS_FILE = os.path.join(DATA_DIR, "markets.csv")

MARKET_FIELDS = [
    "condition_id", "question", "slug",
    "yes_token_id", "no_token_id",
    "end_date", "is_resolved", "outcome", "platform",
]


def save_markets(markets: List[Market]) -> None:
    """Write a list of Market objects to markets.csv."""
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
                "platform":     m.platform,
            })

    print(f"Saved {len(markets)} markets to {MARKETS_FILE}")


def load_markets() -> List[Market]:
    """Read Market objects from markets.csv."""
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
                platform     = row.get("platform", "kalshi"),
            )
            markets.append(market)

    print(f"Loaded {len(markets)} markets from cache.")
    return markets


def markets_cache_exists() -> bool:
    return os.path.exists(MARKETS_FILE)


# ---------------------------------------------------------------------------
# Price history: save and load
# ---------------------------------------------------------------------------

PRICE_FIELDS = ["token_id", "timestamp", "price"]


def save_price_history(token_id: str, bars: List[PriceBar]) -> None:
    """Write a list of PriceBar objects to a CSV file."""
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
    """Read PriceBar objects from the CSV cache."""
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
    return os.path.exists(_price_file_path(token_id))
