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

# Maximum fraction of portfolio to put into a single trade (5% = 0.05)
MAX_POSITION_SIZE_FRACTION = 0.05

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

# RSI strategy settings
RSI_PERIOD      = 14    # Number of bars used to compute RSI (standard = 14)
RSI_OVERSOLD    = 30.0  # RSI below this → price fell too fast → BUY signal
RSI_OVERBOUGHT  = 70.0  # RSI above this → price rose too fast → SELL signal

# Minimum price to consider trading (avoids near-zero and near-certain markets)
# 0.05 = below 5% probability we don't buy; these rarely revert, they go to 0.
MIN_TRADEABLE_PRICE = 0.05

# Maximum price to consider trading on the BUY side
# 0.95 = above 95% probability we don't buy; same logic in reverse.
MAX_TRADEABLE_PRICE = 0.95

# Stop-loss: close any position that has fallen this far below its entry cost.
# 0.30 = exit if down 30% from avg_cost (e.g. bought at 0.10, exit at 0.07).
STOP_LOSS_PCT = 0.30

# Time-based exit: close a non-profitable position after this many ticks.
# 12 ticks × 5 min = 60 min. If reversion hasn't happened in an hour, it won't.
MAX_HOLD_TICKS = 12


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
# 5-minute BTC up/down market settings
# ---------------------------------------------------------------------------

# How many seconds between ticks in 5-min mode (10× faster than standard)
FIVE_MIN_POLL_INTERVAL_SECONDS = 30

# Every N ticks, check if the current 5-min market has rolled over to a new one
# 10 ticks × 30s = check every 5 minutes
FIVE_MIN_MARKET_REFRESH_TICKS = 10

# Length of each 5-minute window in seconds
FIVE_MIN_INTERVAL_SECONDS = 300

# Force-exit any open Up position this many seconds before each market closes
# (avoids binary resolution risk as the token price converges to 0 or 1)
FIVE_MIN_EXIT_BUFFER_SECONDS = 30

# Strategy lookback params tuned for 5-min cross-market history
FIVE_MIN_MOMENTUM_LOOKBACK     = 3   # 3 successive markets to confirm momentum
FIVE_MIN_MEAN_REVERSION_WINDOW = 8   # 8-market rolling window
FIVE_MIN_RSI_PERIOD            = 7   # 7-period RSI for fast markets

# Gamma API slug prefix for 5-min BTC up/down markets
BTC_UPDOWN_5M_PREFIX = "btc-updown-5m"


# ---------------------------------------------------------------------------
# Phase flags
# ---------------------------------------------------------------------------
PAPER_TRADING_ENABLED = True   # Phase 2: simulate live trading without real money
LIVE_TRADING_ENABLED  = True   # Phase 3: real orders through py-clob-client


# ---------------------------------------------------------------------------
# Live trading settings (Phase 3)
# ---------------------------------------------------------------------------

# Polygon chain ID (required by py-clob-client)
POLY_CHAIN_ID = 137

# Polymarket CLOB API host
CLOB_HOST = "https://clob.polymarket.com"

# Order type for live trades: GTC = Good-Til-Cancelled limit orders
LIVE_ORDER_TYPE = "GTC"

# Maximum acceptable price slippage on limit orders (2% = 0.02)
# Buy orders: price + slippage; Sell orders: price - slippage
LIVE_SLIPPAGE_TOLERANCE = 0.02

# Minimum order size in USDC — orders smaller than this are skipped
LIVE_MIN_ORDER_SIZE_USDC = 1.0
