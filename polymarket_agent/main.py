"""
main.py — Command-line entry point for the Polymarket agent.

Modes:
  backtest  — Simulate a strategy on historical price data (default)
  paper     — Run strategy live on real prices with simulated money

Usage examples:
  python main.py                                         # interactive setup
  python main.py --strategy momentum
  python main.py --strategy mean_reversion --markets 10 --cash 500
  python main.py --strategy random_baseline --no-fetch

  python main.py --strategy momentum --mode paper --duration 60
  python main.py --strategy mean_reversion --mode paper --markets 10 --duration 120

Run  python main.py --help  for all options.
Run  python main.py        (no arguments) for interactive setup.
"""

import argparse
import re
import sys
import os
import time

# Make sure Python can find polymarket_agent/ modules (config, data_fetcher, etc.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Make sure Python can find shared/ (portfolio, strategies, etc.)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

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
from shared.portfolio import Portfolio
from shared.risk_manager import RiskManager
from shared.backtest_engine import BacktestEngine
from paper_trader import PaperTrader, FiveMinPaperTrader
# LiveTrader and wallet_from_env are imported lazily inside the live-mode block
# to avoid importing py_clob_client (and its dependency chain) when not needed.
from shared import metrics as metrics_module

# Import all available strategies
from shared.strategies.momentum import MomentumStrategy
from shared.strategies.mean_reversion import MeanReversionStrategy
from shared.strategies.random_baseline import RandomBaselineStrategy
from shared.strategies.rsi import RSIStrategy


