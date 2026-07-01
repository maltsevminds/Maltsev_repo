"""Executor tests — simulated fills, idempotency, retry, reduce-only stops."""

import asyncio
from datetime import datetime, timezone

import pytest

from momentum_scalp.config import Config
from momentum_scalp.db import Database, PositionRow
from momentum_scalp.executor import Executor
from momentum_scalp.position_tracker import PositionAction, TrackedPosition
from momentum_scalp.signal_engine import Side


def cfg(mode="paper", **watchdog):
    raw = {"symbols": ["BTC/USDT"], "mode": mode}
    if watchdog:
        raw["watchdog"] = watchdog
    if mode == "live":
        raw["confirm_live"] = True
    return Config.model_validate(raw)


def make_position(db, side=Side.long, entry=100.0, qty=10.0, stop=94.0):
    """Insert the backing positions row (satisfies the orders FK) and return a
    matching in-memory TrackedPosition."""
    pid = db.open_position(PositionRow(
        symbol="BTC/USDT", side=side.value, mode="paper",
        entry_ts=datetime(2024, 1, 1, tzinfo=timezone.utc), entry_price=entry,
        qty=qty, leverage=5, stop_price=stop, r=6.0, risk_usd=150.0,
    ))
    return TrackedPosition(
        position_id=pid, symbol="BTC/USDT", side=side, entry=entry,
        initial_qty=qty, remaining_qty=qty, stop=stop, r=6.0, tp1=106.0, tp2=112.0,
        tp1_close_pct=0.5, tp2_close_pct=0.3, runner_trail_atr_mult=1.5,
    )


# --------------------------------------------------------------------------- #
# Simulated fills (paper/backtest)
# --------------------------------------------------------------------------- #
def test_sim_entry_applies_slippage_and_fee():
    db = Database(":memory:")
    ex = Executor(cfg("paper"), db)
    pos = make_position(db)
    entry, stop = asyncio.run(ex.open_position(pos, leverage=5))

    # default slippage 0.02% on a buy -> fills a touch above 100.
    assert entry.side == "buy"
    assert entry.price == pytest.approx(100.0 * 1.0002)
    assert entry.fee == pytest.approx(entry.price * 10 * 0.0004)
    assert db.get_order(entry.client_order_id)["status"] == "filled"

    # stop is a resting reduce-only intent (not filled yet)
    assert stop.resting is True
    srow = db.get_order(stop.client_order_id)
    assert srow["type"] == "stop_market" and srow["reduce_only"] == 1
    assert srow["status"] == "open"
    db.close()


def test_sim_short_entry_slips_down():
    db = Database(":memory:")
    ex = Executor(cfg("paper"), db)
    entry, _ = asyncio.run(ex.open_position(make_position(db, side=Side.short), leverage=5))
    assert entry.side == "sell"
    assert entry.price == pytest.approx(100.0 * 0.9998)
    db.close()


def test_execute_partial_close_is_reduce_only():
    db = Database(":memory:")
    ex = Executor(cfg("paper"), db)
    pos = make_position(db)
    asyncio.run(ex.open_position(pos, leverage=5))
    act = PositionAction("partial_close", "BTC/USDT", "tp1", qty=5.0, price=106.0)
    fill = asyncio.run(ex.execute_action(pos, act))
    assert fill.side == "sell" and fill.qty == 5.0
    assert db.get_order(fill.client_order_id)["reduce_only"] == 1
    db.close()


def test_move_stop_uses_incrementing_purpose():
    db = Database(":memory:")
    ex = Executor(cfg("paper"), db)
    pos = make_position(db)
    asyncio.run(ex.open_position(pos, leverage=5))         # places stop-0
    act = PositionAction("move_stop", "BTC/USDT", "tp1_move_be", price=100.0)
    fill = asyncio.run(ex.execute_action(pos, act))         # places stop-1
    assert fill.client_order_id.endswith("stop-1")
    stops = [o for o in db.orders_for_position(pos.position_id)
             if o["type"] == "stop_market"]
    assert {o["client_order_id"].split("-")[-1] for o in stops} == {"0", "1"}
    db.close()


# --------------------------------------------------------------------------- #
# Live: idempotency + retry against a fake exchange
# --------------------------------------------------------------------------- #
class FakeExchange:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.create_calls = []
        self.leverage_set = None
        self._id = 0

    async def set_leverage(self, leverage, symbol):
        self.leverage_set = (leverage, symbol)

    async def cancel_all_orders(self, symbol):
        return True

    async def create_order(self, symbol, type_, side, qty, price, params):
        self.create_calls.append((type_, side, qty, params))
        if len(self.create_calls) <= self.fail_times:
            raise ConnectionError("transient")
        self._id += 1
        return {"id": self._id, "average": 100.5, "fee": {"cost": 0.3}}


def test_live_entry_sends_order_and_sets_leverage():
    db = Database(":memory:")
    fake = FakeExchange()
    ex = Executor(cfg("live", reconnect_backoff_seconds=0.001), db, exchange=fake)
    entry, stop = asyncio.run(ex.open_position(make_position(db), leverage=7))
    assert fake.leverage_set == (7, "BTC/USDT")
    assert entry.price == 100.5 and entry.exchange_order_id == "1"
    # stop went out as a reduce-only trigger order (ccxt-unified, portable)
    stop_call = fake.create_calls[1]
    assert stop_call[0] == "market" and stop_call[3]["reduceOnly"] is True
    assert "stopLossPrice" in stop_call[3]
    db.close()


def test_live_retry_then_success():
    db = Database(":memory:")
    fake = FakeExchange(fail_times=2)  # first two create_order calls throw
    ex = Executor(cfg("live", reconnect_backoff_seconds=0.001, reconnect_max_retries=5),
                  db, exchange=fake)
    entry, _ = asyncio.run(ex.open_position(make_position(db), leverage=5))
    assert entry.exchange_order_id is not None       # eventually succeeded
    assert len(fake.create_calls) >= 3               # retried past the failures
    db.close()


def test_live_gives_up_after_max_retries():
    db = Database(":memory:")
    fake = FakeExchange(fail_times=99)
    ex = Executor(cfg("live", reconnect_backoff_seconds=0.001, reconnect_max_retries=2),
                  db, exchange=fake)
    with pytest.raises(ConnectionError):
        asyncio.run(ex.open_position(make_position(db), leverage=5))
    db.close()


def test_idempotent_replay_does_not_double_send():
    db = Database(":memory:")
    fake = FakeExchange()
    ex = Executor(cfg("live", reconnect_backoff_seconds=0.001), db, exchange=fake)
    pos = make_position(db)
    # First entry send.
    asyncio.run(ex._market(pos.position_id, "BTC/USDT", "buy", 10.0, 100.0,
                           "entry", reduce_only=False))
    calls_after_first = len(fake.create_calls)
    # Replay the exact same (position, purpose): must NOT hit the exchange again.
    fill2 = asyncio.run(ex._market(pos.position_id, "BTC/USDT", "buy", 10.0, 100.0,
                                   "entry", reduce_only=False))
    assert len(fake.create_calls) == calls_after_first   # no second send
    assert fill2.exchange_order_id == "1"                # returned cached fill
    db.close()
