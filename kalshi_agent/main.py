"""
kalshi_agent/main.py — Command-line entry point for the Kalshi agent.

Modes:
  backtest  — Simulate a strategy on cached Kalshi price data (default)
  paper     — Run strategy live on real Kalshi prices with simulated money

Usage examples:
  python main.py --strategy momentum
  python main.py --strategy mean_reversion --markets 10 --cash 500
  python main.py --strategy random_baseline --no-fetch

  python main.py --strategy momentum --mode paper --duration 60
  python main.py --strategy momentum --mode paper --duration 60 --dashboard
  python main.py --strategy momentum --mode paper --duration 60 --name ktest

Run  python main.py --help  for all options.
"""

import argparse
import re
import sys
import os
import time

# Make sure Python can find kalshi_agent/ modules (config, data_fetcher, etc.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Make sure Python can find shared/ (portfolio, strategies, etc.)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from config import (
    DEFAULT_MARKETS_TO_FETCH,
    DEFAULT_STARTING_CASH,
    PAPER_DEFAULT_DURATION_MINUTES,
)
from shared.dashboard import start_in_thread as start_dashboard
from data_fetcher import fetch_markets, fetch_price_history
from data_storage import (
    save_markets, load_markets, markets_cache_exists,
    save_price_history, load_price_history, price_cache_exists,
)
from shared.portfolio import Portfolio
from shared.risk_manager import RiskManager
from shared.backtest_engine import BacktestEngine
from paper_trader import PaperTrader
from shared import metrics as metrics_module

