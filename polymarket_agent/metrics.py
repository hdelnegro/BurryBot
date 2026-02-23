"""
metrics.py — Performance measurement for the backtest.

After the backtest finishes, we compute statistics that tell us HOW WELL
the strategy performed.  These are the same metrics professional traders use.

Metrics calculated:
  - Total return %        How much did we make or lose overall?
  - Sharpe ratio          Return relative to risk (higher = better risk-adjusted)
  - Max drawdown %        Worst peak-to-trough loss (lower = better)
  - Win rate %            What fraction of SELL trades were profitable?
  - Total trades          How many trades did we execute?
  - Average PnL per trade Average profit/loss on each completed sell
"""

from typing import List
import pandas as pd
import numpy as np

from models import Trade


def compute_total_return(starting_cash: float, final_value: float) -> float:
    """
    Total return as a percentage.

    Example: started with $1000, ended with $1120 → +12.0%
    """
    if starting_cash <= 0:
        return 0.0
    return (final_value - starting_cash) / starting_cash * 100.0


def compute_sharpe_ratio(equity_curve: List[float], periods_per_year: int = 730) -> float:
    """
    Sharpe ratio: return per unit of risk.

    Calculated as: (mean of returns) / (std dev of returns) × sqrt(periods_per_year)

    Why periods_per_year=730?
      Our bars are 12 hours each → 2 bars/day × 365 days = 730 bars/year.

    Interpretation:
      < 0.0  Bad (losing money on a risk-adjusted basis)
      0.0–1.0  Below average
      1.0–2.0  Good
      > 2.0   Excellent

    Returns 0.0 if there's not enough data.
    """
    if len(equity_curve) < 2:
        return 0.0

    returns = pd.Series(equity_curve).pct_change().dropna()

    if returns.std() == 0:
        return 0.0  # No volatility — can't compute Sharpe

    sharpe = returns.mean() / returns.std() * np.sqrt(periods_per_year)
    return round(float(sharpe), 4)


def compute_max_drawdown(equity_curve: List[float]) -> float:
    """
    Maximum drawdown: the worst peak-to-trough drop in portfolio value.

    Example: peak=$1200, trough=$900 → drawdown = (1200-900)/1200 = 25.0%

    Returns a positive percentage (bigger = worse).
    """
    if len(equity_curve) < 2:
        return 0.0

    series    = pd.Series(equity_curve)
    peak      = series.cummax()          # running maximum at each point
    drawdown  = (series - peak) / peak   # fractional drop from each peak
    max_dd    = drawdown.min()           # most negative value = worst drawdown

    return round(abs(float(max_dd)) * 100.0, 2)  # return as positive %


def compute_win_rate(trades: List[Trade]) -> float:
    """
    Fraction of SELL trades that made a profit.

    Only SELL trades have a non-zero PnL — BUY trades are not wins or losses yet.

    Returns a percentage, e.g. 62.5 means 62.5% of sells were profitable.
    """
    sell_trades = [t for t in trades if t.action == "SELL"]

    if not sell_trades:
        return 0.0

    winners = sum(1 for t in sell_trades if t.pnl > 0)
    return round(winners / len(sell_trades) * 100.0, 2)


def compute_avg_pnl(trades: List[Trade]) -> float:
    """
    Average profit/loss per completed sell trade (in USDC).
    """
    sell_trades = [t for t in trades if t.action == "SELL"]

    if not sell_trades:
        return 0.0

    total_pnl = sum(t.pnl for t in sell_trades)
    return round(total_pnl / len(sell_trades), 4)


def compute_all_metrics(
    trades:         List[Trade],
    equity_curve:   List[float],
    starting_cash:  float,
    final_value:    float,
) -> dict:
    """
    Compute and return all metrics in one dictionary.

    Args:
        trades:        All Trade objects from portfolio.trade_log.
        equity_curve:  List of total portfolio values at each time step.
        starting_cash: Initial portfolio value.
        final_value:   Final portfolio value.

    Returns:
        A dict with all metric names and values.
    """
    sell_trades = [t for t in trades if t.action == "SELL"]

    return {
        "total_return_pct":   compute_total_return(starting_cash, final_value),
        "sharpe_ratio":       compute_sharpe_ratio(equity_curve),
        "max_drawdown_pct":   compute_max_drawdown(equity_curve),
        "win_rate_pct":       compute_win_rate(trades),
        "total_trades":       len(trades),
        "buy_trades":         len(trades) - len(sell_trades),
        "sell_trades":        len(sell_trades),
        "avg_pnl_per_trade":  compute_avg_pnl(trades),
        "final_value":        round(final_value, 2),
        "starting_cash":      starting_cash,
    }


def print_results(metrics: dict, strategy_name: str) -> None:
    """
    Print a formatted results table to the console.

    Args:
        metrics:       Output of compute_all_metrics().
        strategy_name: Name of the strategy that was tested.
    """
    total_return = metrics["total_return_pct"]
    return_sign  = "+" if total_return >= 0 else ""

    print()
    print("=" * 55)
    print(f"  BACKTEST RESULTS — {strategy_name}")
    print("=" * 55)
    print(f"  Starting cash:        ${metrics['starting_cash']:>10.2f}")
    print(f"  Final value:          ${metrics['final_value']:>10.2f}")
    print(f"  Total return:          {return_sign}{total_return:>9.2f}%")
    print(f"  Sharpe ratio:          {metrics['sharpe_ratio']:>10.4f}")
    print(f"  Max drawdown:          {metrics['max_drawdown_pct']:>9.2f}%")
    print(f"  Win rate:              {metrics['win_rate_pct']:>9.2f}%")
    print(f"  Total trades:          {metrics['total_trades']:>10}")
    print(f"    BUY  trades:         {metrics['buy_trades']:>10}")
    print(f"    SELL trades:         {metrics['sell_trades']:>10}")
    print(f"  Avg PnL/trade:        ${metrics['avg_pnl_per_trade']:>10.4f}")
    print("=" * 55)
    print()
