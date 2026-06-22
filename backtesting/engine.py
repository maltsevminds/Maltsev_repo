"""Vectorised single-asset long-only backtest engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    entry_price: float
    shares: float
    exit_ts: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    is_open: bool = True


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    initial_capital: float = 10_000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
) -> tuple[pd.DataFrame, list[Trade]]:
    """
    Simulate a long-only strategy on OHLCV data.

    Parameters
    ----------
    df : OHLCV DataFrame with DatetimeIndex
    signals : Series aligned to df.index — 1=enter long, -1=exit long, 0=hold
    initial_capital : starting cash in quote currency
    fee : fraction (0.001 = 0.1%)
    slippage : fraction applied to execution price

    Returns
    -------
    equity_curve : DataFrame with columns ['equity']
    trades : list of Trade objects
    """
    cash = float(initial_capital)
    position: Optional[Trade] = None
    trades: list[Trade] = []
    equity_values: list[float] = []

    closes = df["close"].values.astype(float)
    timestamps = df.index
    sig_arr = signals.reindex(df.index).fillna(0).values.astype(int)

    for i in range(len(df)):
        ts = timestamps[i]
        close = closes[i]
        sig = sig_arr[i]

        current_equity = cash + (position.shares * close if position else 0.0)
        equity_values.append(current_equity)

        if sig == 1 and position is None:
            exec_price = close * (1.0 + slippage)
            fee_cost = cash * fee
            shares = (cash - fee_cost) / exec_price
            cash = 0.0
            position = Trade(entry_ts=ts, entry_price=exec_price, shares=shares)

        elif sig == -1 and position is not None:
            exec_price = close * (1.0 - slippage)
            gross = position.shares * exec_price
            fee_cost = gross * fee
            net = gross - fee_cost

            position.exit_ts = ts
            position.exit_price = exec_price
            position.pnl = net - (position.shares * position.entry_price)
            position.pnl_pct = (exec_price / position.entry_price - 1.0) * 100.0
            position.is_open = False

            trades.append(position)
            cash = net
            position = None

    # Force-close any open position at the last bar
    if position is not None:
        exec_price = closes[-1] * (1.0 - slippage)
        gross = position.shares * exec_price
        fee_cost = gross * fee
        net = gross - fee_cost

        position.exit_ts = timestamps[-1]
        position.exit_price = exec_price
        position.pnl = net - (position.shares * position.entry_price)
        position.pnl_pct = (exec_price / position.entry_price - 1.0) * 100.0
        position.is_open = False
        trades.append(position)
        cash = net

    equity_curve = pd.DataFrame({"equity": equity_values}, index=df.index)
    return equity_curve, trades
