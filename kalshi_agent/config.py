"""
kalshi_agent/config.py — All constants and settings for the Kalshi agent.

Change values here to tweak how the agent behaves.
No code logic lives here — just numbers and strings.
"""

# ---------------------------------------------------------------------------
# Kalshi API endpoints (public, no auth required for market data)
# ---------------------------------------------------------------------------

# Trade API v2 base URL
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# How long to wait (seconds) before giving up on an API call
REQUEST_TIMEOUT_SECONDS = 30

# How many times to retry a failed API call before giving up
REQUEST_MAX_RETRIES = 3

# Pause between retries (seconds)
REQUEST_RETRY_DELAY = 2.0


# ---------------------------------------------------------------------------
# Data fetching defaults
# ---------------------------------------------------------------------------

# How many markets to fetch in one request (Kalshi allows up to 200)
KALSHI_MARKETS_FETCH_LIMIT = 200

# Candlestick bar size in minutes (60 = 1-hour bars)
KALSHI_CANDLESTICK_PERIOD = 60

# How many days of price history to fetch on initial load
KALSHI_HISTORY_DAYS = 30

# How many markets to show by default when user doesn't specify
DEFAULT_MARKETS_TO_FETCH = 5

# Maximum markets allowed in one run
MAX_MARKETS_TO_FETCH = 50


# ---------------------------------------------------------------------------
# Portfolio defaults
# ---------------------------------------------------------------------------

# Starting cash balance in USD
DEFAULT_STARTING_CASH = 1000.0

# Maximum fraction of portfolio to put into a single trade (20%)
MAX_POSITION_SIZE_FRACTION = 0.20

# Maximum fraction of portfolio exposed to open positions at once (80%)
MAX_TOTAL_EXPOSURE_FRACTION = 0.80

# Simulated trading fee per trade (Kalshi charges ~1-2% taker fee)
TRADE_FEE_RATE = 0.01


# ---------------------------------------------------------------------------
# Strategy defaults (same values as polymarket_agent for apples-to-apples comparison)
# ---------------------------------------------------------------------------

MOMENTUM_LOOKBACK         = 5
MEAN_REVERSION_Z_THRESHOLD = 1.5
MEAN_REVERSION_WINDOW     = 20
RSI_PERIOD                = 14
RSI_OVERSOLD              = 30.0
RSI_OVERBOUGHT            = 70.0
MIN_TRADEABLE_PRICE       = 0.01
MAX_TRADEABLE_PRICE       = 0.99


# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

DATA_DIR = "data"


# ---------------------------------------------------------------------------
# Paper trading settings
# ---------------------------------------------------------------------------

# How many seconds to wait between each live price fetch
PAPER_POLL_INTERVAL_SECONDS = 300   # 5 minutes

# Default session duration in minutes
PAPER_DEFAULT_DURATION_MINUTES = 60

# How many ticks between full market-list refreshes
MARKET_REFRESH_INTERVAL_TICKS = 12  # 12 × 5 min = 60 min

# Hard cap on how many markets to watch simultaneously
MAX_WATCHED_MARKETS = 50


# ---------------------------------------------------------------------------
# Phase flags
# ---------------------------------------------------------------------------
PAPER_TRADING_ENABLED = True    # Phase 2: simulate live trading without real money
LIVE_TRADING_ENABLED  = False   # Phase 3: real orders (requires RSA-PSS auth)
