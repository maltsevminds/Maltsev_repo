"""Optimizer tests — overrides, grid expansion, ranking, walk-forward folds."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from momentum_scalp.config import Config
from momentum_scalp import optimizer as opt

UTC = "UTC"


def base_cfg(**optimize):
    raw = {"symbols": ["BTC/USDT"]}
    if optimize:
        raw["optimize"] = optimize
    return Config.model_validate(raw)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_apply_overrides_dotted_paths():
    base = {"strategy": {"adx_min": 23}, "leverage_cap": 20}
    out = opt.apply_overrides(base, {"strategy.adx_min": 26, "leverage_cap": 10})
    assert out["strategy"]["adx_min"] == 26 and out["leverage_cap"] == 10
    assert base["strategy"]["adx_min"] == 23  # original untouched (deep copy)


def test_config_with_rebuilds_valid_config():
    cfg = opt.config_with(base_cfg(), {"strategy.adx_min": 30, "leverage_cap": 10})
    assert cfg.strategy.adx_min == 30 and cfg.leverage_cap == 10
    assert cfg.strategy.donchian_period == 20  # untouched default
    assert cfg.mode.value == "backtest"


def test_expand_grid_cartesian_and_empty():
    grid = {"a": [1, 2], "b": [3, 4, 5]}
    combos = opt.expand_grid(grid)
    assert len(combos) == 6
    assert {"a": 1, "b": 3} in combos and {"a": 2, "b": 5} in combos
    assert opt.expand_grid({}) == [{}]  # no grid -> single empty combo


def test_is_better_metric_direction():
    assert opt.is_better(2.0, 1.0, "total_return_pct") is True   # maximize
    assert opt.is_better(1.0, 2.0, "max_drawdown_pct") is True   # minimize


# --------------------------------------------------------------------------- #
# grid_search + walk_forward (engine monkeypatched for determinism/speed)
# --------------------------------------------------------------------------- #
class FakeEngine:
    """Stats depend only on adx_min so ordering is predictable."""

    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, data5, data1h, trade_from=None):
        v = float(self.cfg.strategy.adx_min)
        return SimpleNamespace(stats={
            "total_return_pct": v, "max_drawdown_pct": v, "trades": 1, "wins": 1,
            "win_rate": 1.0, "profit_factor": 2.0,
        })


@pytest.fixture
def patched_engine(monkeypatch):
    monkeypatch.setattr(opt, "BacktestEngine", FakeEngine)


def test_grid_search_ranks_best_first(patched_engine):
    cfg = base_cfg(metric="total_return_pct", grid={"strategy.adx_min": [20, 26, 23]})
    ranked = opt.grid_search(cfg, {}, {})
    got = [gp.overrides["strategy.adx_min"] for gp in ranked]
    assert got == [26, 23, 20]  # maximizing -> descending


def test_grid_search_minimizes_drawdown_metric(patched_engine):
    cfg = base_cfg(metric="max_drawdown_pct", grid={"strategy.adx_min": [20, 26, 23]})
    ranked = opt.grid_search(cfg, {}, {})
    got = [gp.overrides["strategy.adx_min"] for gp in ranked]
    assert got == [20, 23, 26]  # minimizing -> ascending


def _tl(n, start="2024-01-01"):
    idx = pd.date_range(start, periods=n, freq="5min", tz=UTC)
    close = 100 + 0.01 * np.arange(n)
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": np.full(n, 100.0)}, index=idx)


def test_walk_forward_folds_and_aggregate(patched_engine):
    cfg = base_cfg(
        metric="total_return_pct",
        grid={"strategy.adx_min": [20, 26]},
        walk_forward={"train": 4, "test": 2, "step": 2},
    )
    data5 = {"BTC/USDT": _tl(12)}
    data1h = {"BTC/USDT": _tl(12)}
    wf = opt.walk_forward(cfg, data5, data1h, warmup_bars=1)
    assert wf.folds  # produced folds
    # Each fold picks the winning param (adx_min=26 -> best return under FakeEngine)
    assert all(f.best_overrides["strategy.adx_min"] == 26 for f in wf.folds)
    assert wf.aggregate["folds"] == len(wf.folds)
    assert "oos_compounded_return_pct" in wf.aggregate
    # test windows are disjoint & advancing
    starts = [f.test_range[0] for f in wf.folds]
    assert starts == sorted(starts)


def test_walk_forward_empty_when_too_short(patched_engine):
    cfg = base_cfg(grid={"strategy.adx_min": [20]},
                   walk_forward={"train": 100, "test": 50, "step": 50})
    wf = opt.walk_forward(cfg, {"BTC/USDT": _tl(10)}, {"BTC/USDT": _tl(10)})
    assert wf.aggregate == {"folds": 0}
