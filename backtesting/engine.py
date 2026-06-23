"""Honest bar-signal, next-open execution backtest engine.

Execution model
---------------
* Signals are observed at bar i's close — no execution on that bar.
* All orders execute at bar i+1's OPEN (slippage applied to open price).
* SL, TP, and trailing stop fire at bar open using gap-fill semantics
  (triggered only when the open has already gapped through the level).
* Trailing stop tracks the highest CLOSE seen since entry; level updates
  at bar close, checked at next bar's open — no intrabar look-ahead.
* hold_bars > 0: force-close at open[entry_bar + hold_bars].
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class Trade:
    entry_ts: pd.Timestamp
    entry_price: float          # execution price at entry (with slippage)
    shares: float
    entry_cash: float = 0.0     # total cash committed; used for PnL calculation
    exit_ts: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    is_open: bool = True
    exit_reason: str = ""       # signal|stop_loss|trailing_stop|take_profit|hold_bars|end_of_data


def _close_trade(
    position: Trade,
    ts: pd.Timestamp,
    exec_price: float,
    fee: float,
    reason: str,
) -> float:
    """Close position, deduct exit fee, return net cash. Updates Trade in-place."""
    gross = position.shares * exec_price
    net   = gross * (1.0 - fee)
    position.exit_ts     = ts
    position.exit_price  = exec_price
    position.pnl         = net - position.entry_cash
    position.pnl_pct     = (
        (net / position.entry_cash - 1.0) * 100.0
        if position.entry_cash else 0.0
    )
    position.is_open     = False
    position.exit_reason = reason
    return net


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    initial_capital: float = 10_000.0,
    fee: float = 0.001,
    slippage: float = 0.001,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    trailing_stop: float = 0.0,
    hold_bars: int = 0,
) -> tuple[pd.DataFrame, list[Trade]]:
    """
    Long-only backtest without look-ahead bias.

    Parameters
    ----------
    df              : OHLCV DataFrame with DatetimeIndex; must have 'open' column.
    signals         : Series aligned to df.index — 1=enter, -1=exit, 0=hold.
    initial_capital : starting cash (quote currency).
    fee             : fraction charged on entry AND exit (0.001 = 0.1 %).
    slippage        : fraction applied to execution price.
    stop_loss       : fixed stop below entry; gap-fill at bar open. 0 = disabled.
    take_profit     : fixed target above entry; gap-fill at bar open. 0 = disabled.
    trailing_stop   : X% below the highest CLOSE seen since entry.
                      Level updates at each bar's close; checked at next open
                      (gap-fill semantics, no intrabar look-ahead). 0 = disabled.
    hold_bars       : close after this many bars regardless of signal. 0 = disabled.
    """
    cash = float(initial_capital)
    position: Optional[Trade] = None
    entry_bar_idx: int = 0
    sl_price: float = 0.0
    tp_price: float = 0.0
    trail_high: float = 0.0    # highest close seen since entry
    trail_level: float = 0.0   # trailing stop trigger price
    trades: list[Trade] = []
    equity_values: list[float] = []

    sig_arr    = signals.reindex(df.index).fillna(0).values.astype(int)
    opens      = df["open"].values.astype(float)
    closes     = df["close"].values.astype(float)
    timestamps = df.index

    sl_on    = stop_loss     > 0.0
    tp_on    = take_profit   > 0.0
    trail_on = trailing_stop > 0.0

    pending_enter: bool = False
    pending_exit:  bool = False

    for i in range(len(df)):
        o   = opens[i]
        c   = closes[i]
        ts  = timestamps[i]
        sig = sig_arr[i]

        # ── 1. Execute pending entry at this bar's open ───────────────────────
        if pending_enter and position is None:
            exec_price = o * (1.0 + slippage)
            entry_cash = cash
            shares     = cash * (1.0 - fee) / exec_price
            cash       = 0.0
            position   = Trade(
                entry_ts=ts, entry_price=exec_price,
                shares=shares, entry_cash=entry_cash,
            )
            entry_bar_idx = i
            sl_price    = exec_price * (1.0 - stop_loss)   if sl_on    else 0.0
            tp_price    = exec_price * (1.0 + take_profit) if tp_on    else 0.0
            trail_high  = exec_price                        if trail_on else 0.0
            trail_level = exec_price * (1.0 - trailing_stop) if trail_on else 0.0
            pending_enter = False

        # ── 2. Check exit conditions at this bar's open (gap-fill) ───────────
        if position is not None:
            bars_held   = i - entry_bar_idx
            exit_reason = None
            exec_exit   = 0.0

            if sl_on and o <= sl_price:
                exit_reason = "stop_loss"
                exec_exit   = o * (1.0 - slippage)

            elif trail_on and trail_level > 0.0 and o <= trail_level:
                exit_reason = "trailing_stop"
                exec_exit   = o * (1.0 - slippage)

            elif tp_on and o >= tp_price:
                exit_reason = "take_profit"
                exec_exit   = o * (1.0 - slippage)

            elif pending_exit:
                exit_reason = "signal"
                exec_exit   = o * (1.0 - slippage)

            elif hold_bars > 0 and bars_held >= hold_bars:
                exit_reason = "hold_bars"
                exec_exit   = o * (1.0 - slippage)

            if exit_reason:
                cash = _close_trade(position, ts, exec_exit, fee, exit_reason)
                trades.append(position)
                position    = None
                sl_price    = 0.0
                tp_price    = 0.0
                trail_high  = 0.0
                trail_level = 0.0
                pending_exit = False

        # ── 3. Mark equity at this bar's close (mark-to-market) ───────────────
        equity_values.append(
            cash + (position.shares * c if position else 0.0)
        )

        # ── 4. Update trailing stop level using this bar's close ──────────────
        if trail_on and position is not None and c > trail_high:
            trail_high  = c
            trail_level = trail_high * (1.0 - trailing_stop)

        # ── 5. Schedule orders for next bar based on this bar's signal ────────
        if sig == 1 and position is None:
            pending_enter = True
        if sig == -1 and position is not None:
            pending_exit = True

    # ── Force close any open position at last bar's close ────────────────────
    if position is not None:
        exec_price = closes[-1] * (1.0 - slippage)
        cash = _close_trade(
            position, timestamps[-1], exec_price, fee, "end_of_data"
        )
        trades.append(position)

    equity_curve = pd.DataFrame({"equity": equity_values}, index=df.index)
    return equity_curve, trades
