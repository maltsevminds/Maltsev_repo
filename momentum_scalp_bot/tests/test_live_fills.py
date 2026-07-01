"""Event-driven live-fill path: on_fill/update_trail, apply_fill, resting TPs,
client-id parsing, order stream."""

import asyncio
from datetime import datetime, timezone

import pytest

from momentum_scalp.config import Config
from momentum_scalp.data_feed import DataFeed
from momentum_scalp.db import Database, PositionRow
from momentum_scalp.executor import Executor, fill_kind, parse_client_id
from momentum_scalp.position_tracker import PositionManager, TrackedPosition
from momentum_scalp.signal_engine import Side, SignalEngine

UTC = timezone.utc


def cfg(mode="testnet", **wd):
    raw = {"symbols": ["BTC/USDT"], "mode": mode}
    if wd:
        raw["watchdog"] = wd
    return Config.model_validate(raw)


def long_tp(pid=1):
    # entry 100, qty 10, stop 94, tp1 106, tp2 112
    return TrackedPosition(
        position_id=pid, symbol="BTC/USDT", side=Side.long, entry=100.0,
        initial_qty=10.0, remaining_qty=10.0, stop=94.0, r=6.0, tp1=106.0, tp2=112.0,
        tp1_close_pct=0.5, tp2_close_pct=0.3, runner_trail_atr_mult=1.5,
    )


# --------------------------------------------------------------------------- #
# TrackedPosition.on_fill + update_trail
# --------------------------------------------------------------------------- #
def test_on_fill_tp1_moves_stop_to_be():
    tp = long_tp()
    acts = tp.on_fill("tp1", 106.0)
    assert tp.remaining_qty == pytest.approx(5.0)
    assert tp.stop == 100.0 and tp.tp1_done
    assert tp.realized_pnl == pytest.approx(30.0)
    assert [(a.kind, a.reason, a.price) for a in acts] == [("move_stop", "tp1_move_be", 100.0)]


def test_on_fill_tp2_starts_runner_then_trail_ratchets():
    tp = long_tp()
    tp.on_fill("tp1", 106.0)
    acts = tp.on_fill("tp2", 112.0)
    assert tp.remaining_qty == pytest.approx(2.0) and tp.tp2_done
    assert tp.realized_pnl == pytest.approx(66.0)  # 30 + 36
    assert acts == []                              # no immediate follow-up
    assert tp.trailing_stop is None

    a = tp.update_trail(high=120.0, low=118.0, atr=5.0)  # init trail
    assert a.reason == "runner_trail_init" and tp.trailing_stop == pytest.approx(112.5)
    a = tp.update_trail(high=130.0, low=128.0, atr=5.0)  # ratchet up
    assert a.reason == "runner_trail" and tp.trailing_stop == pytest.approx(122.5)
    assert tp.update_trail(high=125.0, low=124.0, atr=5.0) is None  # no loosening


def test_on_fill_stop_before_tp1_is_full_loss():
    tp = long_tp()
    acts = tp.on_fill("stop", 94.0)
    assert acts == [] and tp.closed
    assert tp.realized_pnl == pytest.approx(-60.0) and tp.was_stopped_out


def test_on_fill_runner_stop_closes_at_trail():
    tp = long_tp()
    tp.on_fill("tp1", 106.0)
    tp.on_fill("tp2", 112.0)
    tp.update_trail(130.0, 128.0, 5.0)   # trail 122.5
    tp.on_fill("stop", 122.5)            # exchange stop filled
    assert tp.closed
    assert tp.realized_pnl == pytest.approx(66.0 + 2 * (122.5 - 100.0))  # 111
    assert tp.was_stopped_out is False   # net positive


