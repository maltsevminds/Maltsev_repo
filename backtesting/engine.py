"""Honest bar-signal, next-open execution backtest engine.

Execution model
---------------
* Signals are observed at bar i's close — no execution on that bar.
* All orders execute at bar i+1's OPEN (slippage applied to open price).
* SL and TP fire only when the next bar's open has already gapped through the
  level — no intrabar high/low look-ahead.
* trailing_stop parameter is accepted for API compatibility but silently
  ignored (intrabar trailing is impossible without sub-bar data).
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
    exit_reason: str = ""       # signal|stop_loss|take_profit|hold_bars|end_of_data


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
    trailing_stop: float = 0.0,   # ignored — no intrabar trailing stop
    hold_bars: int = 0,            # 0 = signal-only exit; >0 = force-close after N bars
) -> tuple[pd.DataFrame, list[Trade]]:
    """
    Long-only backtest without look-ahead bias.

    Parameters
    ----------
    df            : OHLCV DataFrame with DatetimeIndex; must have 'open' column.
    signals       : Series aligned to df.index — 1=enter, -1=exit, 0=hold.
    initial_capital : starting cash (quote currency).
    fee           : fraction charged on entry AND exit (0.001 = 0.1 %).
    slippage      : fraction applied to execution price (increases entry cost,
                    decreases exit proceeds).
    stop_loss     : fixed stop below entry; checked at next bar's open only
                    (gap-fill semantics).  0 = disabled.
    take_profit   : fixed target above entry; same gap-fill semantics. 0 = disabled.
    trailing_stop : accepted but ignored.
    hold_bars     : close position after this many bars regardless of signal.
                    0 means never force-close by time.
    """
    cash = float(initial_capital)
    position: Optional[Trade] = None
    entry_bar_idx: int = 0
    sl_price: float = 0.0
    tp_price: float = 0.0
    trades: list[Trade] = []
    equity_values: list[float] = []

    sig_arr    = signals.reindex(df.index).fillna(0).values.astype(int)
    opens      = df["open"].values.astype(float)
    closes     = df["close"].values.astype(float)
    timestamps = df.index

    sl_on = stop_loss   > 0.0
    tp_on = take_profit > 0.0

    # pending_enter / pending_exit carry the previous bar's decision to next bar
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
            entry_cash = cash                               # full cash committed
            shares     = cash * (1.0 - fee) / exec_price   # entry fee deducted
            cash       = 0.0
            position   = Trade(
                entry_ts=ts, entry_price=exec_price,
                shares=shares, entry_cash=entry_cash,
            )
            entry_bar_idx = i
            sl_price = exec_price * (1.0 - stop_loss)   if sl_on else 0.0
            tp_price = exec_price * (1.0 + take_profit) if tp_on else 0.0
            pending_enter = False

        # ── 2. Check exit conditions at this bar's open ───────────────────────
        if position is not None:
            bars_held   = i - entry_bar_idx
            exit_reason = None
            exec_exit   = 0.0

            # Gap-fill stop loss: open already at or below SL level
            if sl_on and o <= sl_price:
                exit_reason = "stop_loss"
                exec_exit   = o * (1.0 - slippage)

            # Gap-fill take profit: open already at or above TP level
            elif tp_on and o >= tp_price:
                exit_reason = "take_profit"
                exec_exit   = o * (1.0 - slippage)

            # Explicit exit signal from previous bar
            elif pending_exit:
                exit_reason = "signal"
                exec_exit   = o * (1.0 - slippage)

            # hold_bars timeout
            elif hold_bars > 0 and bars_held >= hold_bars:
                exit_reason = "hold_bars"
                exec_exit   = o * (1.0 - slippage)

            if exit_reason:
                cash = _close_trade(position, ts, exec_exit, fee, exit_reason)
                trades.append(position)
                position     = None
                sl_price     = 0.0
                tp_price     = 0.0
                pending_exit = False

        # ── 3. Mark equity at this bar's close (mark-to-market) ───────────────
        equity_values.append(
            cash + (position.shares * c if position else 0.0)
        )

        # ── 4. Schedule orders for next bar based on this bar's signal ────────
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
