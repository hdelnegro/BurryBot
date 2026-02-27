# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Directory Structure

```
BurryBot/
├── shared/                  # Platform-agnostic core library + dashboard
│   ├── models.py            # Dataclasses: Market, PriceBar, Signal, Position, Trade
│   ├── strategy_base.py     # Abstract base class all strategies inherit
│   ├── strategies/          # momentum, mean_reversion, rsi, random_baseline
│   ├── portfolio.py         # Cash/position tracking; execute buys/sells
│   ├── risk_manager.py      # Position size and exposure limits
│   ├── metrics.py           # Sharpe, drawdown, win rate, etc.
│   ├── backtest_engine.py   # Bar-by-bar simulation loop
│   ├── dashboard.py         # Flask dashboard; discovers all *_agent/data/ dirs
│   ├── requirements.txt     # flask only
│   └── venv/                # Minimal venv for running dashboard standalone
│
├── polymarket_agent/        # Polymarket platform-specific code
│   ├── venv/                # Full venv (requests, pandas, numpy, flask, etc.)
│   ├── data/                # CSV cache + state_*.json files
│   ├── config.py            # Polymarket API URLs + constants
│   ├── data_fetcher.py      # Gamma API (market list) + CLOB API (prices)
│   ├── data_storage.py      # CSV cache read/write
│   ├── paper_trader.py      # Live polling loop; writes state_<name>.json
│   ├── main.py              # Entry point (backtest + paper modes)
│   └── status.py            # CLI snapshot; no venv needed
│
└── kalshi_agent/            # Kalshi platform-specific code
    ├── venv/                # Venv (requests, pandas, numpy, python-dotenv)
    ├── data/                # CSV cache + state_*.json files
    ├── config.py            # Kalshi API URLs + constants
    ├── data_fetcher.py      # Kalshi REST API (markets, candlesticks, prices)
    ├── data_storage.py      # CSV cache read/write
    ├── paper_trader.py      # Live polling loop; writes state_<name>.json
    └── main.py              # Entry point (backtest + paper modes)
```

## Running the Polymarket Agent

```bash
cd polymarket_agent
source venv/bin/activate

# Backtest mode (default) — simulate on historical data
python main.py --strategy momentum
python main.py --strategy mean_reversion --markets 10 --cash 500
python main.py --strategy random_baseline --no-fetch   # use cached data only

# Paper trading mode — real live prices, simulated money
python main.py --strategy momentum --mode paper --duration 60
python main.py --strategy mean_reversion --mode paper --markets 10 --duration 120

# Paper trading with a custom instance name (defaults to <strategy>_<HHMM>)
python main.py --strategy momentum --mode paper --duration 60 --name run1
python main.py --strategy rsi --mode paper --duration 120 --name run2

# Paper trading with live web dashboard (http://localhost:5000)
python main.py --strategy momentum --mode paper --duration 60 --dashboard

# Check status of a running paper trading session (no venv needed)
python status.py
```

## Running the Kalshi Agent

```bash
cd kalshi_agent
# First time: create venv and install deps
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Backtest mode
python main.py --strategy momentum
python main.py --strategy mean_reversion --markets 10 --cash 500
python main.py --strategy random_baseline --no-fetch

# Paper trading mode
python main.py --strategy momentum --mode paper --duration 60
python main.py --strategy momentum --mode paper --duration 60 --name ktest
python main.py --strategy momentum --mode paper --duration 60 --dashboard
```

## Running the Dashboard

```bash
# Standalone (discovers all *_agent/data/ state files automatically)
cd BurryBot
source shared/venv/bin/activate
python shared/dashboard.py
# → http://localhost:5000 shows cards for ALL running/recent instances
#   with platform badges (polymarket / kalshi)

# Or via --dashboard flag from any agent's main.py (see above)
```

Available strategies: `momentum`, `mean_reversion`, `rsi`, `random_baseline`

## Architecture Overview

BurryBot is a multi-platform prediction-market trading system. Platform-agnostic logic lives in `shared/`; each agent directory contains only platform-specific API code.

**Import convention**: Each agent adds two paths to `sys.path`:
1. Its own directory (for `config`, `data_fetcher`, `data_storage`)
2. The BurryBot root (for `shared.*` imports)

**Backtest mode**: Fetches/caches historical price data as CSVs in `data/`, then replays bar-by-bar through `shared/backtest_engine.py`.

**Paper trading mode**: Polls live prices every 5 minutes, runs the strategy in real-time, executes simulated trades. After every tick writes `data/state_<name>.json` for the dashboard. Multiple instances (across platforms) can run simultaneously.

### Data Flow

