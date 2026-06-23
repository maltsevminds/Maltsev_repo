"""Portfolio performance metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting.engine import Trade


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

    # Annualised Sharpe (assumes hourly/daily bars, scales by sqrt(252))
    daily_rets = eq.pct_change().dropna()
    sharpe = 0.0
    if len(daily_rets) > 1 and daily_rets.std() > 0:
        sharpe = float(daily_rets.mean() / daily_rets.std() * np.sqrt(252))

    # Sortino
    sortino = 0.0
    downside = daily_rets[daily_rets < 0]
    if len(downside) > 1 and downside.std() > 0:
        sortino = float(daily_rets.mean() / downside.std() * np.sqrt(252))

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
