"""PositionTracker tests — the TP1/TP2/runner ladder, stop-outs, reconcile."""

from datetime import datetime, timezone

import pytest

from momentum_scalp.config import Config
from momentum_scalp.db import Database
from momentum_scalp.position_tracker import (
    PositionManager,
    TrackedPosition,
)
from momentum_scalp.signal_engine import Side, SignalEngine

UTC = timezone.utc


def cfg() -> Config:
    return Config.model_validate({"symbols": ["BTC/USDT"]})


def long_signal():
    """entry 100, atr 5 -> stop 94, R 6, TP1 106, TP2 112."""
    bar = _bar(close=110.0, dhp=100.0, dlp=90.0, atr=5.0, rsi=65)
    return SignalEngine(cfg()).evaluate("BTC/USDT", bar, bias=1, funding_rate=0.0)


def short_signal():
    bar = _bar(close=90.0, dhp=110.0, dlp=100.0, atr=5.0, rsi=30)
    return SignalEngine(cfg()).evaluate("BTC/USDT", bar, bias=-1, funding_rate=0.0)


def _bar(close, dhp, dlp, atr, rsi):
    import pandas as pd

    s = pd.Series({
        "close": close, "volume": 300.0, "vol_sma": 100.0,
        "donchian_high_prev": dhp, "donchian_low_prev": dlp,
        "adx": 30.0, "rsi": rsi, "atr": atr,
    })
    s.name = pd.Timestamp("2024-01-01", tz=UTC)
    return s


def tracked_long(entry=100.0, atr=5.0, qty=10.0):
    """Directly build a tracked long: stop 94, R6, TP1 106, TP2 112."""
    from momentum_scalp.signal_engine import compute_targets

    sig = long_signal()
    tp = TrackedPosition.from_signal(1, sig, qty, cfg())
    assert tp.entry == 110.0  # sanity from the crafted signal
    return tp


# --------------------------------------------------------------------------- #
# Ladder — long
# --------------------------------------------------------------------------- #
def test_long_full_ladder_tp1_tp2_runner():
    # entry 110, atr 5 -> stop 104, R6, TP1 116, TP2 122. qty 10.
    tp = tracked_long(qty=10.0)
    assert (tp.tp1, tp.tp2, tp.stop, tp.r) == (116.0, 122.0, 104.0, 6.0)

    # TP1 tick
    acts = tp.on_price(116.0, atr=5.0)
    kinds = [(a.kind, a.reason) for a in acts]
    assert ("partial_close", "tp1") in kinds
    assert ("move_stop", "tp1_move_be") in kinds
    assert tp.remaining_qty == pytest.approx(5.0)  # 50% closed
    assert tp.stop == 110.0                          # moved to BE
    assert tp.realized_pnl == pytest.approx(0.5 * 10 * (116 - 110))  # 30

    # TP2 tick
    acts = tp.on_price(122.0, atr=5.0)
    assert any(a.reason == "tp2" for a in acts)
    assert tp.remaining_qty == pytest.approx(2.0)   # 20% runner remains
    assert tp.tp2_done and tp.trailing_stop == pytest.approx(122 - 1.5 * 5)  # 114.5
    assert tp.realized_pnl == pytest.approx(30 + 0.3 * 10 * (122 - 110))     # 30+36=66

    # price runs up -> trail ratchets
    tp.on_price(130.0, atr=5.0)
    assert tp.trailing_stop == pytest.approx(130 - 7.5)  # 122.5

    # pullback hits trail -> runner closes
    acts = tp.on_price(122.5, atr=5.0)
    assert any(a.reason == "trail_stop" for a in acts)
    assert tp.closed and tp.remaining_qty == 0.0
    # runner pnl = 2 * (122.5 - 110) = 25 -> total 91
    assert tp.realized_pnl == pytest.approx(66 + 2 * (122.5 - 110))


def test_long_initial_stop_out_is_full_loss():
    tp = tracked_long(qty=10.0)   # stop 104, entry 110
    acts = tp.on_price(104.0, atr=5.0)
    assert len(acts) == 1 and acts[0].reason == "stop"
    assert tp.closed
    assert tp.realized_pnl == pytest.approx(10 * (104 - 110))  # -60
    assert tp.was_stopped_out


def test_long_breakeven_stop_after_tp1_is_scratch():
    tp = tracked_long(qty=10.0)
    tp.on_price(116.0, atr=5.0)          # TP1 -> stop to BE 110
    acts = tp.on_price(110.0, atr=5.0)   # pull back to BE
    assert acts[0].reason == "be_stop"
    assert tp.closed
    # +30 from TP1, remaining 5 closed at BE (0 pnl) -> +30 net
    assert tp.realized_pnl == pytest.approx(30.0)
    assert tp.was_stopped_out is False   # net positive -> not a "stop" for pause


