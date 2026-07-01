"""Parameter optimization — grid search + walk-forward.

The search space is declared in ``config.yaml`` under ``optimize.grid`` as
dotted config paths mapped to candidate values, e.g.::

    optimize:
      metric: total_return_pct
      grid:
        strategy.adx_min: [20, 23, 26]
        strategy.atr_stop_mult: [1.0, 1.2, 1.5]

**Grid search** runs a backtest for every combination and ranks them by the
chosen metric. **Walk-forward** rolls a (train, test) window across the data:
on each fold it grid-searches the in-sample train slice, then evaluates the
single best parameter set on the *out-of-sample* test slice — with the engine's
``trade_from`` guard so the test's warm-up bars can't leak into its results.
OOS folds are chained to a compounded equity, the honest measure of whether the
optimization generalizes.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .backtester import BacktestEngine
from .config import Config

# Metrics where smaller is better; everything else is maximized.
_LOWER_IS_BETTER = {"max_drawdown_pct"}


# --------------------------------------------------------------------------- #
# Config overriding
# --------------------------------------------------------------------------- #
def apply_overrides(base: dict, overrides: Dict[str, object]) -> dict:
    """Return a deep-copied config dict with dotted-path ``overrides`` applied."""
    import copy

    out = copy.deepcopy(base)
    for path, value in overrides.items():
        node = out
        parts = path.split(".")
        for key in parts[:-1]:
            node = node.setdefault(key, {})
        node[parts[-1]] = value
    return out


def config_with(base: Config, overrides: Dict[str, object]) -> Config:
    """Build a new validated Config from ``base`` plus dotted overrides."""
    raw = base.model_dump(mode="json")
    return Config.model_validate(apply_overrides(raw, overrides))


def expand_grid(grid: Dict[str, List]) -> List[Dict[str, object]]:
    """Cartesian product of the grid -> list of override dicts (deterministic)."""
    if not grid:
        return [{}]
    keys = sorted(grid)
    combos = itertools.product(*(grid[k] for k in keys))
    return [dict(zip(keys, values)) for values in combos]


def metric_value(stats: dict, metric: str) -> float:
    return float(stats.get(metric, 0.0))


def is_better(a: float, b: float, metric: str) -> bool:
    return a < b if metric in _LOWER_IS_BETTER else a > b


# --------------------------------------------------------------------------- #
# Grid search
# --------------------------------------------------------------------------- #
@dataclass
class GridPoint:
    overrides: Dict[str, object]
    stats: dict

    @property
    def score_keys(self):
        return self.stats


def grid_search(
    base: Config,
    data5: Dict[str, pd.DataFrame],
    data1h: Dict[str, pd.DataFrame],
    *,
    metric: Optional[str] = None,
    trade_from: Optional[pd.Timestamp] = None,
) -> List[GridPoint]:
    """Run every grid combination and return them ranked best-first."""
    metric = metric or base.optimize.metric
    results: List[GridPoint] = []
    for overrides in expand_grid(base.optimize.grid):
        cfg = config_with(base, overrides)
        engine = BacktestEngine(cfg)
        res = engine.run(data5, data1h, trade_from=trade_from)
        results.append(GridPoint(overrides, res.stats))
    reverse = metric not in _LOWER_IS_BETTER
    results.sort(key=lambda gp: metric_value(gp.stats, metric), reverse=reverse)
    return results


# --------------------------------------------------------------------------- #
# Walk-forward
# --------------------------------------------------------------------------- #
@dataclass
class Fold:
    train_range: Tuple[pd.Timestamp, pd.Timestamp]
    test_range: Tuple[pd.Timestamp, pd.Timestamp]
    best_overrides: Dict[str, object]
    is_metric: float
    oos_stats: dict


@dataclass
class WalkForwardResult:
    metric: str
    folds: List[Fold] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)


def _reference_timeline(data5: Dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex([])
    for df in data5.values():
        idx = idx.union(df.index)
    return idx.sort_values()


def _slice(data: Dict[str, pd.DataFrame], start, end) -> Dict[str, pd.DataFrame]:
    return {s: df.loc[start:end] for s, df in data.items()}


def _slice_upto(data: Dict[str, pd.DataFrame], end) -> Dict[str, pd.DataFrame]:
    return {s: df.loc[:end] for s, df in data.items()}


def walk_forward(
    base: Config,
    data5: Dict[str, pd.DataFrame],
    data1h: Dict[str, pd.DataFrame],
    *,
    warmup_bars: int = 60,
) -> WalkForwardResult:
    wf = base.optimize.walk_forward
    metric = base.optimize.metric
    timeline = _reference_timeline(data5)
    n = len(timeline)
    result = WalkForwardResult(metric=metric)

    i = 0
    while i + wf.train + 1 < n:
        tr_start, tr_end = timeline[i], timeline[min(i + wf.train - 1, n - 1)]
        te_lo = i + wf.train
        if te_lo >= n:
            break
        te_start = timeline[te_lo]
        te_end = timeline[min(te_lo + wf.test - 1, n - 1)]

        # In-sample: optimize on the train slice.
        train5 = _slice(data5, tr_start, tr_end)
        train1h = _slice_upto(data1h, tr_end)
        ranked = grid_search(base, train5, train1h, metric=metric)
        best = ranked[0]

        # Out-of-sample: evaluate the winner, warming up on a prefix but only
        # trading from te_start onward.
        warm_start = timeline[max(0, te_lo - warmup_bars)]
        test5 = _slice(data5, warm_start, te_end)
        test1h = _slice_upto(data1h, te_end)
        cfg = config_with(base, best.overrides)
        oos = BacktestEngine(cfg).run(test5, test1h, trade_from=te_start)

        result.folds.append(Fold(
            train_range=(tr_start, tr_end),
            test_range=(te_start, te_end),
            best_overrides=best.overrides,
            is_metric=metric_value(best.stats, metric),
            oos_stats=oos.stats,
        ))
        i += wf.step

    result.aggregate = _aggregate(result.folds)
    return result


def _aggregate(folds: List[Fold]) -> dict:
    if not folds:
        return {"folds": 0}
    compounded = 1.0
    trades = wins = 0
    worst_dd = 0.0
    for f in folds:
        compounded *= 1.0 + f.oos_stats["total_return_pct"] / 100.0
        trades += f.oos_stats["trades"]
        wins += f.oos_stats["wins"]
        worst_dd = max(worst_dd, f.oos_stats["max_drawdown_pct"])
    return {
        "folds": len(folds),
        "oos_compounded_return_pct": (compounded - 1.0) * 100.0,
        "oos_trades": trades,
        "oos_win_rate": (wins / trades) if trades else 0.0,
        "oos_worst_drawdown_pct": worst_dd,
    }
