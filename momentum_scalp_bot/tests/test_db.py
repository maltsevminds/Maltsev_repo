"""Database tests — schema, order idempotency, position lifecycle, equity/events."""

from datetime import datetime, timezone

import pytest

from momentum_scalp.db import Database, OrderRow, PositionRow

UTC = timezone.utc


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def _order(cid="c1", **kw):
    base = dict(
        client_order_id=cid, symbol="BTC/USDT", side="buy", type="market",
        qty=1.0, mode="paper", purpose="entry",
    )
    base.update(kw)
    return OrderRow(**base)


def _position(**kw):
    base = dict(
        symbol="BTC/USDT", side="long", mode="paper",
        entry_ts=datetime(2024, 1, 1, tzinfo=UTC), entry_price=100.0, qty=2.0,
        leverage=5, stop_price=98.0, r=2.0, risk_usd=150.0,
    )
    base.update(kw)
    return PositionRow(**base)


def test_schema_created_and_idempotent(db):
    db.init_schema()  # second call must not error
    assert db.list_open_positions() == []


def test_order_idempotency(db):
    id1 = db.record_order(_order("dup"))
    id2 = db.record_order(_order("dup", qty=999.0))  # same client id
    assert id1 == id2
    rows = list(db.conn.execute("SELECT * FROM orders"))
    assert len(rows) == 1
    assert rows[0]["qty"] == 1.0  # original preserved, not overwritten


def test_update_order_status_and_fill(db):
    db.record_order(_order("o1"))
    db.update_order("o1", status="filled", exchange_order_id="X123",
                    filled_qty=1.0, avg_fill_price=101.5)
    row = db.get_order("o1")
    assert row["status"] == "filled"
    assert row["exchange_order_id"] == "X123"
    assert row["avg_fill_price"] == 101.5


def test_position_open_defaults_remaining_qty(db):
    pid = db.open_position(_position(qty=3.0))
    pos = db.get_position(pid)
    assert pos["remaining_qty"] == 3.0
    assert pos["status"] == "open"


def test_position_partial_then_close(db):
    pid = db.open_position(_position(qty=2.0))
    db.update_position(pid, remaining_qty=1.0, stop_price=100.0, realized_pnl=50.0)
    pos = db.get_position(pid)
    assert pos["remaining_qty"] == 1.0 and pos["stop_price"] == 100.0

    db.close_position(pid, realized_pnl=120.0, avg_exit_price=112.0,
                      exit_reason="tp2+runner", fees=1.2,
                      exit_ts=datetime(2024, 1, 1, 1, tzinfo=UTC))
    pos = db.get_position(pid)
    assert pos["status"] == "closed"
    assert pos["remaining_qty"] == 0.0
    assert pos["realized_pnl"] == 120.0
    assert pos["exit_reason"] == "tp2+runner"


def test_update_position_rejects_unknown_column(db):
    pid = db.open_position(_position())
    with pytest.raises(ValueError, match="cannot update"):
        db.update_position(pid, nonsense=1)


def test_list_open_positions_for_reconcile(db):
    p1 = db.open_position(_position(symbol="BTC/USDT"))
    p2 = db.open_position(_position(symbol="ETH/USDT"))
    db.open_position(_position(symbol="SOL/USDT", mode="testnet"))
    db.close_position(p2, realized_pnl=10.0, avg_exit_price=101.0, exit_reason="tp1")

    open_paper = db.list_open_positions("paper")
    assert {p["symbol"] for p in open_paper} == {"BTC/USDT"}
    assert len(db.list_open_positions()) == 2  # BTC(paper) + SOL(testnet)


def test_orders_linked_to_position(db):
    pid = db.open_position(_position())
    db.record_order(_order("entry", position_id=pid, purpose="entry"))
    db.record_order(_order("stop", position_id=pid, purpose="stop",
                           type="stop_market", side="sell", reduce_only=True))
    linked = db.orders_for_position(pid)
    assert {o["purpose"] for o in linked} == {"entry", "stop"}
    assert linked[1]["reduce_only"] == 1


def test_equity_snapshots_and_last(db):
    db.record_equity(10_000, 10_000, "paper", ts=datetime(2024, 1, 1, tzinfo=UTC))
    db.record_equity(10_250, 10_250, "paper", ts=datetime(2024, 1, 2, tzinfo=UTC))
    last = db.last_equity("paper")
    assert last["equity"] == 10_250
    assert last["high_water_mark"] == 10_250


def test_event_log(db):
    db.log_event("WARNING", "kill_switch", "feed silent 31s", symbol="BTC/USDT")
    ev = db.recent_events()
    assert len(ev) == 1
    assert ev[0]["kind"] == "kill_switch"
    assert ev[0]["symbol"] == "BTC/USDT"


def test_closed_trades_query(db):
    p = db.open_position(_position())
    db.close_position(p, realized_pnl=75.0, avg_exit_price=110.0, exit_reason="tp2",
                      exit_ts=datetime(2024, 1, 3, tzinfo=UTC))
    closed = db.list_closed_trades("paper")
    assert len(closed) == 1 and closed[0]["realized_pnl"] == 75.0