def test_conservative_stop_before_target_on_straddle_bar():
    # A bar that spans both the stop (104) and TP1 (116): stop wins.
    tp = tracked_long(qty=10.0)
    acts = tp.on_bar(high=120.0, low=104.0, atr=5.0)
    assert acts[0].kind == "close" and acts[0].reason == "stop"
    assert tp.realized_pnl == pytest.approx(-60.0)


# --------------------------------------------------------------------------- #
# Ladder — short (mirror)
# --------------------------------------------------------------------------- #
def test_short_ladder_tp1_tp2():
    sig = short_signal()  # entry 90, atr5 -> stop 96, R6, TP1 84, TP2 78
    tp = TrackedPosition.from_signal(1, sig, 10.0, cfg())
    assert (tp.tp1, tp.tp2, tp.stop) == (84.0, 78.0, 96.0)

    tp.on_price(84.0, atr=5.0)   # TP1
    assert tp.remaining_qty == pytest.approx(5.0) and tp.stop == 90.0  # BE
    assert tp.realized_pnl == pytest.approx(0.5 * 10 * (90 - 84))       # 30

    tp.on_price(78.0, atr=5.0)   # TP2
    assert tp.tp2_done
    assert tp.trailing_stop == pytest.approx(78 + 1.5 * 5)  # 85.5
    tp.on_price(70.0, atr=5.0)   # trail tightens downward
    assert tp.trailing_stop == pytest.approx(70 + 7.5)      # 77.5


def test_short_stop_out_is_loss():
    sig = short_signal()
    tp = TrackedPosition.from_signal(1, sig, 10.0, cfg())
    acts = tp.on_price(96.0, atr=5.0)
    assert acts[0].reason == "stop"
    assert tp.realized_pnl == pytest.approx(10 * (90 - 96))  # -60


# --------------------------------------------------------------------------- #
# Manager persistence + reconcile
# --------------------------------------------------------------------------- #
def test_manager_open_persist_and_close():
    db = Database(":memory:")
    mgr = PositionManager(cfg(), db, mode="paper")
    sig = long_signal()
    tp = mgr.open_from_signal(sig, qty=10.0, leverage=3, risk_usd=150.0)
    assert mgr.open_symbols == ["BTC/USDT"]
    row = db.get_position(tp.position_id)
    assert row["status"] == "open" and row["remaining_qty"] == 10.0

    mgr.on_price("BTC/USDT", 116.0, atr=5.0)         # TP1 -> partial + persist
    assert db.get_position(tp.position_id)["remaining_qty"] == pytest.approx(5.0)

    mgr.on_price("BTC/USDT", 104.0 + 6.0, atr=5.0)   # BE stop at 110 -> close
    assert db.get_position(tp.position_id)["status"] == "closed"
    assert mgr.open_symbols == []
    db.close()


def test_reconcile_infers_ladder_stage_from_remaining():
    db = Database(":memory:")
    mgr = PositionManager(cfg(), db, mode="paper")
    sig = long_signal()
    tp = mgr.open_from_signal(sig, qty=10.0, leverage=3, risk_usd=150.0)
    mgr.on_price("BTC/USDT", 116.0, atr=5.0)  # TP1 done -> remaining 5

    # Simulate a restart: fresh manager, rebuild from DB.
    mgr2 = PositionManager(cfg(), db, mode="paper")
    rebuilt = mgr2.reconcile()
    assert len(rebuilt) == 1
    r = rebuilt[0]
    assert r.symbol == "BTC/USDT"
    assert r.remaining_qty == pytest.approx(5.0)
    assert r.tp1_done is True and r.tp2_done is False
    assert r.tp1 == pytest.approx(116.0) and r.tp2 == pytest.approx(122.0)
    db.close()


def test_reconcile_runner_stage():
    db = Database(":memory:")
    mgr = PositionManager(cfg(), db, mode="paper")
    sig = long_signal()
    mgr.open_from_signal(sig, qty=10.0, leverage=3, risk_usd=150.0)
    mgr.on_price("BTC/USDT", 116.0, atr=5.0)  # TP1
    mgr.on_price("BTC/USDT", 122.0, atr=5.0)  # TP2 -> runner, remaining 2

    rebuilt = PositionManager(cfg(), db, mode="paper").reconcile()[0]
    assert rebuilt.tp2_done is True
    assert rebuilt.remaining_qty == pytest.approx(2.0)
    assert rebuilt.trailing_stop is not None
    db.close()
