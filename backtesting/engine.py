"""Vectorised single-asset long-only backtest engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
    exit_reason: str = ""  # signal | stop_loss | take_profit | trailing_stop | end_of_data


def _close_trade(
    position: Trade,
    ts: pd.Timestamp,
    exec_price: float,
    fee: float,
    reason: str,
) -> float:
    gross = position.shares * exec_price
    fee_cost = gross * fee
    net = gross - fee_cost
    position.exit_ts = ts
    position.exit_price = exec_price
    position.pnl = net - (position.shares * position.entry_price)
    position.pnl_pct = (exec_price / position.entry_price - 1.0) * 100.0
    position.is_open = False
    position.exit_reason = reason
    return net


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    initial_capital: float = 10_000.0,
    fee: float = 0.001,
    slippage: float = 0.0005,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    trailing_stop: float = 0.0,
) -> tuple[pd.DataFrame, list[Trade]]:
    """
    Simulate a long-only strategy on OHLCV data.

    Parameters
    ----------
    df            : OHLCV DataFrame with DatetimeIndex
    signals       : Series aligned to df.index — 1=enter long, -1=exit long, 0=hold
    initial_capital : starting cash in quote currency
    fee           : fraction (0.001 = 0.1%)
    slippage      : fraction applied to execution price
    stop_loss     : fixed stop — fraction below entry price (0 = disabled)
    take_profit   : fixed target — fraction above entry price (0 = disabled)
    trailing_stop : trailing stop — fraction below rolling peak high since entry (0 = disabled)
    """
    cash = float(initial_capital)
    position: Optional[Trade] = None
    peak_high: float = 0.0          # rolling highest high since entry (for trailing stop)
    trades: list[Trade] = []
    equity_values: list[float] = []

    closes = df["close"].values.astype(float)
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    timestamps = df.index
    sig_arr = signals.reindex(df.index).fillna(0).values.astype(int)

    sl_on = stop_loss > 0.0
    tp_on = take_profit > 0.0
    ts_on = trailing_stop > 0.0

    for i in range(len(df)):
        ts = timestamps[i]
        close = closes[i]
        high = highs[i]
        low = lows[i]
        sig = sig_arr[i]

        current_equity = cash + (position.shares * close if position else 0.0)
        equity_values.append(current_equity)

        if position is not None:
            # Update rolling peak for trailing stop
            if ts_on:
                peak_high = max(peak_high, high)

            # ── Fixed Stop Loss ───────────────────────────────────────────────
            if sl_on:
                sl_price = position.entry_price * (1.0 - stop_loss)
                if low <= sl_price:
                    exec_price = sl_price * (1.0 - slippage)
                    cash = _close_trade(position, ts, exec_price, fee, "stop_loss")
                    trades.append(position)
                    position = None
                    continue

            # ── Trailing Stop ─────────────────────────────────────────────────
            if ts_on and position is not None:
                trail_level = peak_high * (1.0 - trailing_stop)
                if low <= trail_level:
                    exec_price = trail_level * (1.0 - slippage)
                    cash = _close_trade(position, ts, exec_price, fee, "trailing_stop")
                    trades.append(position)
                    position = None
                    continue

            # ── Take Profit ───────────────────────────────────────────────────
            if tp_on and position is not None:
                tp_price = position.entry_price * (1.0 + take_profit)
                if high >= tp_price:
                    exec_price = tp_price * (1.0 - slippage)
                    cash = _close_trade(position, ts, exec_price, fee, "take_profit")
                    trades.append(position)
                    position = None
                    continue

            # ── Signal Exit ───────────────────────────────────────────────────
            if sig == -1 and position is not None:
                exec_price = close * (1.0 - slippage)
                cash = _close_trade(position, ts, exec_price, fee, "signal")
                trades.append(position)
                position = None
                continue

        # ── Signal Entry ──────────────────────────────────────────────────────
        if sig == 1 and position is None:
            exec_price = close * (1.0 + slippage)
            fee_cost = cash * fee
            shares = (cash - fee_cost) / exec_price
            cash = 0.0
            peak_high = high
            position = Trade(entry_ts=ts, entry_price=exec_price, shares=shares)

    # Force-close any open position at the last bar
    if position is not None:
        exec_price = closes[-1] * (1.0 - slippage)
        cash = _close_trade(position, timestamps[-1], exec_price, fee, "end_of_data")
        trades.append(position)

    equity_curve = pd.DataFrame({"equity": equity_values}, index=df.index)
    return equity_curve, trades
