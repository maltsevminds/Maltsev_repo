"""CLI entrypoint.

    python -m momentum_scalp.main --mode backtest [--data-dir ./data/hist]
    python -m momentum_scalp.main --mode paper
    python -m momentum_scalp.main --mode testnet
    python -m momentum_scalp.main --mode live      # locked unless confirm_live

Backtest loads OHLCV either from local CSVs (``--data-dir``, offline) or by
downloading the configured window via ccxt. Paper/testnet/live run the live
orchestrator (:class:`LiveTrader`). ``live`` refuses to start unless
``confirm_live: true`` — the mode enum + Config already enforce this, and we
double-check here before opening a real-money connection.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from .backtester import BacktestEngine
from .config import Config, Mode, load_config
from .data_feed import DataFeed, timeframe_to_ms
from .db import Database
from .executor import Executor, fill_kind, parse_client_id
from .indicators import OHLCV_COLS, enrich_bias, enrich_entry
from .optimizer import metric_value
from .position_tracker import PositionManager
from .risk_manager import RiskManager
from .signal_engine import SignalEngine
from .watchdog import Watchdog, build_notifier

log = logging.getLogger("momentum_scalp")


def order_ts(order: dict):
    """Best-effort UTC timestamp from a ccxt order dict (ms epoch), else now."""
    import datetime as _dt

    ms = order.get("timestamp")
    if ms:
        return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc)
    return _dt.datetime.now(tz=_dt.timezone.utc)


# --------------------------------------------------------------------------- #
# Data loading for backtests
# --------------------------------------------------------------------------- #
def read_ohlcv_csv(path: str | Path) -> pd.DataFrame:
    """Read an OHLCV CSV (timestamp,open,high,low,close,volume). The timestamp
    may be epoch ms/s or an ISO string."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = next((c for c in ("timestamp", "time", "ts", "date", "datetime") if c in df.columns), None)
    if ts_col is None:
        raise ValueError(f"{path}: no timestamp column found")
    ts = df[ts_col]
    if pd.api.types.is_numeric_dtype(ts):
        unit = "ms" if ts.iloc[-1] > 1e11 else "s"
        idx = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        idx = pd.to_datetime(ts, utc=True)
    out = df[list(OHLCV_COLS)].astype(float)
    out.index = idx
    return out.sort_index()


def load_from_dir(data_dir: str, symbols, timeframe: str) -> Dict[str, pd.DataFrame]:
    """Load ``<SYMBOL>_<tf>.csv`` files, e.g. BTCUSDT_5m.csv, from a directory."""
    out: Dict[str, pd.DataFrame] = {}
    d = Path(data_dir)
    for sym in symbols:
        fname = f"{sym.replace('/', '')}_{timeframe}.csv"
        p = d / fname
        if p.exists():
            out[sym] = read_ohlcv_csv(p)
        else:
            log.warning("missing %s — skipping %s", p, sym)
    return out


