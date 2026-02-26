# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Agent

All commands are run from within `polymarket_agent/` using the local venv:

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
# Overview page shows all running instances; click a card for the full detail view
python main.py --strategy momentum --mode paper --duration 60 --dashboard

# Run the dashboard standalone (one or more traders running in separate terminals)
python dashboard.py

# Check status of a running paper trading session (no venv needed)
# Note: reads state_default.json or the most recently modified state_*.json
python status.py
```

Available strategies: `momentum`, `mean_reversion`, `rsi`, `random_baseline`

## Architecture Overview

The system is a Polymarket prediction-market trading simulator with two execution modes:

**Backtest mode**: Fetches historical price data from the Polymarket CLOB API, caches it as CSVs in `data/`, then replays bar-by-bar through `BacktestEngine`.

**Paper trading mode**: Polls live Polymarket prices every 5 minutes (configurable), runs the strategy in real-time, executes simulated trades. After every tick it writes `data/state_<name>.json` (where `<name>` is the instance name) for the dashboard and status tool. Multiple instances can run simultaneously, each with its own state file.

### Data Flow

```
Polymarket APIs → data_fetcher.py → data_storage.py (CSV cache in data/)
                                          ↓
main.py → BacktestEngine / PaperTrader → strategy.generate_signal()
                                          ↓
                              risk_manager.check_signal()
                                          ↓
                              portfolio.execute_buy/sell()
                                          ↓
                              metrics.compute_all_metrics()
                                          ↓
                              data/state_<name>.json  ←→  dashboard.py / status.py
```

### Key Files

| File | Role |
|------|------|
| `config.py` | All constants — API URLs, thresholds, defaults. Change behavior here, not in logic files. |
| `models.py` | Dataclasses: `Market`, `PriceBar`, `Signal`, `Position`, `Trade` |
| `strategy_base.py` | Abstract base class all strategies must inherit |
| `strategies/` | `momentum.py`, `mean_reversion.py`, `rsi.py`, `random_baseline.py` |
| `backtest_engine.py` | Bar-by-bar simulation loop; prevents lookahead bias by passing `history[:i]` to strategy |
| `paper_trader.py` | Live polling loop; dynamic market refresh; writes `data/state_<name>.json` after each tick |
| `dashboard.py` | Flask web dashboard; overview at `/`, per-instance detail at `/instance/<name>`; served at `localhost:5000` |
| `status.py` | CLI snapshot of running session; reads most recent `data/state_*.json`; no venv needed |
| `portfolio.py` | Tracks cash, positions, trade log; executes buys/sells |
| `risk_manager.py` | Gates every signal; enforces position size and exposure limits |
| `metrics.py` | Computes final performance stats (total return, Sharpe, win rate, etc.) |
| `data_fetcher.py` | Calls Gamma API (market list) and CLOB API (price history) |
| `data_storage.py` | Saves/loads markets and price history as CSV files |

### Dynamic Market Refresh (paper trading)

The paper trader refreshes its market watchlist every `MARKET_REFRESH_INTERVAL_TICKS` ticks (default: 12 × 5 min = 60 min). This handles short-lived markets like hourly BTC up/down contracts. On each refresh:
- Markets no longer in the active list (or past their `end_date`) are removed and any open positions force-closed
- Newly appearing markets are added and their history loaded
- `_known_token_ids` / `_expired_token_ids` sets prevent re-downloading or re-adding markets

### Dashboard / State File

`paper_trader._write_state()` writes `data/state_<name>.json` atomically (via `.tmp` + `os.replace`) after every tick, where `<name>` is the instance name (defaults to `<strategy>_<HHMM>` if `--name` is not passed).

`dashboard.py` serves two views:
- **Overview** (`/`) — polls `/api/instances` every 2 seconds; shows all `state_*.json` files as clickable cards with mini sparklines
- **Detail** (`/instance/<name>`) — polls `/api/state/<name>` every second; full single-instance view with equity curve, signals, positions, and trades

`status.py` reads the most recent `state_*.json` directly and treats state older than 6 minutes as stale.

### Adding a New Strategy

1. Create `strategies/my_strategy.py` inheriting from `StrategyBase`
2. Implement `setup(params)`, `generate_signal(...)`, and optionally `on_trade_executed(trade)`
3. Register it in `main.py`'s `STRATEGY_MAP` dict

### Dependencies

Install with: `pip install -r polymarket_agent/requirements.txt`

Core dependencies: `requests`, `pandas`, `numpy`, `flask`, `py-clob-client`, `python-dotenv`

### Phase Flags

`config.py` has phase flags:
- `PAPER_TRADING_ENABLED = True` (Phase 2 — current)
- `LIVE_TRADING_ENABLED = False` (Phase 3 — not yet implemented)
