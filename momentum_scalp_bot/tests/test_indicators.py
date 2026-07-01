"""Indicator tests — deterministic, against hand-checkable inputs."""

import numpy as np
import pandas as pd
import pytest

from momentum_scalp import indicators as ind
from momentum_scalp.config import StrategyCfg

UTC = "UTC"


def _frame(closes, highs=None, lows=None, vols=None, tf="5min"):
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq=tf, tz=UTC)
    closes = np.asarray(closes, dtype=float)
    highs = np.asarray(highs, dtype=float) if highs is not None else closes + 1.0
    lows = np.asarray(lows, dtype=float) if lows is not None else closes - 1.0
    vols = np.asarray(vols, dtype=float) if vols is not None else np.full(n, 100.0)
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes, "volume": vols},
        index=idx,
    )


def test_ema_matches_manual_recursion():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = ind.ema(s, 3)
    # adjust=False: first valid at index 2 (min_periods), seeded by recursion.
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    # manual: e0=1, e1=1+0.5*(2-1)=1.5, e2=1.5+0.5*(3-1.5)=2.25
    assert out.iloc[2] == pytest.approx(2.25)


def test_sma_basic():
    s = pd.Series([2.0, 4.0, 6.0, 8.0])
    out = ind.sma(s, 2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == pytest.approx(3.0)
    assert out.iloc[3] == pytest.approx(7.0)


def test_rsi_bounds_and_all_up_is_100():
    closes = list(np.arange(1, 40, dtype=float))  # strictly increasing
    r = ind.rsi(pd.Series(closes), 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()
    assert r.iloc[-1] == pytest.approx(100.0)  # no down moves -> RSI 100


def test_rsi_all_down_is_zero():
    closes = list(np.arange(40, 1, -1, dtype=float))
    r = ind.rsi(pd.Series(closes), 14).dropna()
    assert r.iloc[-1] == pytest.approx(0.0)


def test_atr_constant_range():
    # high-low = 2 every bar, no gaps -> TR = 2 -> ATR converges to 2.
    n = 30
    closes = np.full(n, 100.0)
    df = _frame(closes, highs=closes + 1, lows=closes - 1)
    a = ind.atr(df, 14).dropna()
    assert a.iloc[-1] == pytest.approx(2.0, abs=1e-6)


def test_adx_strong_uptrend_has_plus_di_dominant():
    closes = list(np.arange(1, 60, dtype=float))
    df = _frame(closes, highs=np.array(closes) + 0.5, lows=np.array(closes) - 0.5)
    adx_, plus_di, minus_di = ind.adx(df, 14)
    assert plus_di.iloc[-1] > minus_di.iloc[-1]
    assert adx_.dropna().iloc[-1] > 20  # trending -> high ADX


def test_donchian_and_prev_shift():
    highs = [5, 6, 7, 8, 9, 10]
    lows = [1, 2, 1, 3, 2, 4]
    df = _frame([h - 0.5 for h in highs], highs=highs, lows=lows)
    up, lo = ind.donchian(df, 3)
    assert up.iloc[2] == pytest.approx(7.0)  # max(5,6,7)
    assert lo.iloc[2] == pytest.approx(1.0)  # min(1,2,1)
    assert up.iloc[5] == pytest.approx(10.0)  # max(8,9,10)


def test_enrich_entry_columns_present():
    cfg = StrategyCfg()
    closes = list(np.arange(1, 300, dtype=float))
    df = _frame(closes)
    out = ind.enrich_entry(df, cfg)
    for col in ("donchian_high_prev", "donchian_low_prev", "vol_sma",
                "adx", "rsi", "atr"):
        assert col in out.columns
    # prev channel is the plain channel shifted by one bar.
    assert out["donchian_high_prev"].iloc[50] == pytest.approx(
        out["donchian_high"].iloc[49]
    )


def test_enrich_bias_sign():
    cfg = StrategyCfg(ema_bias_period=5)
    up = list(np.arange(1, 40, dtype=float))       # price rising -> above EMA
    down = list(np.arange(40, 1, -1, dtype=float))  # price falling -> below EMA
    assert ind.enrich_bias(_frame(up), cfg)["bias"].iloc[-1] == 1
    assert ind.enrich_bias(_frame(down), cfg)["bias"].iloc[-1] == -1


def test_enrich_requires_ohlcv_columns():
    bad = pd.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError, match="missing columns"):
        ind.enrich_entry(bad, StrategyCfg())
