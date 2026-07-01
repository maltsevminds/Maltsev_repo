"""SignalEngine — turn one closed 5m bar (+ 1h bias + funding) into a trade
decision.

Entry rules (all must hold, in the direction dictated by the 1h bias):

    bias      1h close vs EMA200: long only above, short only below
    breakout  close breaks the *prior* Donchian(20) channel in that direction
    volume    bar volume > volume_mult * SMA20(volume)
    trend     ADX(14) > adx_min
    momentum  RSI(14) inside the long band (58-78) / short band (22-42)
    funding   |funding rate| < funding_max

Stop = entry ∓ atr_stop_mult * ATR(14, 5m). R (risk per unit) = |entry - stop|;
TP1/TP2 are +1R / +2R. The engine is pure and every individual gate is exposed
in ``Signal.reasons`` so the boolean logic is directly unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional

import pandas as pd

from .config import Config

# Indicator columns that must be non-NaN for a bar to be tradeable.
_REQUIRED = (
    "close",
    "donchian_high_prev",
    "donchian_low_prev",
    "vol_sma",
    "adx",
    "rsi",
    "atr",
)


class Side(str, Enum):
    long = "long"
    short = "short"
    flat = "flat"

    @property
    def sign(self) -> int:
        return {"long": 1, "short": -1, "flat": 0}[self.value]


@dataclass(frozen=True)
class Targets:
    stop: float
    tp1: float
    tp2: float
    breakeven: float  # where the stop moves after TP1 (== entry)
    r: float          # risk per unit = |entry - stop|
    runner_trail_atr_mult: float


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: Side
    ts: Optional[datetime] = None
    entry: float = 0.0
    atr: float = 0.0
    targets: Optional[Targets] = None
    reasons: Dict[str, bool] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.side is not Side.flat


# --------------------------------------------------------------------------- #
# Stop / target math (reused by Executor & PositionTracker)
# --------------------------------------------------------------------------- #
def compute_stop(entry: float, atr: float, side: Side, mult: float) -> float:
    if side is Side.long:
        return entry - mult * atr
    if side is Side.short:
        return entry + mult * atr
    raise ValueError("compute_stop requires a directional side")


def compute_targets(entry: float, atr: float, side: Side, cfg: Config) -> Targets:
    s = cfg.strategy
    t = cfg.targets
    stop = compute_stop(entry, atr, side, s.atr_stop_mult)
    r = abs(entry - stop)
    direction = side.sign
    return Targets(
        stop=stop,
        tp1=entry + direction * t.tp1_r * r,
        tp2=entry + direction * t.tp2_r * r,
        breakeven=entry,
        r=r,
        runner_trail_atr_mult=t.runner_trail_atr_mult,
    )


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class SignalEngine:
    def __init__(self, config: Config):
        self.cfg = config
        self.s = config.strategy

    def evaluate(
        self,
        symbol: str,
        bar: pd.Series,
        bias: int,
        funding_rate: float,
    ) -> Signal:
        """Evaluate the latest closed 5m ``bar``. ``bias`` is the +1/-1/0 flag
        from the last closed 1h bar; ``funding_rate`` is a fraction."""
        # Warm-up guard: any missing indicator -> no trade.
        if any(_missing(bar, c) for c in _REQUIRED):
            return Signal(symbol, Side.flat, reasons={"warm": False})

        # Direction is dictated by the trend bias.
        intended = Side.long if bias > 0 else Side.short if bias < 0 else Side.flat
        if intended is Side.flat:
            return Signal(symbol, Side.flat, reasons={"bias_ok": False})

        close = float(bar["close"])
        atr = float(bar["atr"])
        funding_ok = abs(funding_rate) < self.s.funding_max
        volume_ok = float(bar["volume"]) > self.s.volume_mult * float(bar["vol_sma"])
        adx_ok = float(bar["adx"]) > self.s.adx_min

        if intended is Side.long:
            breakout = close > float(bar["donchian_high_prev"])
            lo, hi = self.s.rsi_long
        else:
            breakout = close < float(bar["donchian_low_prev"])
            lo, hi = self.s.rsi_short
        rsi_ok = lo <= float(bar["rsi"]) <= hi

        reasons = {
            "warm": True,
            "bias_ok": True,
            "breakout": breakout,
            "volume_ok": volume_ok,
            "adx_ok": adx_ok,
            "rsi_ok": rsi_ok,
            "funding_ok": funding_ok,
        }

        if not (breakout and volume_ok and adx_ok and rsi_ok and funding_ok):
            return Signal(symbol, Side.flat, ts=_ts(bar), reasons=reasons)

        targets = compute_targets(close, atr, intended, self.cfg)
        return Signal(
            symbol=symbol,
            side=intended,
            ts=_ts(bar),
            entry=close,
            atr=atr,
            targets=targets,
            reasons=reasons,
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _missing(bar: pd.Series, col: str) -> bool:
    return col not in bar.index or pd.isna(bar[col])


def _ts(bar: pd.Series):
    name = bar.name
    return name.to_pydatetime() if isinstance(name, pd.Timestamp) else name
