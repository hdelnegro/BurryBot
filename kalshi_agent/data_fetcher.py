"""
kalshi_agent/data_fetcher.py — Fetches market data from the Kalshi Trade API v2.

No API key required for public market data (paper trading only reads prices).
Phase 3 (live trading) would require RSA-PSS key signing.

Three functions:
  fetch_markets(limit, active_only)   → sorted by 24h volume, cursor-paginated
  fetch_price_history(ticker, event_ticker) → hourly candlestick bars
  fetch_latest_price(ticker)          → single fresh price bar from /markets/{ticker}
"""

import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from config import (
    KALSHI_API_BASE,
    REQUEST_TIMEOUT_SECONDS,
    REQUEST_MAX_RETRIES,
    REQUEST_RETRY_DELAY,
    KALSHI_MARKETS_FETCH_LIMIT,
    KALSHI_CANDLESTICK_PERIOD,
    KALSHI_HISTORY_DAYS,
)
from shared.models import Market, PriceBar


# ---------------------------------------------------------------------------
# Helper: HTTP GET with retry
# ---------------------------------------------------------------------------

def _get_with_retry(url: str, params: dict = None) -> Optional[dict]:
    """
    Make a GET request and retry up to REQUEST_MAX_RETRIES times on failure.
    Returns parsed JSON or None.
    """
    for attempt in range(1, REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as error:
            print(f"  [Attempt {attempt}/{REQUEST_MAX_RETRIES}] Request failed: {error}")
            if attempt < REQUEST_MAX_RETRIES:
                print(f"  Retrying in {REQUEST_RETRY_DELAY}s...")
                time.sleep(REQUEST_RETRY_DELAY)

    print(f"  ERROR: All {REQUEST_MAX_RETRIES} attempts failed for {url}")
    return None


# ---------------------------------------------------------------------------
# Market list
# ---------------------------------------------------------------------------

def fetch_markets(limit: int = 5, active_only: bool = False) -> List[Market]:
    """
    Fetch Kalshi markets, sorted by 24h volume (most liquid first).

    Kalshi's GET /markets does not support server-side sorting, so we:
      1. Cursor-paginate up to KALSHI_MARKETS_FETCH_LIMIT markets
      2. Sort client-side by volume_24h (descending)
      3. Return top `limit` markets

    Args:
        limit:       How many markets to return.
        active_only: If True, fetch only open/active markets.

    Returns:
        List of Market objects (yes_token_id = ticker).
    """
    url = f"{KALSHI_API_BASE}/markets"
    params = {"limit": 200}
    if active_only:
        params["status"] = "open"

    print(f"Fetching Kalshi markets (up to {KALSHI_MARKETS_FETCH_LIMIT}, sorted by volume)...")

    all_raw = []
    cursor  = None

    while len(all_raw) < KALSHI_MARKETS_FETCH_LIMIT:
        if cursor:
            params["cursor"] = cursor

        data = _get_with_retry(url, params=params)
        if data is None:
            break

        batch   = data.get("markets", [])
        cursor  = data.get("cursor")

        all_raw.extend(batch)

        if not cursor or not batch:
            break  # No more pages

    if not all_raw:
        print("  WARNING: No markets returned from Kalshi API.")
        return []

    # Sort by volume_24h descending (field may be named volume or volume_24h)
    def _volume(m):
        for key in ("volume_24h", "volume"):
            v = m.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return 0.0

    all_raw.sort(key=_volume, reverse=True)

    markets = []
    for item in all_raw[:max(limit * 3, 50)]:  # parse extra, filter below
        try:
            ticker       = item.get("ticker", "")
            event_ticker = item.get("event_ticker", "")
            title        = item.get("title", item.get("question", "Unknown market"))
            close_time   = item.get("close_time") or item.get("expiration_time")
            status       = item.get("status", "")
            result       = item.get("result", "")

            if not ticker:
                continue

            is_resolved = status in ("finalized", "settled") or bool(result)
            outcome     = result if result else None

            market = Market(
                condition_id = ticker,
                question     = title,
                slug         = ticker,
                yes_token_id = ticker,        # Kalshi uses ticker as primary key
                no_token_id  = ticker + "_no",
                end_date     = close_time,
                is_resolved  = is_resolved,
                outcome      = outcome,
                platform     = "kalshi",
            )
            markets.append(market)

            if len(markets) >= limit:
                break

        except Exception as e:
            print(f"  WARNING: Skipping malformed market entry: {e}")
            continue

    print(f"  Successfully parsed {len(markets)} Kalshi markets.")
    return markets


# ---------------------------------------------------------------------------
# Price history (candlestick endpoint)
# ---------------------------------------------------------------------------

def fetch_price_history(ticker: str, event_ticker: str = "") -> List[PriceBar]:
    """
    Fetch hourly price history for a Kalshi market.

    Uses GET /series/{event_ticker}/markets/{ticker}/candlesticks
    with period=60 (1-hour bars), 30 days back.

    Kalshi's last_price field is already 0–100 cents; we divide by 100
    to get the 0.0–1.0 probability scale used throughout the system.

    Args:
        ticker:       The market ticker (e.g. "KXHIGHNY123").
        event_ticker: The parent series/event ticker. Guessed from ticker
                      if not provided (first segment before the last digit run).

    Returns:
        List of PriceBar objects sorted oldest → newest.
    """
    if not event_ticker:
        # Best-effort guess: Kalshi tickers often look like SERIES-YYYYMMDD
        # Strip trailing numeric suffix to get the event ticker
        import re
        event_ticker = re.sub(r"-\d+$", "", ticker) or ticker

    end_ts   = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - KALSHI_HISTORY_DAYS * 24 * 3600

    url = f"{KALSHI_API_BASE}/series/{event_ticker}/markets/{ticker}/candlesticks"
    params = {
        "period":   KALSHI_CANDLESTICK_PERIOD,
        "start_ts": start_ts,
        "end_ts":   end_ts,
    }

    print(f"  Fetching price history for Kalshi ticker {ticker}...")
    data = _get_with_retry(url, params=params)

    if data is None:
        print(f"  WARNING: No price history returned for {ticker}")
        return []

    # Response structure: {"candlesticks": [{"ts": ..., "price": {"close": ...}}, ...]}
    raw_candles = data.get("candlesticks", [])

    if not raw_candles:
        print(f"  WARNING: Empty candlestick history for {ticker}")
        return []

    bars = []
    for entry in raw_candles:
        try:
            ts     = entry.get("ts") or entry.get("end_period_ts")
            price_data = entry.get("price", {})

            # Use close price; fall back to yes_ask or yes_bid if close is absent
            raw_price = (
                price_data.get("close")
                or price_data.get("yes_ask")
                or price_data.get("yes_bid")
                or entry.get("yes_price")
            )

            if ts is None or raw_price is None:
                continue

            # Kalshi prices are in cents (0–100); convert to 0.0–1.0
            price = float(raw_price) / 100.0
            price = max(0.0, min(1.0, price))

            bar = PriceBar(
                token_id  = ticker,
                timestamp = datetime.utcfromtimestamp(int(ts)),
                price     = price,
            )
            bars.append(bar)
        except Exception:
            continue

    bars.sort(key=lambda b: b.timestamp)
    print(f"  Got {len(bars)} price bars for {ticker}.")
    return bars


# ---------------------------------------------------------------------------
# Latest price (single market fetch)
# ---------------------------------------------------------------------------

def fetch_latest_price(ticker: str) -> Optional[PriceBar]:
    """
    Fetch the current price for a single Kalshi market.

    Uses GET /markets/{ticker} → reads last_price field.
    Kalshi's last_price is in cents (0–100); we divide by 100 for 0.0–1.0.

    Returns:
        A PriceBar with the current timestamp, or None on failure.
    """
    url  = f"{KALSHI_API_BASE}/markets/{ticker}"
    data = _get_with_retry(url)

    if data is None:
        return None

    market_data = data.get("market", data)

    # Try multiple field names Kalshi may use
    raw_price = (
        market_data.get("last_price")
        or market_data.get("yes_ask")
        or market_data.get("yes_bid")
    )

    if raw_price is None:
        return None

    try:
        price = float(raw_price) / 100.0
        price = max(0.0, min(1.0, price))
    except (TypeError, ValueError):
        return None

    return PriceBar(
        token_id  = ticker,
        timestamp = datetime.utcnow(),
        price     = price,
    )
