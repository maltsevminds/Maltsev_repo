"""Portfolio performance metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting.engine import Trade

_SECONDS_PER_YEAR = 365.0 * 24.0 * 3600.0


def _infer_periods_per_year(index: pd.Index) -> float:
    """Estimate how many bars fit in a year from the median bar spacing.

    Falls back to 252 (daily) when the spacing cannot be determined.
    """
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        return 252.0
    deltas = index.to_series().diff().dropna()
    if deltas.empty:
        return 252.0
    median_sec = float(deltas.dt.total_seconds().median())
    if median_sec <= 0:
        return 252.0
    return _SECONDS_PER_YEAR / median_sec


def calculate_metrics(
    equity_curve: pd.DataFrame,
    trades: list[Trade],
    initial_capital: float,
) -> dict:
    eq = equity_curve["equity"]
    final_equity = float(eq.iloc[-1])

    total_return_pct = (final_equity / initial_capital - 1.0) * 100.0

    # Max Drawdown
    rolling_peak = eq.cummax()
    drawdown = (eq - rolling_peak) / rolling_peak
    max_drawdown_pct = float(drawdown.min()) * 100.0

    # Annualised Sharpe / Sortino — scale by sqrt(periods per year), derived
    # from the actual bar spacing so intraday timeframes are not understated.
    periods_per_year = _infer_periods_per_year(eq.index)
    ann = np.sqrt(periods_per_year)

    bar_rets = eq.pct_change().dropna()
    sharpe = 0.0
    if len(bar_rets) > 1 and bar_rets.std() > 0:
        sharpe = float(bar_rets.mean() / bar_rets.std() * ann)

    # Sortino
    sortino = 0.0
    downside = bar_rets[bar_rets < 0]
    if len(downside) > 1 and downside.std() > 0:
        sortino = float(bar_rets.mean() / downside.std() * ann)

    # Trade statistics
    closed = [t for t in trades if not t.is_open]
    n_trades = len(closed)

    win_rate = 0.0
    profit_factor: float | str = 0.0
    avg_trade_return = 0.0
    expectancy_pct = 0.0

    if n_trades > 0:
        pnls     = [t.pnl     for t in closed]
        pnl_pcts = [t.pnl_pct for t in closed]

        winners     = [p for p in pnls     if p > 0]
        losers      = [p for p in pnls     if p <= 0]
        win_pcts    = [p for p in pnl_pcts if p > 0]
        loss_pcts   = [p for p in pnl_pcts if p <= 0]

        win_rate    = len(winners) / n_trades * 100.0
        gross_profit = sum(winners) if winners else 0.0
        gross_loss   = abs(sum(losers)) if losers else 0.0
        profit_factor = (
            round(gross_profit / gross_loss, 3) if gross_loss > 0 else float("inf")
        )
        avg_trade_return = float(np.mean(pnl_pcts))

        wr_frac      = len(win_pcts) / n_trades
        avg_win_pct  = float(np.mean(win_pcts))  if win_pcts  else 0.0
        avg_loss_pct = float(np.mean(loss_pcts)) if loss_pcts else 0.0
        expectancy_pct = wr_frac * avg_win_pct + (1.0 - wr_frac) * avg_loss_pct

    return {
        "total_return": round(total_return_pct, 2),
        "max_drawdown": round(max_drawdown_pct, 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "profit_factor": "∞" if profit_factor == float("inf") else round(float(profit_factor), 3),
        "win_rate": round(win_rate, 2),
        "total_trades": n_trades,
        "avg_trade_return": round(avg_trade_return, 3),
        "expectancy_pct":   round(expectancy_pct, 3),
        "final_equity": round(final_equity, 2),
    }