async def download_window(config: Config, timeframe: str) -> Dict[str, pd.DataFrame]:
    """Download the configured backtest window for every symbol via ccxt."""
    feed = DataFeed(config)
    bt = config.backtest
    since = int(pd.Timestamp(bt.start, tz="UTC").timestamp() * 1000)
    until = int(pd.Timestamp(bt.end, tz="UTC").timestamp() * 1000)
    out: Dict[str, pd.DataFrame] = {}
    try:
        for sym in config.symbols:
            out[sym] = await feed.fetch_ohlcv_history(sym, timeframe, since, until)
            log.info("downloaded %d %s bars for %s", len(out[sym]), timeframe, sym)
    finally:
        await feed.close()
    return out


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
def run_backtest(config: Config, data_dir: Optional[str], report_dir: Optional[str] = None) -> int:
    tf5, tf1h = config.timeframes.entry, config.timeframes.bias
    if data_dir:
        data5 = load_from_dir(data_dir, config.symbols, tf5)
        data1h = load_from_dir(data_dir, config.symbols, tf1h)
    else:
        data5 = asyncio.run(download_window(config, tf5))
        data1h = asyncio.run(download_window(config, tf1h))

    symbols = [s for s in config.symbols if s in data5 and s in data1h]
    if not symbols:
        log.error("no usable data (need both %s and %s per symbol)", tf5, tf1h)
        return 2
    data5 = {s: data5[s] for s in symbols}
    data1h = {s: data1h[s] for s in symbols}

    engine = BacktestEngine(config)
    result = engine.run(data5, data1h)
    print("\n=== Backtest result ===")
    print(f"symbols : {', '.join(symbols)}")
    print(f"window  : {config.backtest.start} -> {config.backtest.end}")
    print(result.summary())

    if report_dir:
        from .reporting import write_reports

        meta = {"symbols": symbols, "start": config.backtest.start, "end": config.backtest.end}
        paths = write_reports(result, report_dir, meta)
        print("\nreports written:")
        for name, p in paths.items():
            print(f"  {name:10s} {p}")
    return 0


def _load_backtest_data(config: Config, data_dir: Optional[str]):
    tf5, tf1h = config.timeframes.entry, config.timeframes.bias
    if data_dir:
        data5 = load_from_dir(data_dir, config.symbols, tf5)
        data1h = load_from_dir(data_dir, config.symbols, tf1h)
    else:
        data5 = asyncio.run(download_window(config, tf5))
        data1h = asyncio.run(download_window(config, tf1h))
    symbols = [s for s in config.symbols if s in data5 and s in data1h]
    return {s: data5[s] for s in symbols}, {s: data1h[s] for s in symbols}, symbols


def run_optimize(config: Config, data_dir: Optional[str], kind: str) -> int:
    from .optimizer import grid_search, walk_forward

    data5, data1h, symbols = _load_backtest_data(config, data_dir)
    if not symbols:
        log.error("no usable data for optimization")
        return 2
    if not config.optimize.grid:
        log.error("optimize.grid is empty in config.yaml — nothing to search")
        return 2
    metric = config.optimize.metric

    if kind == "grid":
        ranked = grid_search(config, data5, data1h)
        print(f"\n=== Grid search ({len(ranked)} combos, metric={metric}) ===")
        for gp in ranked[: config.optimize.top]:
            params = ", ".join(f"{k}={v}" for k, v in sorted(gp.overrides.items()))
            print(f"  {metric}={metric_value(gp.stats, metric):>8.3f}  "
                  f"trades={gp.stats['trades']:>3}  "
                  f"PF={gp.stats['profit_factor']:.2f}  "
                  f"maxDD={gp.stats['max_drawdown_pct']:.2f}%  |  {params}")
        return 0

    # walk-forward
    wf = walk_forward(config, data5, data1h)
    print(f"\n=== Walk-forward (metric={metric}) ===")
    for i, f in enumerate(wf.folds, 1):
        params = ", ".join(f"{k}={v}" for k, v in sorted(f.best_overrides.items()))
        print(f"  fold {i}: test {f.test_range[0].date()}..{f.test_range[1].date()}  "
              f"OOS return={f.oos_stats['total_return_pct']:>7.2f}%  "
              f"trades={f.oos_stats['trades']:>3}  |  {params}")
    a = wf.aggregate
    if a.get("folds"):
        print(f"\n  aggregate OOS: folds={a['folds']}  "
              f"compounded={a['oos_compounded_return_pct']:.2f}%  "
              f"trades={a['oos_trades']}  win={a['oos_win_rate']:.1%}  "
              f"worstDD={a['oos_worst_drawdown_pct']:.2f}%")
    return 0


