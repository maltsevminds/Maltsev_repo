"""Backtest engine tests — bias alignment, a driven end-to-end trade, stats."""

import numpy as np
import pandas as pd
import pytest

from momentum_scalp.backtester import BacktestEngine, align_bias, compute_stats
from momentum_scalp.config import Config
from momentum_scalp.signal_engine import Side, Signal, compute_targets

UTC = "UTC"


def cfg():
    # small EMA bias period so the 1h warm-up data stays tiny
    return Config.model_validate({
        "symbols": ["BTC/USDT"],
        "strategy": {"ema_bias_period": 20},
        "backtest": {"initial_equity": 10_000, "fee_rate": 0.0, "slippage_pct": 0.0},
    })


def test_align_bias_has_no_lookahead():
    idx1h = pd.date_range("2024-01-01", periods=4, freq="1h", tz=UTC)
    bias = pd.Series([1, -1, 1, -1], index=idx1h)
    idx5 = pd.date_range("2024-01-01", periods=48, freq="5min", tz=UTC)
    aligned = align_bias(bias, idx5)
    # First hour has no *closed* prior hour -> NaN; hour 2 uses hour 1's value.
    assert pd.isna(aligned.iloc[0])
    assert aligned.loc[pd.Timestamp("2024-01-01 01:05", tz=UTC)] == 1  # hour1's bias


class DrivenSignal:
    """Fires one long entry on the first warmed bar; flat afterwards."""

    def __init__(self, config):
        self.cfg = config
        self.fired = False

    def evaluate(self, symbol, bar, bias, funding):
        if self.fired or pd.isna(bar.get("atr")):
            return Signal(symbol, Side.flat)
        entry, atr = float(bar["close"]), float(bar["atr"])
        self.fired = True
        return Signal(symbol, Side.long, ts=bar.name, entry=entry, atr=atr,
                      targets=compute_targets(entry, atr, Side.long, self.cfg))


def _rising_then_drop():
    """5m OHLCV: rise (enter + hit TP1/TP2), then a sharp drop (trail/stop out)."""
    n_up, n_down = 70, 12
    up = 100 + 0.2 * np.arange(n_up)              # 100 -> 113.8
    down = up[-1] - 1.5 * np.arange(1, n_down + 1)  # sharp fall
    close = np.concatenate([up, down])
    n = len(close)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz=UTC)
    return pd.DataFrame({
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.full(n, 100.0),
    }, index=idx)


def _bias_frame(n=60):
    idx = pd.date_range("2023-12-31", periods=n, freq="1h", tz=UTC)
    close = 100 + 0.5 * np.arange(n)  # steadily up -> long bias
    return pd.DataFrame({
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": np.full(n, 100.0),
    }, index=idx)


def test_backtest_runs_a_full_trade_and_reports_stats():
    c = cfg()
    engine = BacktestEngine(c)
    engine.signal = DrivenSignal(c)  # deterministic entry

    data5 = {"BTC/USDT": _rising_then_drop()}
    data1h = {"BTC/USDT": _bias_frame()}
    result = engine.run(data5, data1h)

    assert result.stats["trades"] >= 1
    assert result.final_equity != result.initial_equity
    # Up-then-down after a long entry with TP ladder -> net gain.
    assert result.final_equity > result.initial_equity
    assert len(result.equity_curve) >= 2
    for k in ("win_rate", "profit_factor", "max_drawdown_pct", "total_return_pct"):
        assert k in result.stats
    assert isinstance(result.summary(), str)


def test_backtest_no_signal_no_trades():
    c = cfg()
    engine = BacktestEngine(c)
    # default SignalEngine on flat synthetic data -> no entries
    data5 = {"BTC/USDT": _rising_then_drop()}
    data1h = {"BTC/USDT": _bias_frame()}
    result = engine.run(data5, data1h)
    assert result.stats["trades"] == 0
    assert result.final_equity == result.initial_equity


def test_compute_stats_values():
    trades = [{"realized_pnl": 100.0}, {"realized_pnl": -40.0}, {"realized_pnl": 60.0}]
    curve = [(0, 10_000), (1, 10_100), (2, 10_060), (3, 10_120)]
    s = compute_stats(10_000, 10_120, trades, curve)
    assert s["trades"] == 3 and s["wins"] == 2 and s["losses"] == 1
    assert s["win_rate"] == pytest.approx(2 / 3)
    assert s["profit_factor"] == pytest.approx(160 / 40)
    assert s["total_return_pct"] == pytest.approx(1.2)
    assert s["max_drawdown_pct"] == pytest.approx((10_100 - 10_060) / 10_100 * 100)
