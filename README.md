# BurryBot

A trading strategy research framework for [Polymarket](https://polymarket.com), a prediction market platform where people trade on the probability of real-world events. BurryBot lets you write, backtest, paper-trade, and live-trade strategies against Polymarket's live and historical price data — progressing from simulation to real orders at your own pace.

---

## What is Polymarket?

Polymarket is a prediction market. Instead of trading stocks or currencies, you trade on the outcome of events: "Will X happen by date Y?" Each market has two tokens — YES and NO — whose prices represent the market's collective belief in the probability of that outcome. A YES token priced at `0.72` means the market thinks there's a 72% chance the event happens.

When a market resolves, YES tokens pay out $1.00 if the event happened, $0.00 if it didn't. This means price is always between 0 and 1, and profitable trading requires being right about probabilities more often than the crowd.

---

## What BurryBot Does

BurryBot is a strategy research platform with three execution modes:

**Backtest mode** downloads historical price data from Polymarket's public API, caches it locally, and simulates a strategy's trading decisions bar-by-bar through that history. At the end it calculates performance metrics — return, Sharpe ratio, drawdown, win rate — so you can evaluate how a strategy would have performed.

**Paper trading mode** runs a strategy on live, real-time prices fetched directly from Polymarket every few minutes, but executes all trades in a simulated portfolio. No real money is involved. This lets you observe strategy behaviour on live markets before committing capital.

**Live trading mode** is identical to paper trading in structure, but wires real orders through Polymarket's CLOB API via `py-clob-client`. The portfolio is seeded from the actual on-chain USDC balance, and every approved signal places a GTC limit order on the exchange. A confirmation prompt and geo-block check guard against accidental activation.

The project is structured so that adding a new strategy requires writing only one file and registering it in one place. Everything else — data fetching, risk management, portfolio accounting, and metrics — is handled by the framework.

---

## Getting Started

```bash
cd polymarket_agent
source venv/bin/activate

# Run a backtest with the momentum strategy
python main.py --strategy momentum

# Run a backtest on 10 markets with $500 starting cash
python main.py --strategy mean_reversion --markets 10 --cash 500

# Re-run a backtest without re-downloading data
python main.py --strategy random_baseline --no-fetch

# Run a live paper trading session for 60 minutes
python main.py --strategy momentum --mode paper --duration 60

# Run with a custom instance name (auto-generates <strategy>_<HHMM> if omitted)
python main.py --strategy momentum --mode paper --duration 60 --name run1

# Run multiple instances simultaneously (each writes its own state file)
python main.py --strategy momentum --mode paper --duration 60 --name run1
python main.py --strategy mean_reversion --mode paper --duration 60 --name run2

# Run paper trading with the live web dashboard
# Overview at http://localhost:5000 shows all instances; click a card for the detail view
python main.py --strategy momentum --mode paper --duration 60 --dashboard

# Run the dashboard standalone (one or more traders running in separate terminals)
python dashboard.py

# Check the status of a running paper trading session (in another terminal)
python status.py
```

### Live Trading (Phase 3)

Live trading places real orders on Polymarket using your actual USDC balance. Before running, set up your credentials:

```bash
# 1. Copy the credentials template
cp .env.example .env

# 2. Fill in .env:
#    POLY_PRIVATE_KEY    — proxy signing key from Polymarket account settings → Export Proxy Key
#    POLY_FUNDER_ADDRESS — your Polymarket wallet address

# 3. Run with --mode live (a confirmation prompt will appear before any orders are placed)
python main.py --strategy momentum --mode live --markets 3 --duration 60

# Run with the dashboard to monitor in real time
python main.py --strategy momentum --mode live --markets 3 --duration 60 --dashboard
```

The default wallet type is `magic` (Polymarket.com email account), which uses a gasless relayer — no POL token is needed for gas.

Available strategies: `momentum`, `mean_reversion`, `rsi`, `random_baseline`

All configuration (API URLs, position sizing limits, strategy parameters, live trading constants) lives in `config.py`.

For an explanation of the performance metrics (Sharpe ratio, max drawdown, win rate) and strategy concepts (momentum, mean reversion, RSI), see [docs/trading_concepts.md](docs/trading_concepts.md).

---

## Technologies

**Python 3.12** — The project uses standard Python with no async frameworks or heavy abstractions. The codebase is intentionally straightforward: data flows through plain function calls and class methods.

**pandas / numpy** — Used inside the strategy layer for efficient time-series manipulation. Each strategy receives its price history as a pandas DataFrame with a datetime index, which makes rolling window calculations (means, standard deviations, percentage changes) concise and correct.

**Flask** — Powers the live web dashboard. Runs in a daemon thread alongside the paper trader so it terminates automatically when the main process exits. The dashboard is a single self-contained HTML page served from a Python string — no template files or static assets needed.

**requests** — HTTP calls to Polymarket's two public APIs. No authentication is needed for read-only historical data. The fetcher wraps all calls in a retry loop to handle transient network failures gracefully.

**py-clob-client** — The official Polymarket Python client, used in live trading mode (Phase 3) to authenticate and submit real GTC limit orders via Polymarket's Central Limit Order Book (CLOB). Its dependency chain (`eip712_structs`, etc.) is imported lazily — only when `--mode live` is active — so backtest and paper trading modes have no additional requirements.

**python-dotenv** — Used in live trading mode to load wallet credentials (`POLY_PRIVATE_KEY`, `POLY_FUNDER_ADDRESS`) from a `.env` file, keeping secrets out of the codebase.

**CSV for caching** — Rather than a database, historical price data is cached as plain CSV files. This keeps the project dependency-free for data storage, makes the cache human-readable and easy to inspect, and is fast enough for the volumes involved (thousands of price bars per market).

---

## Architecture

### The Separation of Concerns

The codebase is divided into components that each own one responsibility and do not reach into each other's internals:

```
Polymarket APIs
      │
      ▼
 data_fetcher.py      ←— fetches from Gamma API (market list) and CLOB API (prices)
      │
      ▼
 data_storage.py      ←— persists to / reads from CSV cache in data/
      │
      ▼
 main.py              ←— CLI entry point; assembles components and hands off to engine
      │
      ├──► BacktestEngine   ←— bar-by-bar simulation loop (backtest mode)
      │
      ├──► PaperTrader      ←— live polling loop (paper trading mode)
      │         │
      │         ├──► strategy.generate_signal()    ←— strategy decides BUY / SELL / HOLD
      │         │           │
      │         │           ▼
      │         │    risk_manager.check_signal()   ←— gates the trade; sizes the position
      │         │           │
      │         │           ▼
      │         │    portfolio.execute_buy/sell()  ←— updates cash, positions, trade log (simulated)
      │         │           │
      │         │           ▼
      │         │    metrics.compute_all_metrics() ←— evaluates final performance
      │         │
      │         └──► data/state_<name>.json  ←— written after every tick
      │
      └──► LiveTrader        ←— inherits PaperTrader; overrides trade execution only (live mode)
                │
                ├──► wallet.py / ClobClient  ←— authenticates via proxy key; geo-block check
                │
                ├──► strategy + risk_manager  ←— unchanged (reused from PaperTrader)
                │
                ├──► _execute_live_buy/sell() ←— places real GTC limit orders via CLOB API
                │         fills confirmed → portfolio.execute_buy/sell() with real price
                │
                └──► data/state_<name>.json  ←— same format; live sessions visible on dashboard

 dashboard.py     ←— Flask server; overview of all instances + per-instance detail
 status.py        ←— CLI snapshot reader (python status.py)
```

### Why two separate engines?

Backtest and paper trading have fundamentally different loops. The backtest engine iterates through a fixed, pre-loaded dataset — it knows all the data upfront and can move through time as fast as the CPU allows. The paper trader has no pre-loaded future; it must wait for real time to pass, polling an external API on a schedule, handling network failures, and responding to the user pressing Ctrl+C. Merging these two loops into one would make both harder to understand and harder to test independently.

### Why a strategy base class?

`StrategyBase` defines a contract: any strategy must implement `setup()`, `generate_signal()`, and optionally `on_trade_executed()`. The engines are written entirely against this interface and know nothing about how any individual strategy works. This means:

- Adding a new strategy never requires touching the engine, portfolio, or risk manager.
- The `random_baseline` strategy serves as a performance floor: if a strategy can't beat random trading, it has no edge.
- Strategies are cleanly isolated — their internal state doesn't bleed into the rest of the system.

### Why does the engine pass `history[:i]` to the strategy?

Lookahead bias is the most common way backtests produce falsely optimistic results. If a strategy can "see" a future price bar, it will appear to perform perfectly — but that performance is meaningless because you can't know tomorrow's prices today. The engine deliberately passes only the bars before index `i` to `generate_signal()`, so the strategy is always making decisions as it would have had to in real time.

### How position sizing works

The risk manager sits between the strategy and the portfolio and enforces two hard limits regardless of what the strategy signals:

1. **Max position size**: a single position cannot exceed 20% of total portfolio value. This prevents concentrating too much capital in one market.
2. **Max total exposure**: no more than 80% of the portfolio can be in open positions at once. At least 20% is always kept in cash.

Within those limits, the actual trade size is also scaled by the strategy's `confidence` value (0.0–1.0). A strategy that is very certain about a signal can deploy more capital than one that is uncertain. SELLs are always approved — reducing exposure is never blocked.

The 20% / 80% split is configured in `config.py` and easy to adjust.

---

## Components in Detail

### `models.py` — Data types

Defines the five dataclasses that flow through the system:

- **`Market`** — a Polymarket prediction market question, with its YES and NO token IDs. Token IDs are the addresses the CLOB API uses to identify each tradeable side.
- **`PriceBar`** — a single price observation for a token at a point in time. Prices are probabilities: 0.0–1.0.
- **`Signal`** — the output of a strategy: action (`BUY`/`SELL`/`HOLD`), which token, the price at signal time, a human-readable reason, and a confidence score.
- **`Position`** — an open holding in the portfolio, tracking average cost and share count.
- **`Trade`** — an immutable record of an executed buy or sell, including fees and realised PnL.

### `config.py` — All configuration in one place

All constants live here: API URLs, timeout and retry settings, default starting cash, position size limits, strategy parameters (momentum lookback, mean reversion Z-score threshold), the minimum/maximum tradeable price range, and live trading settings (`POLY_CHAIN_ID`, `CLOB_HOST`, `LIVE_SLIPPAGE_TOLERANCE`, `LIVE_MIN_ORDER_SIZE_USDC`). Nothing else in the codebase hardcodes numbers. The idea is that changing the behaviour of the system means changing one file, not hunting through logic code.

### `data_fetcher.py` — API client

Calls two Polymarket public endpoints:

- **Gamma API** (`gamma-api.polymarket.com/markets`) returns a list of markets sorted by 24-hour volume. Sorting by volume is important: high-volume markets are the ones most likely to have extensive CLOB price history. Markets sorted by some other criterion might have no historical data at all.
- **CLOB API** (`clob.polymarket.com/prices-history`) returns a time series of `{t, p}` pairs (Unix timestamp, price) for a specific token. The default fidelity is 12-hour bars (`fidelity=720`), which provides enough historical data points for statistical analysis without being so granular that downloads take a long time.

All HTTP calls go through a retry wrapper that handles transient network failures with configurable delay between attempts.

### `data_storage.py` — CSV cache

Saves and loads market lists and price histories as CSV files under `data/`. On a backtest run, the system checks for a cache hit before calling the API. If a cache exists, it loads in under a second. If not, it fetches from the API (which takes 5–30 seconds per market) and then saves to CSV for subsequent runs.

The `--no-fetch` flag forces the engine to use only what's already cached, which is useful when iterating on a strategy and you don't want to wait for network calls.

### `backtest_engine.py` — Simulation loop

Loads all price data into pandas DataFrames upfront, then iterates bar-by-bar. At each bar it processes every market in order: asks the strategy for a signal, asks the risk manager whether to execute, then tells the portfolio to act. After processing all markets for a bar, it records the current total portfolio value to the equity curve.

At the end of the simulation, all remaining open positions are force-closed at the last known price (liquidation), which ensures the final metrics reflect the true state of the portfolio rather than ignoring unrealised holdings.

### `paper_trader.py` — Live polling loop

On startup, fetches a list of active markets (only open markets — backtesting can use resolved markets but paper trading should only trade things still being decided). Then loads historical price bars to give the strategy enough context to generate signals from the first tick.

The main loop polls every 5 minutes (configurable as `PAPER_POLL_INTERVAL_SECONDS`). At each tick it fetches the latest price bar for each market, appends it to the in-memory price history, runs the strategy, checks the risk manager, and simulates any approved trades. After every tick it writes the full session state to `data/state_<name>.json` (where `<name>` is the instance name, defaulting to `<strategy>_<HHMM>`) for the dashboard and status tool to read. Multiple instances can run simultaneously, each writing its own file.

**Dynamic market refresh**: Every `MARKET_REFRESH_INTERVAL_TICKS` ticks (default: every 12 ticks = 60 minutes), the trader re-fetches the top-volume active market list. This is necessary because Polymarket has short-lived markets — for example, "Bitcoin Up or Down - 2PM ET" opens and resolves every hour. Without periodic refreshes, a long-running session would end up watching only expired markets. The refresh detects newly listed markets (adds them and loads their history), and detects expired or resolved markets (removes them from the watchlist and force-closes any open positions). A set of `_known_token_ids` prevents re-downloading history for markets already seen, and `_expired_token_ids` prevents re-adding markets that have already been removed.

The loop respects a Ctrl+C interrupt — it catches `SIGINT` and sets a flag that causes the loop to exit cleanly after the current tick, rather than crashing mid-trade.

`PaperTrader` is also the base class for `LiveTrader` — the two share all market discovery, strategy, risk, and state-writing logic. Only the trade execution methods differ.

### `wallet.py` — Wallet adapter

Abstracts the credential and authentication layer for live trading behind a `WalletAdapter` interface. The only concrete implementation is `MagicLinkWallet` (Polymarket.com email account, `signature_type=1`), which derives L2 API credentials automatically from the proxy signing key on each startup and uses Polymarket's gasless relayer (no POL token needed for gas). `EOAWallet` and `GnosisSafeWallet` are stubs for future use.

`wallet_from_env()` is a factory that reads `POLY_WALLET_TYPE`, `POLY_PRIVATE_KEY`, and `POLY_FUNDER_ADDRESS` from `.env` and returns the correct adapter. All `py_clob_client` imports are lazy (inside `build_clob_client()`) so they don't cascade to non-live modes.

### `live_trader.py` — Live order execution

`LiveTrader` inherits `PaperTrader` and overrides two things:

**`run()`** — checks the geo-block endpoint (`polymarket.com/api/geoblock`) and the exchange contract allowance before starting the loop, and prints a live-mode banner.

**`_run_tick()`** — mirrors `PaperTrader._run_tick()` exactly, except approved signals go to `_execute_live_buy()` / `_execute_live_sell()` instead of directly to `portfolio.execute_buy/sell()`. The live methods:

1. Round the signal price to the market's tick size (fetched and cached per token)
2. Call `ClobClient.create_and_post_order(OrderArgs(...))` with a GTC limit order
3. On a confirmed fill (`status == "matched"` or `"live"`): call `portfolio.execute_buy/sell()` with the actual fill price, and store the CLOB `order_id` in `trade.trade_id` for traceability
4. On an unmatched order: log and skip — no portfolio update

After each tick's orders, `_resync_cash()` re-fetches the actual USDC balance from the chain to prevent the local cash balance from drifting out of sync.

At startup, `_sync_portfolio_from_chain()` seeds the portfolio's cash from the real on-chain USDC balance and lists any pre-existing open positions (though it does not load them into the local portfolio automatically).

### `strategy_base.py` — Strategy interface

An abstract base class with three methods:

- **`setup(params)`** — called once before the simulation starts. Used to store parameters and initialise internal state.
- **`generate_signal(...)`** — called at every time step for every market. Receives price history up to (but not including) the current bar, the current price, and the current timestamp. Returns a `Signal`.
- **`on_trade_executed(trade)`** — optional hook called after a trade executes. Useful for strategies that maintain internal state based on fills.

### `strategies/momentum.py`

Looks at the last N price bars and counts how many moved up vs. down. If every bar in the lookback window moved in the same direction, it signals a trend. A clean uptrend triggers BUY; a clean downtrend triggers SELL; anything mixed is HOLD. The confidence value is 1.0 when all bars align.

Momentum is best in markets where news arrives gradually and the crowd updates slowly. It performs poorly at sharp reversals.

### `strategies/mean_reversion.py`

Computes a rolling mean and standard deviation over a window of recent bars. Calculates the Z-score of the current price (how many standard deviations away from the mean it is). If the Z-score exceeds a threshold in either direction, it signals that the market has overreacted and the price will snap back.

Mean reversion is the philosophical opposite of momentum. It works when markets overreact to short-term news and prices temporarily deviate from their fair value. The Z-score threshold of ±1.5 (default) means a signal fires when the price is in roughly the bottom or top 7% of its recent distribution — unusual enough to suggest a temporary mispricing.

### `strategies/rsi.py`

Computes the Relative Strength Index (RSI) over a rolling window. RSI measures the speed and magnitude of recent price changes on a 0–100 scale. When RSI drops below 30, the market is considered oversold and a BUY is signalled; when it rises above 70, the market is considered overbought and a SELL is signalled. This is a classic momentum-reversal hybrid — it captures overextension in either direction rather than betting on continued trends.

### `strategies/random_baseline.py`

Makes random BUY/SELL/HOLD decisions. Serves only as a benchmark: any real strategy should outperform random trading over a sufficient backtest period. If it doesn't, the strategy has no edge.

### `portfolio.py` — Virtual brokerage account

Tracks cash balance, open positions, and a trade log. BUY orders deduct cash and create or add to a position (using weighted average cost for add-ons). SELL orders close the entire position, calculate realised PnL against the average cost basis, and return net proceeds (after fees) to cash. Every executed trade is appended to the trade log for later metric calculation.

Sells are always all-or-nothing (full position close). Partial fills add significant complexity — order splitting, average price tracking per fill — for marginal benefit in this research context.

### `risk_manager.py` — Trade gatekeeper

The only component that can block a trade. Checks position size limits, total exposure limits, and available cash. Returns a three-tuple: `(allowed, trade_size_in_usdc, reason_string)`. The reason string is always printed, so the user can see why a trade was blocked or approved.

### `dashboard.py` — Live web dashboard

A Flask application that serves a browser dashboard at `http://localhost:5000`. It is started via the `--dashboard` flag and runs in a background daemon thread alongside the paper trader — no separate process is needed, and it shuts down automatically when the trader exits. It can also run standalone (`python dashboard.py`) if one or more traders are running in separate terminals.

The dashboard has two views:

**Overview page** (`/`) — polls `/api/instances` every 2 seconds and renders a card grid showing all active and recently completed instances. Each card displays the instance name, strategy, portfolio value, return %, a mini sparkline of the equity curve, and trade/win-rate stats. Live instances show a pulsing green dot; dead ones turn red and dimmed. Clicking any card navigates to the detail view.

**Detail page** (`/instance/<name>`) — polls `/api/state/<name>` every second and shows the full single-instance dashboard:
- **KPI cards**: portfolio value, total return %, Sharpe ratio, max drawdown, win rate, time remaining with a progress bar
- **Equity curve**: a Chart.js line chart updating in real time
- **Market signals**: the latest signal for each watched market (BUY / SELL / HOLD with reason)
- **Open positions**: each holding with unrealised PnL
- **Recent trades**: last 20 trades with action, price, and PnL

A `← Overview` back button appears at the top of every detail page.

Each paper trader instance writes to its own `data/state_<name>.json` atomically (via a `.tmp` + `os.replace`) after every tick. The atomic write ensures the dashboard never reads a half-written file. HTTP caching headers (`Cache-Control: no-store`) are set on every response.

### `status.py` — Terminal status snapshot

A lightweight CLI tool that reads the most recently modified `data/state_*.json` and prints a colour-coded summary of the current paper trading session. Unlike the dashboard it has zero dependencies beyond the Python standard library — no venv activation needed.

It detects a stale state file (older than 6 minutes, which is one missed poll interval plus a grace period) and reports "No agent running" rather than displaying outdated numbers. The output includes strategy name, tick count, time remaining, portfolio value and return, Sharpe/drawdown/win-rate metrics, all open positions with unrealised PnL, the five most recent trades, and a count of BUY/SELL/HOLD signals from the last tick.

### `metrics.py` — Performance measurement

Computes the standard set of trading performance metrics after a backtest or paper trading session:

- **Total return %** — overall gain or loss as a percentage of starting capital.
- **Sharpe ratio** — return per unit of risk. Calculated as the mean of bar-to-bar equity returns divided by their standard deviation, annualised assuming 730 bars per year (2 per day at the default 12-hour fidelity). A Sharpe above 1.0 is generally considered good; above 2.0 is excellent.
- **Max drawdown %** — the largest peak-to-trough decline in portfolio value during the simulation. This measures the worst case an investor would have experienced while holding through the strategy.
- **Win rate %** — fraction of completed sell trades that were profitable.
- **Average PnL per trade** — average realised profit or loss per sell, in USDC.

---

## Development Roadmap

The codebase has explicit phase flags in `config.py`:

- **Phase 1 (complete)** — Backtesting on historical data
- **Phase 2 (complete)** — Paper trading on live prices with simulated money
- **Phase 3 (complete)** — Live trading via `py-clob-client` with real orders (`LIVE_TRADING_ENABLED = True`)

All three phases are now implemented. Potential next directions: partial-fill handling, FOK orders for immediate execution, EOA wallet support, and Kalshi live trading.

---

## Adding a Strategy

1. Create `polymarket_agent/strategies/my_strategy.py`
2. Inherit from `StrategyBase` and implement `setup()` and `generate_signal()`
3. Register it in `main.py`'s `STRATEGY_MAP`:
   ```python
   from strategies.my_strategy import MyStrategy
   STRATEGY_MAP = {
       ...
       "my_strategy": MyStrategy,
   }
   ```
4. Run it: `python main.py --strategy my_strategy`
