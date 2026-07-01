"""PositionTracker — the position lifecycle state machine + reconcile.

Each open position runs a fixed ladder driven by price:

    TP1 (+1R)  close ``tp1_close_pct`` of the original qty, move stop to BE
    TP2 (+2R)  close ``tp2_close_pct``, start the runner trailing stop
    runner     trail the rest by ``runner_trail_atr_mult`` * ATR (ratchets)
    stop       any adverse touch of the active stop closes what remains

:class:`TrackedPosition` is pure: ``on_bar`` mutates its own state and returns
the list of :class:`PositionAction`\\ s the caller (Executor in live, the
backtest engine otherwise) should carry out. Intrabar ambiguity is resolved
conservatively — the stop is always checked before the targets, so a bar that
straddles both is treated as the stop filling first.

:class:`PositionManager` wires that to the SQLite store and can rebuild all
open positions after a restart (``reconcile``), inferring how far up the ladder
each one had progressed from its remaining quantity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config
from .db import Database, PositionRow
from .signal_engine import Side, Signal


# --------------------------------------------------------------------------- #
# Actions emitted for the Executor / backtest engine
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PositionAction:
    kind: str            # "partial_close" | "close" | "move_stop"
    symbol: str
    reason: str          # tp1 | tp2 | runner_trail | stop | be_stop | trail_stop ...
    qty: float = 0.0
    price: float = 0.0   # execution reference (partial/close) or new stop (move_stop)


def _pnl(side: Side, entry: float, price: float, qty: float) -> float:
    return (price - entry) * qty if side is Side.long else (entry - price) * qty


def _target_hit(fav: float, level: float, side: Side) -> bool:
    return fav >= level if side is Side.long else fav <= level


def _stop_hit(adv: float, stop: float, side: Side) -> bool:
    return adv <= stop if side is Side.long else adv >= stop


# --------------------------------------------------------------------------- #
# Tracked position (pure state machine)
# --------------------------------------------------------------------------- #
@dataclass
class TrackedPosition:
    position_id: int
    symbol: str
    side: Side
    entry: float
    initial_qty: float
    remaining_qty: float
    stop: float
    r: float
    tp1: float
    tp2: float
    tp1_close_pct: float
    tp2_close_pct: float
    runner_trail_atr_mult: float
    tp1_done: bool = False
    tp2_done: bool = False
    trailing_stop: Optional[float] = None
    realized_pnl: float = 0.0
    closed: bool = False

    # -- construction ------------------------------------------------- #
    @classmethod
    def from_signal(cls, position_id: int, signal: Signal, qty: float, cfg: Config):
        t = signal.targets
        return cls(
            position_id=position_id,
            symbol=signal.symbol,
            side=signal.side,
            entry=signal.entry,
            initial_qty=qty,
            remaining_qty=qty,
            stop=t.stop,
            r=t.r,
            tp1=t.tp1,
            tp2=t.tp2,
            tp1_close_pct=cfg.targets.tp1_close_pct,
            tp2_close_pct=cfg.targets.tp2_close_pct,
            runner_trail_atr_mult=t.runner_trail_atr_mult,
        )

    @classmethod
    def from_row(cls, row, cfg: Config) -> "TrackedPosition":
        """Rebuild after a restart. Ladder progress is inferred from how much
        of the original quantity is left."""
        side = Side(row["side"])
        direction = side.sign
        entry, r = row["entry_price"], row["r"]
        qty, remaining = row["qty"], row["remaining_qty"]
        filled_frac = 1.0 - (remaining / qty if qty else 0.0)
        # >~50% closed -> TP1 taken; >~80% closed -> TP2 taken (runner left).
        tp1_done = filled_frac > cfg.targets.tp1_close_pct - 1e-6
        tp2_done = filled_frac > (cfg.targets.tp1_close_pct + cfg.targets.tp2_close_pct) - 1e-6
        tp = cls(
            position_id=row["id"],
            symbol=row["symbol"],
            side=side,
            entry=entry,
            initial_qty=qty,
            remaining_qty=remaining,
            stop=row["stop_price"],
            r=r,
            tp1=entry + direction * cfg.targets.tp1_r * r,
            tp2=entry + direction * cfg.targets.tp2_r * r,
            tp1_close_pct=cfg.targets.tp1_close_pct,
            tp2_close_pct=cfg.targets.tp2_close_pct,
            runner_trail_atr_mult=cfg.targets.runner_trail_atr_mult,
            tp1_done=tp1_done,
            tp2_done=tp2_done,
            realized_pnl=row["realized_pnl"],
        )
        if tp2_done:
            tp.trailing_stop = row["stop_price"]
        return tp

    # -- price processing --------------------------------------------- #
    def on_price(self, price: float, atr: float) -> List[PositionAction]:
        return self.on_bar(price, price, atr)

    def on_bar(self, high: float, low: float, atr: float) -> List[PositionAction]:
        actions: List[PositionAction] = []
        if self.closed or self.remaining_qty <= 0:
            return actions

        long = self.side is Side.long
        direction = self.side.sign
        fav = high if long else low   # extreme that reaches targets
        adv = low if long else high   # extreme that hits stops

        eff_stop = (
            self.trailing_stop
            if self.tp2_done and self.trailing_stop is not None
            else self.stop
        )

        # 1) Stop first (conservative on straddle bars).
        if _stop_hit(adv, eff_stop, self.side):
            reason = "stop" if not self.tp1_done else (
                "be_stop" if not self.tp2_done else "trail_stop"
            )
            return [self._close_remaining(eff_stop, reason)]

        # 2) TP1 -> partial + move stop to break-even.
        if not self.tp1_done and _target_hit(fav, self.tp1, self.side):
            q = self._round(self.tp1_close_pct * self.initial_qty)
            self.realized_pnl += _pnl(self.side, self.entry, self.tp1, q)
            self.remaining_qty = self._round(self.remaining_qty - q)
            self.tp1_done = True
            self.stop = self.entry
            actions.append(PositionAction("partial_close", self.symbol, "tp1", q, self.tp1))
            actions.append(PositionAction("move_stop", self.symbol, "tp1_move_be", price=self.entry))

        # 3) TP2 -> partial + start the runner trailing stop.
        tp2_filled_now = False
        if self.tp1_done and not self.tp2_done and _target_hit(fav, self.tp2, self.side):
            q = self._round(self.tp2_close_pct * self.initial_qty)
            self.realized_pnl += _pnl(self.side, self.entry, self.tp2, q)
            self.remaining_qty = self._round(self.remaining_qty - q)
            self.tp2_done = True
            self.trailing_stop = fav - direction * self.runner_trail_atr_mult * atr
            tp2_filled_now = True
            actions.append(PositionAction("partial_close", self.symbol, "tp2", q, self.tp2))
            actions.append(PositionAction("move_stop", self.symbol, "runner_trail_init", price=self.trailing_stop))

        # 4) Runner: ratchet the trail; stop-out only on later bars.
        if self.tp2_done and self.remaining_qty > 0:
            candidate = fav - direction * self.runner_trail_atr_mult * atr
            new_trail = max(self.trailing_stop, candidate) if long else min(self.trailing_stop, candidate)
            if new_trail != self.trailing_stop:
                self.trailing_stop = new_trail
                actions.append(PositionAction("move_stop", self.symbol, "runner_trail", price=new_trail))
            if not tp2_filled_now and _stop_hit(adv, self.trailing_stop, self.side):
                actions.append(self._close_remaining(self.trailing_stop, "trail_stop"))

        return actions

    # -- helpers ------------------------------------------------------- #
    def _close_remaining(self, price: float, reason: str) -> PositionAction:
        q = self.remaining_qty
        self.realized_pnl += _pnl(self.side, self.entry, price, q)
        self.remaining_qty = 0.0
        self.closed = True
        return PositionAction("close", self.symbol, reason, q, price)

    @staticmethod
    def _round(x: float) -> float:
        # Kill floating dust so remaining hits clean 0 at the end of the ladder.
        return round(x, 12)

    @property
    def was_stopped_out(self) -> bool:
        """True when the position closed at a loss/BE via the stop path — the
        signal the RiskManager uses for its consecutive-stop pause."""
        return self.closed and self.realized_pnl <= 0.0


# --------------------------------------------------------------------------- #
# Manager (persistence + reconcile)
# --------------------------------------------------------------------------- #
class PositionManager:
    def __init__(self, config: Config, db: Database, mode: Optional[str] = None):
        self.cfg = config
        self.db = db
        self.mode = mode or config.mode.value
        self.positions: Dict[str, TrackedPosition] = {}

    @property
    def open_symbols(self) -> List[str]:
        return list(self.positions.keys())

    def open_from_signal(self, signal: Signal, qty: float, leverage: int, risk_usd: float) -> TrackedPosition:
        t = signal.targets
        pid = self.db.open_position(
            PositionRow(
                symbol=signal.symbol,
                side=signal.side.value,
                mode=self.mode,
                entry_ts=signal.ts,
                entry_price=signal.entry,
                qty=qty,
                leverage=leverage,
                stop_price=t.stop,
                r=t.r,
                risk_usd=risk_usd,
            )
        )
        tp = TrackedPosition.from_signal(pid, signal, qty, self.cfg)
        self.positions[signal.symbol] = tp
        return tp

    def on_bar(self, symbol: str, high: float, low: float, atr: float) -> List[PositionAction]:
        tp = self.positions.get(symbol)
        if tp is None:
            return []
        actions = tp.on_bar(high, low, atr)
        if actions:
            self._persist(tp)
        if tp.closed:
            self.positions.pop(symbol, None)
        return actions

    def on_price(self, symbol: str, price: float, atr: float) -> List[PositionAction]:
        return self.on_bar(symbol, price, price, atr)

    def _persist(self, tp: TrackedPosition) -> None:
        if tp.closed:
            self.db.close_position(
                tp.position_id,
                realized_pnl=tp.realized_pnl,
                avg_exit_price=tp.stop if tp.trailing_stop is None else tp.trailing_stop,
                exit_reason="ladder",
            )
        else:
            self.db.update_position(
                tp.position_id,
                remaining_qty=tp.remaining_qty,
                stop_price=tp.trailing_stop if tp.trailing_stop is not None else tp.stop,
                realized_pnl=tp.realized_pnl,
            )

    def reconcile(self) -> List[TrackedPosition]:
        """Rebuild in-memory tracked positions from the DB after a restart."""
        self.positions.clear()
        for row in self.db.list_open_positions(self.mode):
            tp = TrackedPosition.from_row(row, self.cfg)
            self.positions[tp.symbol] = tp
        return list(self.positions.values())
