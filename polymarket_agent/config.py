"""
config.py — All constants and settings for the backtesting agent.

Change values here to tweak how the agent behaves.
No code logic lives here — just numbers and strings.
"""

# ---------------------------------------------------------------------------
# API endpoints (public, no login required for historical data)
# ---------------------------------------------------------------------------

# Gamma API: returns a list of markets (questions, token IDs, etc.)
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

# CLOB API: returns price history for one token
CLOB_API_URL = "https://clob.polymarket.com/prices-history"

# How long to wait (seconds) before giving up on an API call
REQUEST_TIMEOUT_SECONDS = 30

# How many times to retry a failed API call before giving up
REQUEST_MAX_RETRIES = 3

# Pause between retries (seconds)
REQUEST_RETRY_DELAY = 2.0


# ---------------------------------------------------------------------------
# Data fetching defaults
# ---------------------------------------------------------------------------

# How many markets to fetch when the user doesn't specify
DEFAULT_MARKETS_TO_FETCH = 5

# Maximum markets allowed in one run (keeps things fast)
MAX_MARKETS_TO_FETCH = 50

# Price history interval for CLOB API.
# "max"  = fetch as much history as available
PRICE_HISTORY_INTERVAL = "max"

# Fidelity = time bucket size in minutes.
# 720 minutes = 12-hour bars  (good balance: enough data, not too slow)
PRICE_HISTORY_FIDELITY = 720

# Gamma API sort order for market listing.
# "volume24hr" = most-traded markets today (these have the most CLOB price history)
GAMMA_SORT_FIELD = "volume24hr"
GAMMA_SORT_ASCENDING = False


# ---------------------------------------------------------------------------
# Portfolio defaults
# ---------------------------------------------------------------------------

# Starting cash balance in USDC (dollar-equivalent)
DEFAULT_STARTING_CASH = 1000.0

# Maximum fraction of portfolio to put into a single trade (20% = 0.20)
MAX_POSITION_SIZE_FRACTION = 0.20

# Maximum fraction of portfolio exposed to open positions at once (80% = 0.80)
MAX_TOTAL_EXPOSURE_FRACTION = 0.80

# Simulated trading fee per trade (0.2% = 0.002).
# Polymarket charges roughly 0-2% depending on liquidity.
TRADE_FEE_RATE = 0.002


# ---------------------------------------------------------------------------
# Strategy defaults
# ---------------------------------------------------------------------------

# Momentum strategy: number of bars to look back when calculating trend
MOMENTUM_LOOKBACK = 5

# Mean-reversion strategy: how many standard deviations from the mean
# before we consider a price "too far away" and likely to snap back
MEAN_REVERSION_Z_THRESHOLD = 1.5

# Mean-reversion strategy: number of bars used to compute the rolling mean
MEAN_REVERSION_WINDOW = 20

# Minimum price to consider trading (avoids near-zero junk markets)
MIN_TRADEABLE_PRICE = 0.01

# Maximum price to consider trading on the BUY side (near-certain markets are boring)
MAX_TRADEABLE_PRICE = 0.99


# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

# Where cached CSV files are stored
DATA_DIR = "data"


# ---------------------------------------------------------------------------
# Paper trading settings (Phase 2)
# ---------------------------------------------------------------------------

# How many seconds to wait between each live price fetch
PAPER_POLL_INTERVAL_SECONDS = 300   # 300 seconds = 5 minutes

# Default session duration in minutes (used if --duration not specified)
PAPER_DEFAULT_DURATION_MINUTES = 60

# How many ticks between full market-list refreshes.
# Short-lived markets (BTC up/down hourly) expire and new ones open during a long
# session, so we periodically re-fetch the top-volume market list and add newcomers.
# 12 ticks × 5 min = refresh every 60 minutes.
MARKET_REFRESH_INTERVAL_TICKS = 12

# Hard cap on how many markets to watch simultaneously.
# Keeps each tick from taking too long to poll.
MAX_WATCHED_MARKETS = 50

# ---------------------------------------------------------------------------
# Phase flags
# ---------------------------------------------------------------------------
PAPER_TRADING_ENABLED = True    # Phase 2: simulate live trading without real money
LIVE_TRADING_ENABLED  = False   # Phase 3: real orders through py-clob-client
