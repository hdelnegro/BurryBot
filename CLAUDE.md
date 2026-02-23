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
```

Available strategies: `momentum`, `mean_reversion`, `random_baseline`

## Architecture Overview

The system is a Polymarket prediction-market trading simulator with two execution modes:

**Backtest mode**: Fetches historical price data from the Polymarket CLOB API, caches it as CSVs in `data/`, then replays bar-by-bar through `BacktestEngine`.

**Paper trading mode**: Polls live Polymarket prices every 5 minutes (configurable), runs the strategy in real-time, executes simulated trades — no real money involved.

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
```

### Key Files

| File | Role |
|------|------|
| `config.py` | All constants — API URLs, thresholds, defaults. Change behavior here, not in logic files. |
| `models.py` | Dataclasses: `Market`, `PriceBar`, `Signal`, `Position`, `Trade` |
| `strategy_base.py` | Abstract base class all strategies must inherit |
| `strategies/` | `momentum.py`, `mean_reversion.py`, `random_baseline.py` |
| `backtest_engine.py` | Bar-by-bar simulation loop; prevents lookahead bias by passing `history[:i]` to strategy |
| `paper_trader.py` | Live polling loop; fetches real prices, simulates trades |
| `portfolio.py` | Tracks cash, positions, trade log; executes buys/sells |
| `risk_manager.py` | Gates every signal; enforces position size and exposure limits |
| `metrics.py` | Computes final performance stats (total return, Sharpe, win rate, etc.) |
| `data_fetcher.py` | Calls Gamma API (market list) and CLOB API (price history) |
| `data_storage.py` | Saves/loads markets and price history as CSV files |

### Adding a New Strategy

1. Create `strategies/my_strategy.py` inheriting from `StrategyBase`
2. Implement `setup(params)`, `generate_signal(...)`, and optionally `on_trade_executed(trade)`
3. Register it in `main.py`'s `STRATEGY_MAP` dict

### Dependencies

Install with: `pip install -r polymarket_agent/requirements.txt`

Core dependencies: `requests`, `pandas`, `numpy`, `py-clob-client`, `python-dotenv`

### Phase Flags

`config.py` has phase flags:
- `PAPER_TRADING_ENABLED = True` (Phase 2 — current)
- `LIVE_TRADING_ENABLED = False` (Phase 3 — not yet implemented)
