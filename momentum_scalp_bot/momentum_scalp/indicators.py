"""Technical indicators — vectorized pandas/numpy, no external TA dependency.

Implemented natively (exact standard formulas, Wilder smoothing for
RSI/ATR/ADX) instead of pandas-ta, which is unavailable on Python 3.11 and
broken against numpy 2.x. Every function is pure: same input DataFrame ->
same output, which makes them trivially unit-testable and safe to reuse in
backtest, paper and live without any hidden state.

OHLCV DataFrame contract
------------------------
Columns: ``open, high, low, close, volume`` (lowercase floats), indexed by a
timezone-aware UTC ``DatetimeIndex`` sorted ascending, one row per closed bar.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd

from .config import StrategyCfg

OHLCV_COLS = ("open", "high", "low", "close", "volume")


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (adjust=False, the standard TA convention)."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (a.k.a. RMA / SMMA): EMA with alpha = 1/period."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    return _rma(true_range(df), period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder). Returns values in [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _rma(gain, period)
    avg_loss = _rma(loss, period)
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # All-gains window -> avg_loss == 0 -> RS = inf; define RSI = 100 there.
    out = out.where(avg_loss != 0.0, 100.0)
    # All-flat/all-losses handled naturally (avg_gain 0 -> RSI 0).
    return out


def adx(
    df: pd.DataFrame, period: int = 14
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index (Wilder). Returns (adx, +DI, -DI)."""
    up = df["high"].diff()
    down = -df["low"].diff()

    plus_dm = pd.Series(
        np.where((up > down) & (up > 0.0), up, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0.0), down, 0.0), index=df.index
    )

    atr_ = _rma(true_range(df), period)
    plus_di = 100.0 * _rma(plus_dm, period) / atr_
    minus_di = 100.0 * _rma(minus_dm, period) / atr_

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_ = _rma(dx, period)
    return adx_, plus_di, minus_di


def donchian(
    df: pd.DataFrame, period: int = 20
) -> Tuple[pd.Series, pd.Series]:
    """Donchian channel over ``period`` bars (inclusive of the current bar)."""
    upper = df["high"].rolling(period, min_periods=period).max()
    lower = df["low"].rolling(period, min_periods=period).min()
    return upper, lower


# --------------------------------------------------------------------------- #
# Feature bundles used by the SignalEngine
# --------------------------------------------------------------------------- #
def enrich_entry(df: pd.DataFrame, cfg: StrategyCfg) -> pd.DataFrame:
    """Add all 5m entry-timeframe indicator columns.

    Donchian breakout uses the channel of the *prior* ``period`` bars
    (``*_prev``), so "close breaks above the channel" excludes the breaking bar
    itself — the standard breakout definition.
    """
    _require_ohlcv(df)
    out = df.copy()

    up, lo = donchian(out, cfg.donchian_period)
    out["donchian_high"] = up
    out["donchian_low"] = lo
    out["donchian_high_prev"] = up.shift(1)
    out["donchian_low_prev"] = lo.shift(1)

    out["vol_sma"] = sma(out["volume"], cfg.volume_sma_period)

    adx_, plus_di, minus_di = adx(out, cfg.adx_period)
    out["adx"] = adx_
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    return out


def enrich_bias(df: pd.DataFrame, cfg: StrategyCfg) -> pd.DataFrame:
    """Add the 1h trend-bias columns: EMA200 and a +1/-1/0 bias flag."""
    _require_ohlcv(df)
    out = df.copy()
    out["ema_bias"] = ema(out["close"], cfg.ema_bias_period)
    bias = pd.Series(0, index=out.index, dtype="int64")
    bias = bias.mask(out["close"] > out["ema_bias"], 1)
    bias = bias.mask(out["close"] < out["ema_bias"], -1)
    out["bias"] = bias
    return out


def _require_ohlcv(df: pd.DataFrame) -> None:
    missing = [c for c in OHLCV_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    if not df.index.is_monotonic_increasing:
        raise ValueError("OHLCV index must be sorted ascending by time")
