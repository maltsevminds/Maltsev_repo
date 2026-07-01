"""SignalEngine tests — the entry boolean logic and stop/target math."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from momentum_scalp.config import Config
from momentum_scalp.signal_engine import (
    Side,
    SignalEngine,
    compute_stop,
    compute_targets,
)

UTC = timezone.utc


def cfg() -> Config:
    return Config.model_validate({"symbols": ["BTC/USDT"]})


def long_bar(**overrides) -> pd.Series:
    """A bar that satisfies every LONG entry condition; override to break one."""
    data = {
        "close": 110.0,
        "volume": 200.0,
        "vol_sma": 100.0,          # 200 > 1.5*100=150  -> volume_ok
        "donchian_high_prev": 100.0,  # 110 > 100        -> breakout
        "donchian_low_prev": 90.0,
        "adx": 30.0,               # > 23               -> adx_ok
        "rsi": 65.0,               # in [58, 78]        -> rsi_ok
        "atr": 5.0,
    }
    data.update(overrides)
    s = pd.Series(data)
    s.name = pd.Timestamp("2024-01-01 00:05", tz=UTC)
    return s


def short_bar(**overrides) -> pd.Series:
    data = {
        "close": 90.0,
        "volume": 200.0,
        "vol_sma": 100.0,
        "donchian_high_prev": 110.0,
        "donchian_low_prev": 100.0,  # 90 < 100          -> breakout
        "adx": 30.0,
        "rsi": 30.0,                 # in [22, 42]       -> rsi_ok
        "atr": 5.0,
    }
    data.update(overrides)
    s = pd.Series(data)
    s.name = pd.Timestamp("2024-01-01 00:05", tz=UTC)
    return s


eng = SignalEngine(cfg())
FUND_OK = 0.0001  # < 0.05%


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #
def test_long_entry_all_conditions_met():
    sig = eng.evaluate("BTC/USDT", long_bar(), bias=1, funding_rate=FUND_OK)
    assert sig.side is Side.long
    assert sig.is_actionable
    assert sig.entry == 110.0
    assert all(sig.reasons.values())


def test_short_entry_all_conditions_met():
    sig = eng.evaluate("BTC/USDT", short_bar(), bias=-1, funding_rate=FUND_OK)
    assert sig.side is Side.short
    assert all(sig.reasons.values())


# --------------------------------------------------------------------------- #
# Each gate, flipped one at a time -> flat, and the right reason is False
# --------------------------------------------------------------------------- #
def test_bias_must_agree_with_direction():
    # Bias short but a long-shaped bar -> the long breakout check fails.
    sig = eng.evaluate("BTC/USDT", long_bar(), bias=-1, funding_rate=FUND_OK)
    assert sig.side is Side.flat
    assert sig.reasons["breakout"] is False


def test_bias_zero_is_flat():
    sig = eng.evaluate("BTC/USDT", long_bar(), bias=0, funding_rate=FUND_OK)
    assert sig.side is Side.flat
    assert sig.reasons == {"bias_ok": False}


def test_no_breakout_blocks_long():
    sig = eng.evaluate("BTC/USDT", long_bar(close=99.0), bias=1, funding_rate=FUND_OK)
    assert sig.side is Side.flat and sig.reasons["breakout"] is False


def test_low_volume_blocks_entry():
    sig = eng.evaluate("BTC/USDT", long_bar(volume=140.0), bias=1, funding_rate=FUND_OK)
    assert sig.side is Side.flat and sig.reasons["volume_ok"] is False


def test_volume_strictly_greater_than_threshold():
    # Exactly at 1.5x is NOT enough (strict >).
    sig = eng.evaluate("BTC/USDT", long_bar(volume=150.0), bias=1, funding_rate=FUND_OK)
    assert sig.reasons["volume_ok"] is False


def test_weak_adx_blocks_entry():
    sig = eng.evaluate("BTC/USDT", long_bar(adx=23.0), bias=1, funding_rate=FUND_OK)
    assert sig.side is Side.flat and sig.reasons["adx_ok"] is False  # strict >


def test_rsi_outside_long_band_blocks():
    assert eng.evaluate("BTC/USDT", long_bar(rsi=57.9), bias=1, funding_rate=FUND_OK).reasons["rsi_ok"] is False
    assert eng.evaluate("BTC/USDT", long_bar(rsi=78.1), bias=1, funding_rate=FUND_OK).reasons["rsi_ok"] is False


def test_rsi_band_edges_inclusive():
    assert eng.evaluate("BTC/USDT", long_bar(rsi=58.0), bias=1, funding_rate=FUND_OK).side is Side.long
    assert eng.evaluate("BTC/USDT", long_bar(rsi=78.0), bias=1, funding_rate=FUND_OK).side is Side.long


def test_high_funding_blocks_entry():
    sig = eng.evaluate("BTC/USDT", long_bar(), bias=1, funding_rate=0.0006)
    assert sig.side is Side.flat and sig.reasons["funding_ok"] is False
    # negative funding beyond threshold also blocks
    assert eng.evaluate("BTC/USDT", long_bar(), bias=1, funding_rate=-0.0006).reasons["funding_ok"] is False


def test_warmup_nan_is_flat():
    sig = eng.evaluate("BTC/USDT", long_bar(atr=np.nan), bias=1, funding_rate=FUND_OK)
    assert sig.side is Side.flat and sig.reasons == {"warm": False}


# --------------------------------------------------------------------------- #
# Stop / target math
# --------------------------------------------------------------------------- #
def test_long_stop_and_targets():
    sig = eng.evaluate("BTC/USDT", long_bar(), bias=1, funding_rate=FUND_OK)
    t = sig.targets
    # stop = 110 - 1.2*5 = 104 ; R = 6
    assert t.stop == pytest.approx(104.0)
    assert t.r == pytest.approx(6.0)
    assert t.tp1 == pytest.approx(116.0)   # +1R
    assert t.tp2 == pytest.approx(122.0)   # +2R
    assert t.breakeven == pytest.approx(110.0)


def test_short_stop_and_targets():
    sig = eng.evaluate("BTC/USDT", short_bar(), bias=-1, funding_rate=FUND_OK)
    t = sig.targets
    # stop = 90 + 1.2*5 = 96 ; R = 6
    assert t.stop == pytest.approx(96.0)
    assert t.tp1 == pytest.approx(84.0)
    assert t.tp2 == pytest.approx(78.0)


def test_compute_stop_helper_directions():
    assert compute_stop(100, 5, Side.long, 1.2) == pytest.approx(94.0)
    assert compute_stop(100, 5, Side.short, 1.2) == pytest.approx(106.0)
    with pytest.raises(ValueError):
        compute_stop(100, 5, Side.flat, 1.2)


def test_targets_consistent_with_riskmanager_sizing():
    """R from the signal is exactly the stop distance the RiskManager sizes on."""
    from momentum_scalp.risk_manager import RiskManager

    c = cfg()
    sig = SignalEngine(c).evaluate("BTC/USDT", long_bar(), bias=1, funding_rate=FUND_OK)
    rm = RiskManager(c, 10_000)
    size = rm.compute_size(entry_price=sig.entry, stop_price=sig.targets.stop)
    modeled_loss = size.quantity * sig.targets.r
    assert modeled_loss == pytest.approx(size.risk_usd, rel=1e-9)