# Import all available strategies (shared, platform-agnostic)
from shared.strategies.momentum import MomentumStrategy
from shared.strategies.mean_reversion import MeanReversionStrategy
from shared.strategies.random_baseline import RandomBaselineStrategy
from shared.strategies.rsi import RSIStrategy


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------
STRATEGY_MAP = {
    "momentum":        MomentumStrategy,
    "mean_reversion":  MeanReversionStrategy,
    "random_baseline": RandomBaselineStrategy,
    "rsi":             RSIStrategy,
}


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Kalshi Trading Agent\n"
            "  backtest mode: replay cached Kalshi prices, measure performance\n"
            "  paper mode:    run live on real Kalshi prices, simulate trades (no real money)"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "--strategy",
        type=str,
        required=True,
        choices=list(STRATEGY_MAP.keys()),
        help=(
            "Which strategy to run:\n"
            "  momentum        — Buy on uptrends, sell on downtrends\n"
            "  mean_reversion  — Buy when price is abnormally low (Z-score)\n"
            "  rsi             — Buy when RSI < 30 (oversold), sell when RSI > 70 (overbought)\n"
            "  random_baseline — Random trades (performance floor benchmark)"
        ),
    )

    parser.add_argument(
        "--mode",
        type=str,
        default="backtest",
        choices=["backtest", "paper"],
        help="Execution mode (default: backtest)",
    )

    parser.add_argument(
        "--markets",
        type=int,
        default=DEFAULT_MARKETS_TO_FETCH,
        help=f"How many markets to watch (default: {DEFAULT_MARKETS_TO_FETCH})",
    )

    parser.add_argument(
        "--cash",
        type=float,
        default=DEFAULT_STARTING_CASH,
        help=f"Starting virtual cash in USD (default: {DEFAULT_STARTING_CASH})",
    )

    parser.add_argument(
        "--duration",
        type=int,
        default=PAPER_DEFAULT_DURATION_MINUTES,
        help=f"[paper mode only] Session duration in minutes (default: {PAPER_DEFAULT_DURATION_MINUTES})",
    )

    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="[backtest only] Skip API fetch, use cached data only",
    )

    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="[paper mode only] Start a live web dashboard at http://localhost:5000",
    )

    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help=(
            "[paper mode only] Instance name for this run.\n"
            "Defaults to <strategy>_<HHMM> if not provided."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    return clean[:40] or "default"


# ---------------------------------------------------------------------------
# Data loading logic (backtest mode)
# ---------------------------------------------------------------------------

def load_data_for_backtest(num_markets: int, no_fetch: bool):
    """Fetch or load Kalshi markets and price histories for backtesting."""
    if no_fetch and markets_cache_exists():
        print("Loading markets from cache (--no-fetch)...")
        markets = load_markets()[:num_markets]
    elif no_fetch and not markets_cache_exists():
        print("ERROR: --no-fetch was set but no market cache exists.")
        print("Run without --no-fetch first to download data.")
        sys.exit(1)
    else:
        markets = fetch_markets(limit=num_markets, active_only=False)
        if not markets:
            print("ERROR: Could not fetch any markets. Check your internet connection.")
            sys.exit(1)
        save_markets(markets)

    if not markets:
        print("ERROR: No markets available.")
        sys.exit(1)

    print(f"\nLoaded {len(markets)} Kalshi markets.")

    price_data = {}

    for market in markets:
        ticker = market.yes_token_id

        if no_fetch and price_cache_exists(ticker):
            bars = load_price_history(ticker)
            print(f"  Cache hit: {market.slug[:40]} ({len(bars)} bars)")
        elif no_fetch:
            print(f"  Cache MISS (skipping): {market.slug[:40]}")
            continue
        else:
            if price_cache_exists(ticker):
                bars = load_price_history(ticker)
                print(f"  Cache hit: {market.slug[:40]} ({len(bars)} bars)")
            else:
                import re as _re
                event_ticker = _re.sub(r"-\d+$", "", ticker) or ticker
                bars = fetch_price_history(ticker, event_ticker)
                if bars:
                    save_price_history(ticker, bars)
                else:
                    print(f"  No price data for: {market.slug[:40]} — skipping")
                    continue

        if bars:
            price_data[ticker] = bars

    return markets, price_data


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    parser = build_arg_parser()
    args   = parser.parse_args()

    mode_label = "Paper Trading" if args.mode == "paper" else "Backtesting"

    print()
    print("=" * 55)
    print(f"  Kalshi Agent — {mode_label}")
    print(f"  Strategy : {args.strategy}")
    print(f"  Markets  : {args.markets}")
    print(f"  Cash     : ${args.cash:.2f}")
    if args.mode == "paper":
        print(f"  Duration : {args.duration} minutes")
    print("=" * 55)

    strategy_class = STRATEGY_MAP[args.strategy]
    strategy       = strategy_class()
    strategy.setup(params={})

    portfolio    = Portfolio(starting_cash=args.cash)
    risk_manager = RiskManager()

    # ----------------------------------------------------------------
    # PAPER TRADING MODE
    # ----------------------------------------------------------------
    if args.mode == "paper":
        instance_name = (
            _sanitize_name(args.name)
            if args.name
            else f"{args.strategy}_{time.strftime('%H%M')}"
        )

        if args.dashboard:
            try:
                start_dashboard(host="127.0.0.1", port=5000)
            except OSError:
                print("Dashboard already running at http://localhost:5000")

        trader = PaperTrader(
            strategy         = strategy,
            portfolio        = portfolio,
            risk_manager     = risk_manager,
            num_markets      = args.markets,
            duration_minutes = args.duration,
            instance_name    = instance_name,
        )
        results = trader.run()

        if results:
            metrics_module.print_results(results, strategy.name + " [Paper/Kalshi]")
        else:
            print("Paper trading session produced no results.")

        print("Done.")
        return

    # ----------------------------------------------------------------
    # BACKTEST MODE
    # ----------------------------------------------------------------
    markets, price_data = load_data_for_backtest(args.markets, args.no_fetch)

    if not price_data:
        print("ERROR: No price data available. Cannot run backtest.")
        sys.exit(1)

    markets_with_data = [m for m in markets if m.yes_token_id in price_data]
    print(f"\nMarkets with price data: {len(markets_with_data)}")

    if not markets_with_data:
        print("ERROR: No markets have price data.")
        sys.exit(1)

    engine  = BacktestEngine(strategy, portfolio, risk_manager)
    results = engine.run(markets_with_data, price_data)

    if results:
        metrics_module.print_results(results, strategy.name + " [Kalshi]")
    else:
        print("Backtest produced no results.")

    print("Data files saved to: data/")
    print("Done.")


if __name__ == "__main__":
    main()
