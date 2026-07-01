"""Risk gate + position sizing.

Everything here works off *realized* PnL and equity, exactly as the spec
requires. The manager is deliberately pure and clock-injected: every method
that needs "now" takes a timezone-aware UTC ``datetime``, so tests are fully
deterministic and there is no hidden global state.

Responsibilities
----------------
Sizing
    risk_usd = equity * risk_fraction
    stop_pct = |entry - stop| / entry
    notional = risk_usd / stop_pct
    leverage = min(notional / equity, leverage_cap)   (capped, integer)
    quantity = notional / entry

Gates (all reasons returned, not raised)
    - daily loss limit   -> halt new entries until the next UTC day
    - weekly loss limit  -> flatten everything, off until Monday
    - weekly drawdown    -> shrink risk fraction to the reduced value
    - N stops in a row   -> pause new entries for pause_minutes
    - max positions      -> hard cap on concurrent open positions
    - correlation cluster -> cap concurrent positions per cluster
    - no averaging down  -> refuse a second position on a symbol already open
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, List, Optional

from .config import Config


def _as_utc(dt: datetime) -> datetime:
    """Normalize to timezone-aware UTC; assume naive datetimes are already UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _week_start(dt: datetime) -> date:
    """Monday (UTC) of the week containing ``dt``."""
    d = _as_utc(dt).date()
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PositionSize:
    """Result of a sizing computation."""

    risk_usd: float
    stop_pct: float
    notional: float
    leverage: int
    quantity: float
    risk_fraction: float

    @property
    def is_valid(self) -> bool:
        return self.quantity > 0 and self.notional > 0


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of a gate check."""

    allowed: bool
    reason: str = "ok"

    def __bool__(self) -> bool:  # lets callers write `if decision:`
        return self.allowed


@dataclass
class _ClosedTrade:
    ts: datetime
    symbol: str
    pnl: float
    was_stop: bool


# --------------------------------------------------------------------------- #
# Risk manager
# --------------------------------------------------------------------------- #
class RiskManager:
    """Stateful risk gate. Feed it equity updates and trade closes; ask it
    whether a new entry is allowed and how big it should be."""

    def __init__(self, config: Config, starting_equity: float):
        if starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        self.cfg = config
        self.rcfg = config.risk

        self.equity: float = starting_equity
        self.high_water_mark: float = starting_equity

        # Anchors capture the realized equity at the start of the current UTC
        # day / week, so PnL% is measured against the right baseline.
        self._day: Optional[date] = None
        self._day_anchor_equity: float = starting_equity
        self._week: Optional[date] = None
        self._week_anchor_equity: float = starting_equity

        self.consecutive_stops: int = 0
        self.pause_until: Optional[datetime] = None
        self.closed_trades: List[_ClosedTrade] = []
        # Latched by the weekly loss limit; cleared automatically on Monday.
        self._week_locked_on: Optional[date] = None

    # ------------------------------------------------------------------ #
    # State updates
    # ------------------------------------------------------------------ #
    def on_equity_update(self, equity: float, now: datetime) -> None:
        """Record the latest *realized* equity and roll day/week anchors."""
        now = _as_utc(now)
        today = now.date()
        wk = _week_start(now)

        # When a new day/week starts, the baseline is the equity carried *into*
        # it — i.e. the prior equity — not this (possibly post-trade) value.
        if self._day is None:
            self._day, self._day_anchor_equity = today, equity
        elif today != self._day:
            self._day, self._day_anchor_equity = today, self.equity

        if self._week is None:
            self._week, self._week_anchor_equity = wk, equity
        elif wk != self._week:
            self._week, self._week_anchor_equity = wk, self.equity
            self._week_locked_on = None  # new week clears the weekly lock

        self.equity = equity
        if equity > self.high_water_mark:
            self.high_water_mark = equity

    def register_close(
        self,
        symbol: str,
        pnl: float,
        now: datetime,
        *,
        was_stop: bool,
    ) -> None:
        """Record a realized trade close and update streak / pause / equity."""
        now = _as_utc(now)
        self.closed_trades.append(_ClosedTrade(now, symbol, pnl, was_stop))

        if was_stop or pnl < 0:
            self.consecutive_stops += 1
            if self.consecutive_stops >= self.rcfg.consecutive_stops_pause:
                self.pause_until = now + timedelta(minutes=self.rcfg.pause_minutes)
        else:
            self.consecutive_stops = 0

        self.on_equity_update(self.equity + pnl, now)

        # Latch the weekly lock the moment the weekly limit is breached, so we
        # stay off even if equity later ticks back above the threshold.
        if self._weekly_pnl_pct() <= -self.rcfg.weekly_loss_limit:
            self._week_locked_on = _week_start(now)

    # ------------------------------------------------------------------ #
    # Derived metrics
    # ------------------------------------------------------------------ #
    def _daily_pnl_pct(self) -> float:
        if self._day_anchor_equity <= 0:
            return 0.0
        return (self.equity - self._day_anchor_equity) / self._day_anchor_equity

    def _weekly_pnl_pct(self) -> float:
        if self._week_anchor_equity <= 0:
            return 0.0
        return (self.equity - self._week_anchor_equity) / self._week_anchor_equity

    def rolling_7d_pnl(self, now: datetime) -> float:
        """Sum of realized PnL over the trailing 7 days."""
        cutoff = _as_utc(now) - timedelta(days=7)
        return sum(t.pnl for t in self.closed_trades if t.ts >= cutoff)

    def current_risk_fraction(self) -> float:
        """1.5% normally, cut to the reduced fraction when the weekly drawdown
        exceeds the reduce threshold."""
        if self._weekly_pnl_pct() <= -self.rcfg.weekly_drawdown_reduce:
            return self.rcfg.reduced_risk_per_trade
        return self.rcfg.risk_per_trade

    # ------------------------------------------------------------------ #
    # Sizing
    # ------------------------------------------------------------------ #
    def compute_size(
        self,
        entry_price: float,
        stop_price: float,
        *,
        equity: Optional[float] = None,
    ) -> PositionSize:
        """Risk-based position size. Works for both long and short (uses the
        absolute stop distance)."""
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        eq = self.equity if equity is None else equity
        if eq <= 0:
            raise ValueError("equity must be positive")

        stop_dist = abs(entry_price - stop_price)
        if stop_dist <= 0:
            raise ValueError("stop must differ from entry")

        risk_fraction = self.current_risk_fraction()
        risk_usd = eq * risk_fraction
        stop_pct = stop_dist / entry_price
        notional = risk_usd / stop_pct
        raw_leverage = notional / eq
        # Cap leverage, floor to an integer (exchanges take integer leverage).
        leverage = max(1, min(int(math.floor(raw_leverage)), self.cfg.leverage_cap))
        # If the cap bit, the effective notional (and thus risk) is bounded by
        # leverage * equity — never risk *more* than requested.
        capped_notional = min(notional, leverage * eq)
        quantity = capped_notional / entry_price

        return PositionSize(
            risk_usd=risk_usd,
            stop_pct=stop_pct,
            notional=capped_notional,
            leverage=leverage,
            quantity=quantity,
            risk_fraction=risk_fraction,
        )

    # ------------------------------------------------------------------ #
    # Gates
    # ------------------------------------------------------------------ #
    def is_paused(self, now: datetime) -> bool:
        return self.pause_until is not None and _as_utc(now) < self.pause_until

    def daily_halted(self) -> bool:
        return self._daily_pnl_pct() <= -self.rcfg.daily_loss_limit

    def weekly_halted(self, now: datetime) -> bool:
        return self._week_locked_on is not None and _week_start(now) == self._week_locked_on

    def must_flatten(self, now: datetime) -> bool:
        """True when the weekly limit demands closing every open position."""
        return self.weekly_halted(now)

    def can_open(
        self,
        symbol: str,
        now: datetime,
        open_symbols: Iterable[str],
    ) -> RiskDecision:
        """The full entry gate. ``open_symbols`` is the set of symbols with a
        live position right now (excluding the candidate)."""
        now = _as_utc(now)
        open_symbols = list(open_symbols)

        if self.weekly_halted(now):
            return RiskDecision(False, "weekly loss limit hit — flat until Monday")
        if self.daily_halted():
            return RiskDecision(False, "daily loss limit hit — halted until next UTC day")
        if self.is_paused(now):
            mins = math.ceil((self.pause_until - now).total_seconds() / 60)
            return RiskDecision(False, f"paused after {self.consecutive_stops} stops (~{mins}m left)")

        if self.rcfg.no_averaging_down and symbol in open_symbols:
            return RiskDecision(False, f"{symbol} already open — no averaging down")

        if len(open_symbols) >= self.rcfg.max_positions:
            return RiskDecision(False, f"max positions ({self.rcfg.max_positions}) reached")

        cluster = self.cfg.cluster_of(symbol)
        if cluster is not None:
            limit = self.rcfg.clusters[cluster].max
            members = set(self.rcfg.clusters[cluster].symbols)
            in_cluster = sum(1 for s in open_symbols if s in members)
            if in_cluster >= limit:
                return RiskDecision(
                    False, f"cluster '{cluster}' at capacity ({in_cluster}/{limit})"
                )

        return RiskDecision(True, "ok")

    # ------------------------------------------------------------------ #
    # Introspection (handy for logging / Telegram status)
    # ------------------------------------------------------------------ #
    def snapshot(self, now: datetime) -> dict:
        return {
            "equity": round(self.equity, 2),
            "high_water_mark": round(self.high_water_mark, 2),
            "daily_pnl_pct": round(self._daily_pnl_pct() * 100, 3),
            "weekly_pnl_pct": round(self._weekly_pnl_pct() * 100, 3),
            "rolling_7d_pnl": round(self.rolling_7d_pnl(now), 2),
            "risk_fraction": self.current_risk_fraction(),
            "consecutive_stops": self.consecutive_stops,
            "paused": self.is_paused(now),
            "daily_halted": self.daily_halted(),
            "weekly_halted": self.weekly_halted(now),
        }
