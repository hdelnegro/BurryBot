"""
main.py — Command-line entry point for the Polymarket agent.

Modes:
  backtest  — Simulate a strategy on historical price data (default)
  paper     — Run strategy live on real prices with simulated money

Usage examples:
  python main.py --strategy momentum
  python main.py --strategy mean_reversion --markets 10 --cash 500
  python main.py --strategy random_baseline --no-fetch

  python main.py --strategy momentum --mode paper --duration 60
  python main.py --strategy mean_reversion --mode paper --markets 10 --duration 120

Run  python main.py --help  for all options.
"""

import argparse
import sys
import os

# Make sure Python can find our modules (they're in the same directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    DEFAULT_MARKETS_TO_FETCH,
    DEFAULT_STARTING_CASH,
    PAPER_DEFAULT_DURATION_MINUTES,
)
from data_fetcher import fetch_markets, fetch_price_history
from data_storage import (
    save_markets, load_markets, markets_cache_exists,
    save_price_history, load_price_history, price_cache_exists,
)
from portfolio import Portfolio
from risk_manager import RiskManager
from backtest_engine import BacktestEngine
from paper_trader import PaperTrader
import metrics as metrics_module

# Import all available strategies
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.random_baseline import RandomBaselineStrategy


# ---------------------------------------------------------------------------
# Strategy registry — add new strategies here
# ---------------------------------------------------------------------------
STRATEGY_MAP = {
    "momentum":        MomentumStrategy,
    "mean_reversion":  MeanReversionStrategy,
    "random_baseline": RandomBaselineStrategy,
}


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Polymarket Trading Agent\n"
            "  backtest mode: replay historical prices, measure performance\n"
            "  paper mode:    run live on real prices, simulate trades (no real money)"
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
            "  random_baseline — Random trades (performance floor benchmark)"
        ),
    )

    parser.add_argument(
        "--mode",
        type=str,
        default="backtest",
        choices=["backtest", "paper"],
        help=(
            "Execution mode (default: backtest):\n"
            "  backtest — simulate strategy on historical data\n"
            "  paper    — run live with real prices, simulated money"
        ),
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
        help=f"Starting virtual cash in USDC (default: {DEFAULT_STARTING_CASH})",
    )

    parser.add_argument(
        "--duration",
        type=int,
        default=PAPER_DEFAULT_DURATION_MINUTES,
        help=(
            f"[paper mode only] Session duration in minutes "
            f"(default: {PAPER_DEFAULT_DURATION_MINUTES})"
        ),
    )

    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="[backtest only] Skip API fetch, use cached data only",
    )

    return parser


# ---------------------------------------------------------------------------
# Data loading logic (backtest mode)
# ---------------------------------------------------------------------------

def load_data_for_backtest(num_markets: int, no_fetch: bool):
    """
    Fetch or load markets and their price histories for backtesting.

    If --no-fetch is set, use cached CSVs only.
    Otherwise, fetch from API and cache results.

    Returns:
        (markets, price_data) where price_data is dict of token_id → [PriceBar]
    """
    # ---- Markets ----
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

    print(f"\nLoaded {len(markets)} markets.")

    # ---- Price histories ----
    price_data = {}

    for market in markets:
        token_id = market.yes_token_id

        if no_fetch and price_cache_exists(token_id):
            bars = load_price_history(token_id)
            print(f"  Cache hit: {market.slug[:40]} ({len(bars)} bars)")
        elif no_fetch:
            print(f"  Cache MISS (skipping): {market.slug[:40]}")
            continue
        else:
            if price_cache_exists(token_id):
                bars = load_price_history(token_id)
                print(f"  Cache hit: {market.slug[:40]} ({len(bars)} bars)")
            else:
                bars = fetch_price_history(token_id)
                if bars:
                    save_price_history(token_id, bars)
                else:
                    print(f"  No price data for: {market.slug[:40]} — skipping")
                    continue

        if bars:
            price_data[token_id] = bars

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
    print(f"  Polymarket Agent — {mode_label}")
    print(f"  Strategy : {args.strategy}")
    print(f"  Markets  : {args.markets}")
    print(f"  Cash     : ${args.cash:.2f}")
    if args.mode == "paper":
        print(f"  Duration : {args.duration} minutes")
    print("=" * 55)

    # Instantiate the chosen strategy
    strategy_class = STRATEGY_MAP[args.strategy]
    strategy       = strategy_class()
    strategy.setup(params={})

    # Instantiate portfolio and risk manager (shared by both modes)
    portfolio    = Portfolio(starting_cash=args.cash)
    risk_manager = RiskManager()

    # ----------------------------------------------------------------
    # PAPER TRADING MODE
    # ----------------------------------------------------------------
    if args.mode == "paper":
        trader = PaperTrader(
            strategy         = strategy,
            portfolio        = portfolio,
            risk_manager     = risk_manager,
            num_markets      = args.markets,
            duration_minutes = args.duration,
        )
        results = trader.run()

        if results:
            metrics_module.print_results(results, strategy.name + " [Paper]")
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
        metrics_module.print_results(results, strategy.name)
    else:
        print("Backtest produced no results.")

    print("Data files saved to: data/")
    print("Done.")


if __name__ == "__main__":
    main()
