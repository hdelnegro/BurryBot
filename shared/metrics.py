"""
shared/metrics.py — Performance measurement for backtests and paper trading.

Platform-agnostic: works for any prediction market platform.
"""

from typing import List
import pandas as pd
import numpy as np

from shared.models import Trade


def compute_total_return(starting_cash: float, final_value: float) -> float:
    if starting_cash <= 0:
        return 0.0
    return (final_value - starting_cash) / starting_cash * 100.0


def compute_sharpe_ratio(equity_curve: List[float], periods_per_year: int = 730) -> float:
    """
    Sharpe ratio: return per unit of risk.

    periods_per_year=730 assumes 12-hour bars (2/day × 365 days).
    """
    if len(equity_curve) < 2:
        return 0.0
    returns = pd.Series(equity_curve).pct_change().dropna()
    if returns.std() == 0:
        return 0.0
    sharpe = returns.mean() / returns.std() * np.sqrt(periods_per_year)
    return round(float(sharpe), 4)


def compute_max_drawdown(equity_curve: List[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    series   = pd.Series(equity_curve)
    peak     = series.cummax()
    drawdown = (series - peak) / peak
    max_dd   = drawdown.min()
    return round(abs(float(max_dd)) * 100.0, 2)


def compute_win_rate(trades: List[Trade]) -> float:
    sell_trades = [t for t in trades if t.action == "SELL"]
    if not sell_trades:
        return 0.0
    winners = sum(1 for t in sell_trades if t.pnl > 0)
    return round(winners / len(sell_trades) * 100.0, 2)


def compute_avg_pnl(trades: List[Trade]) -> float:
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
    sell_trades = [t for t in trades if t.action == "SELL"]
    return {
        "total_return_pct":  compute_total_return(starting_cash, final_value),
        "sharpe_ratio":      compute_sharpe_ratio(equity_curve),
        "max_drawdown_pct":  compute_max_drawdown(equity_curve),
        "win_rate_pct":      compute_win_rate(trades),
        "total_trades":      len(trades),
        "buy_trades":        len(trades) - len(sell_trades),
        "sell_trades":       len(sell_trades),
        "avg_pnl_per_trade": compute_avg_pnl(trades),
        "final_value":       round(final_value, 2),
        "starting_cash":     starting_cash,
    }


def print_results(metrics: dict, strategy_name: str) -> None:
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