# --------------------------------------------------------------------------- #
# PositionManager.apply_fill + lookup
# --------------------------------------------------------------------------- #
def test_manager_apply_fill_persists_and_looks_up():
    db = Database(":memory:")
    mgr = PositionManager(cfg(), db, mode="testnet")
    sig = SignalEngine(cfg()).evaluate("BTC/USDT", _entry_bar(), bias=1, funding_rate=0.0)
    tp = mgr.open_from_signal(sig, qty=10.0, leverage=5, risk_usd=150.0)

    assert mgr.position_by_id(tp.position_id) is tp
    follow = mgr.apply_fill("BTC/USDT", "tp1", tp.tp1)
    assert follow[0].kind == "move_stop"
    assert db.get_position(tp.position_id)["remaining_qty"] == pytest.approx(5.0)

    mgr.apply_fill("BTC/USDT", "stop", tp.entry)  # BE stop -> close
    assert db.get_position(tp.position_id)["status"] == "closed"
    assert mgr.position_by_id(tp.position_id) is None
    db.close()


def _entry_bar():
    import pandas as pd

    s = pd.Series({
        "close": 110.0, "volume": 300.0, "vol_sma": 100.0,
        "donchian_high_prev": 100.0, "donchian_low_prev": 90.0,
        "adx": 30.0, "rsi": 65.0, "atr": 5.0,
    })
    s.name = pd.Timestamp("2024-01-01", tz=UTC)
    return s


# --------------------------------------------------------------------------- #
# Executor: resting TPs in live, client-id parsing
# --------------------------------------------------------------------------- #
class FakeExchange:
    def __init__(self):
        self.calls = []
        self._id = 0

    async def set_leverage(self, leverage, symbol):
        pass

    async def cancel_all_orders(self, symbol):
        return True

    async def create_order(self, symbol, type_, side, qty, price, params):
        self.calls.append((type_, side, qty, params))
        self._id += 1
        return {"id": self._id, "average": 100.0, "fee": {"cost": 0.0}}


def test_live_open_places_resting_tp1_tp2():
    db = Database(":memory:")
    pid = db.open_position(PositionRow(
        symbol="BTC/USDT", side="long", mode="testnet",
        entry_ts=datetime(2024, 1, 1, tzinfo=UTC), entry_price=100.0, qty=10.0,
        leverage=5, stop_price=94.0, r=6.0, risk_usd=150.0))
    tp = long_tp(pid=pid)
    fake = FakeExchange()
    ex = Executor(cfg("testnet", reconnect_backoff_seconds=0.001), db, exchange=fake)
    asyncio.run(ex.open_position(tp, leverage=5))

    types = [c[0] for c in fake.calls]
    # entry + stop + tp1 + tp2, all portable ccxt-unified market/trigger orders
    assert types == ["market", "market", "market", "market"]
    assert "stopLossPrice" in fake.calls[1][3]        # the protective stop
    tp1_call = fake.calls[2]
    assert tp1_call[2] == pytest.approx(5.0)          # 0.5 * 10
    assert tp1_call[3]["reduceOnly"] is True and tp1_call[3]["takeProfitPrice"] == 106.0
    assert fake.calls[3][2] == pytest.approx(3.0)     # 0.3 * 10 for tp2
    db.close()


def test_parse_client_id_and_fill_kind():
    assert parse_client_id("msb-testnet-42-stop-1") == (42, "stop-1")
    assert parse_client_id("msb-live-7-entry") == (7, "entry")
    assert parse_client_id("not-ours") is None
    assert parse_client_id("msb-live-x-tp1") is None  # bad pid
    assert fill_kind("tp1") == "tp1"
    assert fill_kind("stop-2") == "stop"
    assert fill_kind("tp2") == "tp2"
    assert fill_kind("entry") is None


# --------------------------------------------------------------------------- #
# DataFeed.stream_orders
# --------------------------------------------------------------------------- #
class FakeOrderWS:
    def __init__(self, batches):
        self.batches = list(batches)

    def milliseconds(self):
        return 0

    async def watch_orders(self):
        if not self.batches:
            raise asyncio.CancelledError
        return self.batches.pop(0)


def test_stream_orders_yields_and_updates_heartbeat():
    order = {"clientOrderId": "msb-testnet-1-tp1", "status": "closed", "average": 106.0}
    feed = DataFeed(cfg(), exchange=FakeOrderWS([[order]]))

    async def first():
        agen = feed.stream_orders()
        o = await agen.__anext__()
        await agen.aclose()
        return o

    got = asyncio.run(first())
    assert got["clientOrderId"] == "msb-testnet-1-tp1"
    assert feed.seconds_since_last_message() < float("inf")  # heartbeat updated
