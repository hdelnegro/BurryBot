"""
data_fetcher.py — Fetches market data from Polymarket's public APIs.

No login or API key is needed for historical/read-only data.

Two data sources:
  1. Gamma API  → list of markets (questions, IDs, outcomes)
  2. CLOB API   → price history for a specific token (time series)
"""

import time
import requests
from datetime import datetime
from typing import List, Optional

from config import (
    GAMMA_API_URL,
    CLOB_API_URL,
    REQUEST_TIMEOUT_SECONDS,
    REQUEST_MAX_RETRIES,
    REQUEST_RETRY_DELAY,
    PRICE_HISTORY_INTERVAL,
    PRICE_HISTORY_FIDELITY,
    GAMMA_SORT_FIELD,
    GAMMA_SORT_ASCENDING,
)
from shared.models import Market, PriceBar


# ---------------------------------------------------------------------------
# Helper: HTTP GET with automatic retry
# ---------------------------------------------------------------------------

def _get_with_retry(url: str, params: dict = None) -> Optional[dict]:
    """
    Make a GET request and retry up to REQUEST_MAX_RETRIES times on failure.

    Returns the parsed JSON response, or None if all attempts fail.
    """
    for attempt in range(1, REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()  # raises an error for 4xx/5xx status codes
            return response.json()

        except requests.exceptions.RequestException as error:
            print(f"  [Attempt {attempt}/{REQUEST_MAX_RETRIES}] Request failed: {error}")
            if attempt < REQUEST_MAX_RETRIES:
                print(f"  Retrying in {REQUEST_RETRY_DELAY}s...")
                time.sleep(REQUEST_RETRY_DELAY)

    print(f"  ERROR: All {REQUEST_MAX_RETRIES} attempts failed for {url}")
    return None


# ---------------------------------------------------------------------------
# Gamma API: fetch market list
# ---------------------------------------------------------------------------

def fetch_markets(limit: int = 5, active_only: bool = False) -> List[Market]:
    """
    Fetch a list of prediction markets from the Gamma API.

    We sort by 24-hour volume so we get the most-traded (most liquid) markets.
    These are also the ones most likely to have CLOB price history available.

    Args:
        limit:       How many markets to retrieve.
        active_only: If True, only fetch open markets (for paper/live trading).
                     If False, fetch a mix including closed markets (for backtesting).

    Returns:
        A list of Market objects.
    """
    params = {
        "limit":      limit,
        "order":      GAMMA_SORT_FIELD,
        "ascending":  str(GAMMA_SORT_ASCENDING).lower(),
    }
    if active_only:
        params["closed"] = "false"

    print(f"Fetching {limit} markets from Gamma API...")
    data = _get_with_retry(GAMMA_API_URL, params=params)

    if data is None:
        print("  WARNING: Could not fetch markets. Returning empty list.")
        return []

    markets = []
    for item in data:
        try:
            import json as _json

            # clobTokenIds is a JSON string: '["id1", "id2"]'
            # The order matches the outcomes list: first=YES, second=NO
            raw_token_ids = item.get("clobTokenIds", "[]")
            token_ids = _json.loads(raw_token_ids) if isinstance(raw_token_ids, str) else raw_token_ids

            # outcomes is also a JSON string: '["Yes", "No"]'
            raw_outcomes = item.get("outcomes", '["Yes", "No"]')
            outcome_labels = _json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes

            if len(token_ids) < 2 or len(outcome_labels) < 2:
                continue  # Need at least YES and NO tokens

            # Map outcome labels to token IDs
            yes_token_id = ""
            no_token_id  = ""
            for i, label in enumerate(outcome_labels):
                if label.upper() == "YES" and i < len(token_ids):
                    yes_token_id = token_ids[i]
                elif label.upper() == "NO" and i < len(token_ids):
                    no_token_id = token_ids[i]

            # Fallback: first token = YES, second = NO if labels are non-standard
            if not yes_token_id and len(token_ids) >= 1:
                yes_token_id = token_ids[0]
            if not no_token_id and len(token_ids) >= 2:
                no_token_id = token_ids[1]

            # Skip markets with missing token IDs
            if not yes_token_id or not no_token_id:
                continue

            market = Market(
                condition_id  = item.get("conditionId", ""),
                question      = item.get("question", "Unknown question"),
                slug          = item.get("slug", ""),
                yes_token_id  = yes_token_id,
                no_token_id   = no_token_id,
                end_date      = item.get("endDate"),
                is_resolved   = item.get("closed", False),
                outcome       = None,  # Not available in listing endpoint
            )
            markets.append(market)

        except Exception as e:
            # Skip malformed market entries but keep going
            print(f"  WARNING: Skipping malformed market entry: {e}")
            continue

    print(f"  Successfully parsed {len(markets)} markets.")
    return markets


# ---------------------------------------------------------------------------
# CLOB API: fetch price history
# ---------------------------------------------------------------------------

def fetch_price_history(token_id: str) -> List[PriceBar]:
    """
    Fetch the full price history for one token from the CLOB API.

    Prices represent probabilities (0.0 to 1.0).
    Each bar covers PRICE_HISTORY_FIDELITY minutes (default: 720 min = 12 hours).

    Args:
        token_id: The YES or NO token ID to fetch history for.

    Returns:
        A list of PriceBar objects sorted oldest → newest.
    """
    params = {
        "market":    token_id,
        "interval":  PRICE_HISTORY_INTERVAL,
        "fidelity":  PRICE_HISTORY_FIDELITY,
    }

    print(f"  Fetching price history for token {token_id[:12]}...")
    data = _get_with_retry(CLOB_API_URL, params=params)

    if data is None:
        print(f"  WARNING: No price history returned for token {token_id[:12]}")
        return []

    # The API returns a dict with a "history" key containing a list of {t, p} pairs.
    # "t" = Unix timestamp (seconds), "p" = price
    raw_bars = data.get("history", [])

    if not raw_bars:
        print(f"  WARNING: Empty price history for token {token_id[:12]}")
        return []

    bars = []
    for entry in raw_bars:
        try:
            bar = PriceBar(
                token_id  = token_id,
                timestamp = datetime.utcfromtimestamp(entry["t"]),
                price     = float(entry["p"]),
            )
            bars.append(bar)
        except (KeyError, ValueError, TypeError) as e:
            # Skip malformed entries silently
            continue

    # Sort chronologically (oldest first)
    bars.sort(key=lambda b: b.timestamp)

    print(f"  Got {len(bars)} price bars.")
    return bars


# ---------------------------------------------------------------------------
# 5-minute BTC up/down markets: fetch current market by slug
# ---------------------------------------------------------------------------

def fetch_current_5min_market() -> Optional[Market]:
    """
    Compute the slug for the currently-open 5-minute BTC up/down market
    from the system clock, then fetch its details from the Gamma API.

    Slug format: btc-updown-5m-{interval_start_unix_ts}
    where interval_start = floor(time.time() / 300) * 300

    Returns a Market object (Up token = yes_token_id, Down token = no_token_id),
    or None if the market cannot be found or parsed.
    """
    import json as _json
    from config import BTC_UPDOWN_5M_PREFIX, FIVE_MIN_INTERVAL_SECONDS

    interval_start = int(time.time()) // FIVE_MIN_INTERVAL_SECONDS * FIVE_MIN_INTERVAL_SECONDS
    slug = f"{BTC_UPDOWN_5M_PREFIX}-{interval_start}"

    data = _get_with_retry(GAMMA_API_URL, params={"slug": slug})
    if not data or not isinstance(data, list) or not data:
        print(f"  WARNING: No 5-min market found for slug {slug}")
        return None

    item = data[0]
    try:
        raw_token_ids = item.get("clobTokenIds", "[]")
        token_ids = _json.loads(raw_token_ids) if isinstance(raw_token_ids, str) else raw_token_ids
        if len(token_ids) < 2:
            print(f"  WARNING: 5-min market {slug} has fewer than 2 token IDs")
            return None

        return Market(
            condition_id = item.get("conditionId", ""),
            question     = item.get("question", "Unknown"),
            slug         = item.get("slug", slug),
            yes_token_id = token_ids[0],   # "Up" token
            no_token_id  = token_ids[1],   # "Down" token
            end_date     = item.get("endDate"),
            is_resolved  = item.get("closed", False),
            outcome      = None,
        )
    except Exception as e:
        print(f"  WARNING: Could not parse 5-min market {slug}: {e}")
        return None


# ---------------------------------------------------------------------------
# Stub for Phase 2: order book depth (not used in backtesting)
# ---------------------------------------------------------------------------

def fetch_order_book(token_id: str) -> dict:
    """
    STUB — will be implemented in Phase 2 (paper trading).

    In live trading, the order book shows all open buy/sell offers
    and helps estimate how much slippage a large order would cause.
    """
    raise NotImplementedError(
        "fetch_order_book() is not implemented yet. "
        "This will be added in Phase 2 (paper trading)."
    )
