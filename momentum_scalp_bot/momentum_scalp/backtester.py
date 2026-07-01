"""Backtest engine — runs the full strategy over historical OHLCV.

Wires every module together on simulated fills:

    Indicators -> SignalEngine -> RiskManager (gate + sizing)
              -> PositionManager (ladder) -> Executor (sim fills) -> SQLite

Bars from all symbols are merged into one global timeline so the cross-symbol
risk rules (max positions, correlation clusters, daily/weekly limits) are
enforced exactly as they would be live. There is no look-ahead: a signal is
evaluated on a bar's *closed* values and entered at that bar's close; the 1h
bias is shifted one hour so only fully-closed hours inform a decision.

Funding history isn't replayed here, so the funding gate is treated as passing
(funding=0) in backtest — flagged in the README.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import pandas as pd

from .config import Config
from .db import Database
from .executor import Executor
from .indicators import enrich_bias, enrich_entry
from .position_tracker import PositionManager
from .risk_manager import RiskManager
from .signal_engine import SignalEngine


@dataclass
class BacktestResult:
    initial_equity: float
    final_equity: float
    equity_curve: List[Tuple[pd.Timestamp, float]] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)
    stats: Dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        s = self.stats
        return (
            f"trades={s['trades']}  win_rate={s['win_rate']:.1%}  "
            f"return={s['total_return_pct']:.2f}%  "
            f"PF={s['profit_factor']:.2f}  maxDD={s['max_drawdown_pct']:.2f}%  "
            f"final=${self.final_equity:,.2f}"
        )


def align_bias(bias_1h: pd.Series, index_5m: pd.DatetimeIndex) -> pd.Series:
    """Project the 1h bias onto the 5m index using only closed hours.

    Shift by one hour-bar so the currently-forming hour never leaks in, then
    forward-fill onto the 5m timeline."""
    shifted = bias_1h.shift(1)
    return shifted.reindex(index_5m, method="ffill")


class BacktestEngine:
    def __init__(self, config: Config, db: Database | None = None):
        self.cfg = config
        self.db = db or Database(":memory:")
        self.mode = "backtest"
        self.rm = RiskManager(config, config.backtest.initial_equity)
        self.signal = SignalEngine(config)
        self.mgr = PositionManager(config, self.db, mode=self.mode)
        self.executor = Executor(config, self.db)

    def run(self, data5: Dict[str, pd.DataFrame], data1h: Dict[str, pd.DataFrame]) -> BacktestResult:
        import asyncio

        return asyncio.run(self._run_async(data5, data1h))

    async def _run_async(self, data5, data1h) -> BacktestResult:
        # Precompute features + bias per symbol.
        frames: Dict[str, pd.DataFrame] = {}
        bias: Dict[str, pd.Series] = {}
        for sym, df in data5.items():
            frames[sym] = enrich_entry(df, self.cfg.strategy)
            b1h = enrich_bias(data1h[sym], self.cfg.strategy)["bias"]
            bias[sym] = align_bias(b1h, frames[sym].index)

        # Global, time-ordered event stream across all symbols.
        events: List[Tuple[pd.Timestamp, str]] = sorted(
            (ts, sym) for sym, f in frames.items() for ts in f.index
        )
        if not events:
            return self._result([])

        initial = self.cfg.backtest.initial_equity
        self.rm.on_equity_update(initial, events[0][0])
        curve: List[Tuple[pd.Timestamp, float]] = [(events[0][0], initial)]
        fees: Dict[str, float] = {}

        for ts, sym in events:
            bar = frames[sym].loc[ts]

            # 1) Manage an open position on this symbol (ladder or forced flatten).
            closed_this_bar = await self._manage_open(sym, ts, bar, fees, curve)

            # 2) Consider a fresh entry (never on the bar we just closed).
            if not closed_this_bar and sym not in self.mgr.positions:
                await self._maybe_enter(sym, ts, bar, bias, fees)

        return self._result(curve)

    # ------------------------------------------------------------------ #
    async def _manage_open(self, sym, ts, bar, fees, curve) -> bool:
        tp = self.mgr.positions.get(sym)
        if tp is None:
            return False

        # Weekly limit -> flatten everything at the bar close.
        if self.rm.must_flatten(ts):
            action = self.mgr.force_close(sym, float(bar["close"]), "weekly_flatten")
            if action is not None:
                fill = await self.executor.execute_action(tp, action)
                fees[sym] = fees.get(sym, 0.0) + fill.fee
                self._book_close(sym, tp, ts, fees, curve)
            return True

        before = tp.realized_pnl
        actions = self.mgr.on_bar(sym, float(bar["high"]), float(bar["low"]), float(bar["atr"]))
        for a in actions:
            fill = await self.executor.execute_action(tp, a)
            fees[sym] = fees.get(sym, 0.0) + fill.fee
        _ = before  # (per-slice PnL rolls into tp.realized_pnl)

        if tp.closed:
            self._book_close(sym, tp, ts, fees, curve)
            return True
        return False

    async def _maybe_enter(self, sym, ts, bar, bias, fees) -> None:
        if self.rm.must_flatten(ts):
            return
        decision = self.rm.can_open(sym, ts, self.mgr.open_symbols)
        if not decision.allowed:
            return
        sig = self.signal.evaluate(sym, bar, int(bias[sym].loc[ts]) if pd.notna(bias[sym].loc[ts]) else 0, 0.0)
        if not sig.is_actionable:
            return
        size = self.rm.compute_size(sig.entry, sig.targets.stop)
        if not size.is_valid:
            return
        tp = self.mgr.open_from_signal(sig, size.quantity, size.leverage, size.risk_usd)
        entry, _stop = await self.executor.open_position(tp, size.leverage)
        fees[sym] = entry.fee

    def _book_close(self, sym, tp, ts, fees, curve) -> None:
        net = tp.realized_pnl - fees.get(sym, 0.0)
        self.rm.register_close(sym, net, ts, was_stop=tp.was_stopped_out)
        self.db.record_equity(self.rm.equity, self.rm.high_water_mark, self.mode, ts=ts)
        curve.append((ts, self.rm.equity))
        fees[sym] = 0.0

    # ------------------------------------------------------------------ #
    def _result(self, curve) -> BacktestResult:
        initial = self.cfg.backtest.initial_equity
        final = self.rm.equity
        trades = [dict(r) for r in self.db.list_closed_trades(self.mode, limit=100000)]
        return BacktestResult(
            initial_equity=initial,
            final_equity=final,
            equity_curve=curve,
            trades=trades,
            stats=compute_stats(initial, final, trades, curve),
        )


def compute_stats(initial, final, trades, curve) -> Dict[str, float]:
    pnls = [t["realized_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    n = len(pnls)

    # Max drawdown from the equity curve (peak-to-trough).
    peak = initial
    max_dd = 0.0
    for _ts, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n) if n else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0,
        "total_return_pct": ((final - initial) / initial * 100.0) if initial else 0.0,
        "max_drawdown_pct": max_dd * 100.0,
        "avg_trade": (sum(pnls) / n) if n else 0.0,
    }