# --------------------------------------------------------------------------- #
# Live / paper / testnet orchestration
# --------------------------------------------------------------------------- #
class LiveTrader:
    """Streams closed 5m bars per symbol and drives the same core the backtest
    uses. Warms up indicator buffers, reconciles open positions on start, and
    runs a Watchdog kill-switch alongside the streams."""

    def __init__(self, config: Config):
        self.cfg = config
        self.mode = config.mode
        self.db = Database(config.database.path)
        self.feed = DataFeed(config)
        self.rm = RiskManager(config, self._starting_equity())
        self.signal = SignalEngine(config)
        self.mgr = PositionManager(config, self.db, mode=self.mode.value)
        self.executor = Executor(config, self.db, exchange=None)
        self.notifier = build_notifier(config)
        self.watchdog = Watchdog(config, self.db, self.notifier, on_kill=self._on_kill)
        self._stop = asyncio.Event()

    def _starting_equity(self) -> float:
        row = None
        try:
            row = Database(self.cfg.database.path).last_equity(self.cfg.mode.value)
        except Exception:  # noqa: BLE001
            pass
        if row is not None:
            return float(row["equity"])
        return self.cfg.paper.initial_equity

    async def _on_kill(self, reason: str) -> None:
        log.critical("KILL-SWITCH: %s — flattening open positions", reason)
        # Best-effort flatten of every open position at last close.
        for sym, tp in list(self.mgr.positions.items()):
            buf = self.feed.buffer(sym, self.cfg.timeframes.entry)
            price = float(buf["close"].iloc[-1]) if len(buf) else tp.entry
            action = self.mgr.force_close(sym, price, "kill_switch")
            if action is not None:
                await self.executor.execute_action(tp, action)
        self._stop.set()

    async def run(self) -> int:
        # Share the executor's exchange with the feed so orders + WS use one conn.
        self.executor.exchange = self.feed._ensure_exchange()
        self.mgr.reconcile()
        await self.watchdog.alert(
            f"▶️ momentum-scalp starting in {self.mode.value} mode", kind="startup"
        )
        tasks = []
        try:
            tasks = [asyncio.create_task(self._run_symbol(s)) for s in self.cfg.symbols]
            tasks.append(asyncio.create_task(self._watch_loop()))
            if self.cfg.is_live_orders:
                # testnet/live: ladder exits fill on the exchange -> react to them.
                tasks.append(asyncio.create_task(self._orders_loop()))
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await self.feed.close()
            self.db.close()
        return 0

    async def _run_symbol(self, symbol: str) -> None:
        tf5, tf1h = self.cfg.timeframes.entry, self.cfg.timeframes.bias
        warm = max(self.cfg.strategy.donchian_period, self.cfg.strategy.adx_period) + 50
        await self.feed.warm_up(symbol, tf5, bars=max(warm, 260))
        await self.feed.warm_up(symbol, tf1h, bars=self.cfg.strategy.ema_bias_period + 5)

        async for _bar in self.feed.stream_closed_bars(symbol, tf5):
            await self._on_closed_bar(symbol)

    async def _on_closed_bar(self, symbol: str) -> None:
        tf5, tf1h = self.cfg.timeframes.entry, self.cfg.timeframes.bias
        feats = enrich_entry(self.feed.buffer(symbol, tf5), self.cfg.strategy)
        if feats.empty:
            return
        bar = feats.iloc[-1]
        ts = bar.name

        # 1) Manage an open position.
        tp = self.mgr.positions.get(symbol)
        if tp is not None:
            if self.cfg.is_live_orders:
                # Exits are resting exchange orders; here we only ratchet the
                # runner's trailing stop and re-place it when it moves.
                action = self.mgr.update_trail(
                    symbol, float(bar["high"]), float(bar["low"]), float(bar["atr"]))
                if action is not None:
                    await self.executor.execute_action(tp, action)
            else:
                # paper: simulate fills by crossing price.
                actions = self.mgr.on_bar(
                    symbol, float(bar["high"]), float(bar["low"]), float(bar["atr"]))
                for a in actions:
                    await self.executor.execute_action(tp, a)
                if tp.closed:
                    await self._book_close(symbol, tp, ts)
                    return

        # 2) Entry.
        if symbol in self.mgr.positions or not self.rm.can_open(symbol, ts, self.mgr.open_symbols):
            return
        if self.rm.must_flatten(ts):
            return
        bias_df = enrich_bias(self.feed.buffer(symbol, tf1h), self.cfg.strategy)
        bias = int(bias_df["bias"].iloc[-1]) if len(bias_df) else 0
        funding = await self.feed.fetch_funding_rate(symbol)
        sig = self.signal.evaluate(symbol, bar, bias, funding)
        if not sig.is_actionable:
            return
        size = self.rm.compute_size(sig.entry, sig.targets.stop)
        if not size.is_valid:
            return
        tp = self.mgr.open_from_signal(sig, size.quantity, size.leverage, size.risk_usd)
        await self.executor.open_position(tp, size.leverage)
        await self.watchdog.alert(
            f"📈 {sig.side.value} {symbol} @ {sig.entry} stop {sig.targets.stop}",
            kind="entry", symbol=symbol,
        )

    async def _orders_loop(self) -> None:
        """Live path: map exchange fills of our resting reduce-only orders onto
        the ladder (TP1 -> move stop to BE; TP2 -> start runner; stop -> close)."""
        async for order in self.feed.stream_orders():
            if str(order.get("status")) != "closed":  # only fully-filled orders
                continue
            cid = order.get("clientOrderId") or order.get("clientOrderID")
            parsed = parse_client_id(str(cid) if cid else "")
            if parsed is None:
                continue
            pid, purpose = parsed
            kind = fill_kind(purpose)
            if kind is None:
                continue
            tp = self.mgr.position_by_id(pid)
            if tp is None:
                continue
            price = float(order.get("average") or order.get("price")
                          or order.get("stopPrice") or tp.entry)
            follow_ups = self.mgr.apply_fill(tp.symbol, kind, price)
            for a in follow_ups:
                await self.executor.execute_action(tp, a)
            if tp.closed:
                await self._book_close(tp.symbol, tp, order_ts(order))

    async def _book_close(self, symbol: str, tp, ts) -> None:
        await self.executor.cancel_symbol_orders(symbol)
        self.rm.register_close(symbol, tp.realized_pnl, ts, was_stop=tp.was_stopped_out)
        self.db.record_equity(self.rm.equity, self.rm.high_water_mark, self.mode.value, ts=ts)
        await self.watchdog.alert(
            f"✅ closed {symbol} pnl={tp.realized_pnl:.2f} (equity ${self.rm.equity:,.2f})",
            kind="close", symbol=symbol,
        )

    async def _watch_loop(self) -> None:
        interval = max(1, self.cfg.watchdog.ws_timeout_seconds // 3)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            await self.watchdog.tick(
                self.feed.seconds_since_last_message(),
                has_open_position=bool(self.mgr.positions),
            )


def run_live(config: Config) -> int:
    if config.mode is Mode.live and not config.confirm_live:
        log.error("live mode is LOCKED (confirm_live=false). Refusing to start.")
        return 3
    return asyncio.run(LiveTrader(config).run())


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="momentum_scalp", description="Momentum-scalp futures bot")
    p.add_argument("--mode", required=True, choices=[m.value for m in Mode])
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--data-dir", default=None,
                   help="backtest: load OHLCV CSVs from this dir instead of downloading")
    p.add_argument("--report-dir", default=None,
                   help="backtest: write trades.csv, equity_curve.csv/.html, summary.json here")
    p.add_argument("--optimize", default=None, choices=["grid", "walkforward"],
                   help="backtest: run grid search / walk-forward over optimize.grid")
    p.add_argument("--log-level", default="INFO")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config, mode=args.mode)
    if config.mode is Mode.backtest:
        if args.optimize:
            return run_optimize(config, args.data_dir, args.optimize)
        return run_backtest(config, args.data_dir, args.report_dir)
    return run_live(config)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