# ---------------------------------------------------------------------------
# Strategy registry — add new strategies here
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
            "  rsi             — Buy when RSI < 30 (oversold), sell when RSI > 70 (overbought)\n"
            "  random_baseline — Random trades (performance floor benchmark)"
        ),
    )

    parser.add_argument(
        "--mode",
        type=str,
        default="backtest",
        choices=["backtest", "paper", "live"],
        help=(
            "Execution mode (default: backtest):\n"
            "  backtest — simulate strategy on historical data\n"
            "  paper    — run live with real prices, simulated money\n"
            "  live     — run live with real prices, REAL money (Phase 3)"
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
            "[paper mode only] Instance name for this run (letters, digits, underscores, hyphens).\n"
            "Defaults to <strategy>_<HHMM> if not provided."
        ),
    )

    parser.add_argument(
        "--market-type",
        type=str,
        default="standard",
        choices=["standard", "5min"],
        dest="market_type",
        help=(
            "[paper mode only] Market type (default: standard):\n"
            "  standard — top-volume Polymarket markets, 5-min poll\n"
            "  5min     — BTC 5-minute up/down markets, 30-second poll"
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_name(name: str) -> str:
    """Keep only letters, digits, underscores, hyphens. Truncate to 40 chars."""
    clean = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    return clean[:40] or "default"


# ---------------------------------------------------------------------------
# Data loading logic (backtest mode)
# ---------------------------------------------------------------------------

def load_data_for_backtest(num_markets: int, no_fetch: bool):
    """
    Fetch or load markets and their price histories for backtesting.
    """
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
# Interactive setup
# ---------------------------------------------------------------------------

def interactive_setup() -> argparse.Namespace:
    """
    Prompt the user for all settings one by one.
    Called automatically when main.py is run with no arguments.
    """
    from datetime import datetime, timezone, timedelta

    BOLD  = "\033[1m"
    CYAN  = "\033[96m"
    DIM   = "\033[2m"
    RESET = "\033[0m"

    def pick(prompt, options):
        print(f"\n{BOLD}{prompt}{RESET}")
        for i, (label, desc, _) in enumerate(options, 1):
            line = f"  {i}. {CYAN}{label}{RESET}"
            if desc:
                line += f"  {DIM}— {desc}{RESET}"
            print(line)
        while True:
            raw = input(f"Select [1-{len(options)}]: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                _, _, value = options[int(raw) - 1]
                return value
            print(f"  Please enter a number from 1 to {len(options)}.")

    print()
    print(BOLD + CYAN + "=" * 55 + RESET)
    print(BOLD + CYAN + "  BurryBot — Interactive Setup" + RESET)
    print(BOLD + CYAN + "=" * 55 + RESET)
    print(DIM + "  (Run with --help to see all CLI flags instead)" + RESET)

    strategy = pick("Strategy:", [
        ("momentum",        "Buy on uptrends, sell on downtrends",             "momentum"),
        ("mean_reversion",  "Buy when price drops below Z-score threshold",    "mean_reversion"),
        ("rsi",             "Buy when RSI<30 (oversold), sell when RSI>70",    "rsi"),
        ("random_baseline", "Random trades — performance floor benchmark",     "random_baseline"),
    ])

    mode = pick("Mode:", [
        ("paper",    "Live prices, simulated trades — no real money",    "paper"),
        ("backtest", "Replay historical price data (fast, no network)",  "backtest"),
    ])

    markets = pick("Markets to watch:", [
        ("5",  "quick run, fewer signals",           5),
        ("10", "balanced",                           10),
        ("20", "good coverage",                      20),
        ("30", "broad coverage",                     30),
        ("50", "maximum",                            50),
    ])

    cash = pick("Starting virtual cash (USDC):", [
        ("$500",    "",              500.0),
        ("$1,000",  "default",       1000.0),
        ("$2,000",  "",              2000.0),
        ("$5,000",  "",              5000.0),
    ])

    duration  = PAPER_DEFAULT_DURATION_MINUTES
    dashboard = False
    no_fetch  = False

    if mode == "paper":
        print(f"\n{BOLD}Session end date/time:{RESET}")
        print(f"  Format : {CYAN}YYYYMMDDhhmm{RESET}  (ART — Buenos Aires time, UTC-3)")
        print(f"  Example: {DIM}202602241000{RESET}  = 24 Feb 2026 at 10:00am ART")
        while True:
            raw = input("  End time: ").strip()
            if len(raw) == 12 and raw.isdigit():
                try:
                    end_local = datetime.strptime(raw, "%Y%m%d%H%M")
                    end_utc   = end_local.replace(tzinfo=timezone(timedelta(hours=-3)))
                    now_utc   = datetime.now(timezone.utc)
                    duration  = int((end_utc - now_utc).total_seconds() / 60)
                    if duration <= 0:
                        print("  That time is already in the past. Enter a future date/time.")
                        continue
                    h, m = divmod(duration, 60)
                    print(f"  → {h}h {m}m from now  (until {end_utc.strftime('%Y-%m-%d %H:%M UTC')})")
                    break
                except ValueError:
                    pass
            print("  Invalid — use exactly 12 digits: YYYYMMDDhhmm")

        dashboard = pick("Live dashboard at http://localhost:5000?", [
            ("Yes", "open in your browser for live charts and metrics", True),
            ("No",  "console output only",                              False),
        ])

    else:  # backtest
        no_fetch = pick("Data source:", [
            ("Fetch fresh", "download latest data from Polymarket API", False),
            ("Cache only",  "use previously downloaded CSVs (faster)",  True),
        ])

    print()
    print(BOLD + "─" * 55 + RESET)
    print(f"  Strategy : {CYAN}{strategy}{RESET}")
    print(f"  Mode     : {CYAN}{mode}{RESET}")
    print(f"  Markets  : {markets}")
    print(f"  Cash     : ${cash:,.0f}")
    if mode == "paper":
        h, m = divmod(duration, 60)
        print(f"  Duration : {h}h {m}m  ({duration} min)")
        print(f"  Dashboard: {'yes' if dashboard else 'no'}")
    else:
        print(f"  No-fetch : {'yes' if no_fetch else 'no'}")

    cmd_parts = [
        "python main.py",
        f"--strategy {strategy}",
        f"--mode {mode}",
        f"--markets {markets}",
        f"--cash {int(cash)}",
    ]
    if mode == "paper":
        cmd_parts.append(f"--duration {duration}")
        if dashboard:
            cmd_parts.append("--dashboard")
    else:
        if no_fetch:
            cmd_parts.append("--no-fetch")
    print(f"\n  {DIM}CLI equivalent:{RESET}")
    print(f"  {CYAN}{' '.join(cmd_parts)}{RESET}")

    print(BOLD + "─" * 55 + RESET)

    confirm = pick("Start?", [
        ("Yes", "run with the settings above",    True),
        ("No",  "abort and exit",                 False),
    ])
    if not confirm:
        print("Aborted.")
        sys.exit(0)

    return argparse.Namespace(
        strategy    = strategy,
        mode        = mode,
        markets     = markets,
        cash        = cash,
        duration    = duration,
        dashboard   = dashboard,
        no_fetch    = no_fetch,
        name        = None,
        market_type = "standard",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) == 1:
        args = interactive_setup()
    else:
        parser = build_arg_parser()
        args   = parser.parse_args()

    mode_label = {"paper": "Paper Trading", "live": "Live Trading"}.get(args.mode, "Backtesting")
    market_type = getattr(args, "market_type", "standard")

    print()
    print("=" * 55)
    print(f"  Polymarket Agent — {mode_label}")
    print(f"  Strategy : {args.strategy}")
    if args.mode == "paper":
        print(f"  Market type: {market_type}")
    print(f"  Markets  : {args.markets}")
    print(f"  Cash     : ${args.cash:.2f}")
    if args.mode == "paper":
        print(f"  Duration : {args.duration} minutes")
    if args.mode == "live":
        print(f"  *** REAL MONEY — orders will be placed on Polymarket ***")
    print("=" * 55)

    strategy_class = STRATEGY_MAP[args.strategy]
    strategy       = strategy_class()

    # Apply strategy params — use tuned 5-min values when in 5min market mode
    if market_type == "5min":
        from config import (
            FIVE_MIN_MOMENTUM_LOOKBACK,
            FIVE_MIN_MEAN_REVERSION_WINDOW,
            FIVE_MIN_RSI_PERIOD,
        )
        strategy.setup(params={
            "lookback": FIVE_MIN_MOMENTUM_LOOKBACK,
            "window":   FIVE_MIN_MEAN_REVERSION_WINDOW,
            "period":   FIVE_MIN_RSI_PERIOD,
        })
    else:
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
                from shared.dashboard import start_in_thread as start_dashboard
                start_dashboard(host="127.0.0.1", port=5000)
            except OSError:
                print("Dashboard already running at http://localhost:5000")

        trader_class = FiveMinPaperTrader if market_type == "5min" else PaperTrader
        trader = trader_class(
            strategy         = strategy,
            portfolio        = portfolio,
            risk_manager     = risk_manager,
            num_markets      = args.markets,
            duration_minutes = args.duration,
            instance_name    = instance_name,
        )
        results = trader.run()

        if results:
            metrics_module.print_results(results, strategy.name + " [Paper]")
        else:
            print("Paper trading session produced no results.")

        print("Done.")
        return

    # ----------------------------------------------------------------
    # LIVE TRADING MODE
    # ----------------------------------------------------------------
    if args.mode == "live":
        instance_name = (
            _sanitize_name(args.name)
            if args.name
            else f"live_{args.strategy}_{time.strftime('%H%M')}"
        )

        # Lazy imports — py_clob_client is only needed in live mode
        from live_trader import LiveTrader
        from wallet import wallet_from_env

        # Load wallet + fetch real balance for the confirmation prompt
        print("\nLoading wallet credentials from .env...")
        try:
            wallet = wallet_from_env()
        except (ValueError, KeyError) as e:
            print(f"\nERROR: Could not load wallet: {e}")
            print("Copy .env.example to .env and fill in your credentials.")
            sys.exit(1)

        # Build a temporary client just to read the balance for the prompt
        print("Connecting to Polymarket CLOB to read balance...")
        try:
            tmp_client = wallet.build_clob_client()
            balance_info = tmp_client.get_balance_allowance(params={"asset_type": "USDC"})
            real_balance = float(balance_info.get("balance", args.cash))
        except Exception as e:
            print(f"WARNING: Could not read balance ({e}). Will show after startup.")
            real_balance = args.cash

        # Confirmation prompt — must type YES explicitly
        print()
        print("!" * 55)
        print("  WARNING: LIVE TRADING MODE")
        print("  Real money will be spent on Polymarket.")
        print("!" * 55)
        print(f"  Strategy:        {args.strategy}")
        print(f"  Markets:         {args.markets}")
        print(f"  Duration:        {args.duration} min")
        print(f"  Wallet:          {wallet.funder_address}")
        print(f"  Balance (chain): ${real_balance:,.2f} USDC")
        print(f"  Instance name:   {instance_name}")
        print("!" * 55)
        print()
        confirm = input("Type YES to continue with live trading: ").strip()
        if confirm != "YES":
            print("Aborted — live trading not started.")
            sys.exit(0)

        if args.dashboard:
            try:
                from shared.dashboard import start_in_thread as start_dashboard
                start_dashboard(host="127.0.0.1", port=5000)
            except OSError:
                print("Dashboard already running at http://localhost:5000")

        portfolio = Portfolio(starting_cash=real_balance)

        trader = LiveTrader(
            wallet           = wallet,
            strategy         = strategy,
            portfolio        = portfolio,
            risk_manager     = risk_manager,
            num_markets      = args.markets,
            duration_minutes = args.duration,
            instance_name    = instance_name,
        )
        results = trader.run()

        if results:
            metrics_module.print_results(results, strategy.name + " [Live]")
        else:
            print("Live trading session produced no results.")

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