```
Platform APIs → data_fetcher.py → data_storage.py (CSV cache in data/)
                                        ↓
main.py → BacktestEngine / PaperTrader → strategy.generate_signal()
                                        ↓
                            shared/risk_manager.check_signal()
                                        ↓
                            shared/portfolio.execute_buy/sell()
                                        ↓
                            shared/metrics.compute_all_metrics()
                                        ↓
                data/state_<name>.json  ←→  shared/dashboard.py
                  (platform: "polymarket"|"kalshi")
```

### Key Files

| File | Role |
|------|------|
| `shared/models.py` | Dataclasses: `Market` (+ `platform` field), `PriceBar`, `Signal`, `Position`, `Trade` |
| `shared/strategy_base.py` | Abstract base class all strategies must inherit |
| `shared/strategies/` | `momentum.py`, `mean_reversion.py`, `rsi.py`, `random_baseline.py` |
| `shared/backtest_engine.py` | Bar-by-bar simulation loop; prevents lookahead bias |
| `shared/portfolio.py` | Tracks cash, positions, trade log; executes buys/sells |
| `shared/risk_manager.py` | Gates every signal; enforces position size and exposure limits |
| `shared/metrics.py` | Computes final performance stats (total return, Sharpe, win rate, etc.) |
| `shared/dashboard.py` | Flask web dashboard; globs all `*_agent/data/state_*.json`; platform badges |
| `*/config.py` | Platform-specific constants — API URLs, thresholds, defaults |
| `*/data_fetcher.py` | Platform API calls (market list, price history, latest price) |
| `*/data_storage.py` | Saves/loads markets and price history as CSV files |
| `*/paper_trader.py` | Live polling loop; writes `data/state_<name>.json` with `platform` field |
| `*/main.py` | Platform entry point; sets up sys.path for both agent dir and shared/ |
| `polymarket_agent/status.py` | CLI snapshot; reads most recent `data/state_*.json`; no venv needed |

### Dynamic Market Refresh (paper trading)

The paper trader refreshes its market watchlist every `MARKET_REFRESH_INTERVAL_TICKS` ticks (default: 12 × 5 min = 60 min). On each refresh:
- Markets no longer active (or past their `end_date`) are removed and any open positions force-closed
- Newly appearing markets are added and their history loaded
- `_known_token_ids` / `_expired_token_ids` sets prevent re-downloading or re-adding markets

### Dashboard / State File

Each agent's `paper_trader._write_state()` writes `data/state_<name>.json` atomically after every tick. The state includes `"platform": "polymarket"` or `"platform": "kalshi"`.

`shared/dashboard.py` serves two views:
- **Overview** (`/`) — polls `/api/instances` every 2 seconds; shows all `state_*.json` files across **all** agent directories as clickable cards with platform badges and sparklines
- **Detail** (`/instance/<name>`) — polls `/api/state/<name>` every second; full single-instance view with equity curve, signals, positions, and trades

### Kalshi API Notes

- **Base URL**: `https://api.elections.kalshi.com/trade-api/v2`
- **No auth required** for paper trading (all endpoints used are public read-only)
- **Price scale**: Kalshi's `last_price` is in cents (0–100); `data_fetcher.py` divides by 100 to get 0.0–1.0
- **Market key**: Kalshi `ticker` maps to `yes_token_id` throughout the system
- **History**: `/series/{event_ticker}/markets/{ticker}/candlesticks?period=60` → 1-hour bars
- **Live price**: `GET /markets/{ticker}` → `last_price` field

### Adding a New Strategy

1. Create `shared/strategies/my_strategy.py` inheriting from `shared.strategy_base.StrategyBase`
2. Implement `setup(params)`, `generate_signal(...)`, and optionally `on_trade_executed(trade)`
3. Register it in each agent's `main.py` `STRATEGY_MAP` dict

### Adding a New Platform

1. Create `newplatform_agent/` with `config.py`, `data_fetcher.py`, `data_storage.py`, `paper_trader.py`, `main.py`
2. Set `platform = "newplatform"` in `paper_trader._write_state()`
3. Dashboard auto-discovers `newplatform_agent/data/state_*.json` with no changes needed

### Dependencies

```bash
# Polymarket agent (full stack)
pip install -r polymarket_agent/requirements.txt

# Kalshi agent
pip install -r kalshi_agent/requirements.txt   # requests, pandas, numpy, python-dotenv

# Shared dashboard (Flask only)
pip install -r shared/requirements.txt
# Or: pip install -r polymarket_agent/requirements.txt (already includes flask)
```

### Phase Flags

Each agent's `config.py` has phase flags:
- `PAPER_TRADING_ENABLED = True` (Phase 2 — current)
- `LIVE_TRADING_ENABLED = False` (Phase 3 — not yet implemented; Kalshi requires RSA-PSS key signing)
