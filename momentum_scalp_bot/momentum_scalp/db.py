"""SQLite persistence — orders, positions, equity snapshots and an event log.

This is the durable record the rest of the bot is built on:

* **orders**   — every order we *intend* to place, keyed by a unique
  ``client_order_id``. The uniqueness constraint is the backbone of the
  Executor's idempotency (a retried send can't create a duplicate order).
* **positions** — the logical position lifecycle (open -> partial closes ->
  closed). ``list_open_positions`` lets the PositionTracker rebuild state
  after a restart.
* **equity**   — realized-equity snapshots + high-water mark for the RiskManager
  and reporting.
* **events**   — an audit trail (kill-switch, reconnects, Telegram alerts).

Stdlib ``sqlite3`` only; timestamps stored as ISO-8601 UTC strings (sortable).
Pass ``":memory:"`` for tests. All methods are synchronous and fast; the async
bot calls them inline.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(ts) -> str:
    if ts is None:
        return utc_now_iso()
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


# --------------------------------------------------------------------------- #
# Row dataclasses (typed insert payloads)
# --------------------------------------------------------------------------- #
@dataclass
class OrderRow:
    client_order_id: str          # idempotency key (unique)
    symbol: str
    side: str                     # "buy" | "sell"
    type: str                     # "market" | "stop_market" | "take_profit_market" | "limit"
    qty: float
    mode: str                     # backtest | paper | testnet | live
    purpose: str                  # entry | stop | tp1 | tp2 | runner_trail
    price: Optional[float] = None
    reduce_only: bool = False
    position_id: Optional[int] = None
    status: str = "new"           # new | open | filled | canceled | rejected
    exchange_order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_fill_price: Optional[float] = None
    ts: Optional[str] = None


@dataclass
class PositionRow:
    symbol: str
    side: str                     # "long" | "short"
    mode: str
    entry_ts: str
    entry_price: float
    qty: float
    leverage: int
    stop_price: float
    r: float
    risk_usd: float
    remaining_qty: Optional[float] = None
    status: str = "open"          # open | closed
    realized_pnl: float = 0.0
    fees: float = 0.0
    avg_exit_price: Optional[float] = None
    exit_ts: Optional[str] = None
    exit_reason: Optional[str] = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT    NOT NULL,
    side          TEXT    NOT NULL,
    mode          TEXT    NOT NULL,
    entry_ts      TEXT    NOT NULL,
    entry_price   REAL    NOT NULL,
    qty           REAL    NOT NULL,
    remaining_qty REAL    NOT NULL,
    leverage      INTEGER NOT NULL,
    stop_price    REAL    NOT NULL,
    r             REAL    NOT NULL,
    risk_usd      REAL    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'open',
    realized_pnl  REAL    NOT NULL DEFAULT 0,
    fees          REAL    NOT NULL DEFAULT 0,
    avg_exit_price REAL,
    exit_ts       TEXT,
    exit_reason   TEXT
);
CREATE INDEX IF NOT EXISTS ix_positions_status ON positions(status, mode);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT    NOT NULL UNIQUE,
    position_id     INTEGER REFERENCES positions(id),
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    type            TEXT    NOT NULL,
    purpose         TEXT    NOT NULL,
    qty             REAL    NOT NULL,
    price           REAL,
    reduce_only     INTEGER NOT NULL DEFAULT 0,
    mode            TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'new',
    exchange_order_id TEXT,
    filled_qty      REAL    NOT NULL DEFAULT 0,
    avg_fill_price  REAL,
    ts              TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_position ON orders(position_id);

CREATE TABLE IF NOT EXISTS equity (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT NOT NULL,
    mode  TEXT NOT NULL,
    equity REAL NOT NULL,
    high_water_mark REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_equity_ts ON equity(ts);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    level   TEXT NOT NULL,
    kind    TEXT NOT NULL,
    symbol  TEXT,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


class Database:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the async bot may touch it from a worker.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    # ------------------------------------------------------------------ #
    # Orders (idempotent)
    # ------------------------------------------------------------------ #
    def record_order(self, order: OrderRow) -> int:
        """Insert an order. Idempotent on ``client_order_id``: a duplicate is
        ignored and the existing row's id is returned."""
        order.ts = _iso(order.ts)
        existing = self.get_order(order.client_order_id)
        if existing is not None:
            return existing["id"]
        with self._tx() as cur:
            cur.execute(
                """INSERT OR IGNORE INTO orders
                   (client_order_id, position_id, symbol, side, type, purpose,
                    qty, price, reduce_only, mode, status, exchange_order_id,
                    filled_qty, avg_fill_price, ts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order.client_order_id, order.position_id, order.symbol,
                    order.side, order.type, order.purpose, order.qty, order.price,
                    int(order.reduce_only), order.mode, order.status,
                    order.exchange_order_id, order.filled_qty,
                    order.avg_fill_price, order.ts,
                ),
            )
        return self.get_order(order.client_order_id)["id"]

    def get_order(self, client_order_id: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
        )
        return cur.fetchone()

    def update_order(
        self,
        client_order_id: str,
        *,
        status: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
        filled_qty: Optional[float] = None,
        avg_fill_price: Optional[float] = None,
    ) -> None:
        sets, vals = [], []
        for col, val in (
            ("status", status),
            ("exchange_order_id", exchange_order_id),
            ("filled_qty", filled_qty),
            ("avg_fill_price", avg_fill_price),
        ):
            if val is not None:
                sets.append(f"{col} = ?")
                vals.append(val)
        if not sets:
            return
        vals.append(client_order_id)
        with self._tx() as cur:
            cur.execute(
                f"UPDATE orders SET {', '.join(sets)} WHERE client_order_id = ?",
                vals,
            )

    def orders_for_position(self, position_id: int) -> List[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM orders WHERE position_id = ? ORDER BY id", (position_id,)
            )
        )

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    def open_position(self, pos: PositionRow) -> int:
        pos.entry_ts = _iso(pos.entry_ts)
        if pos.remaining_qty is None:
            pos.remaining_qty = pos.qty
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO positions
                   (symbol, side, mode, entry_ts, entry_price, qty, remaining_qty,
                    leverage, stop_price, r, risk_usd, status, realized_pnl, fees)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    pos.symbol, pos.side, pos.mode, pos.entry_ts, pos.entry_price,
                    pos.qty, pos.remaining_qty, pos.leverage, pos.stop_price,
                    pos.r, pos.risk_usd, pos.status, pos.realized_pnl, pos.fees,
                ),
            )
            return cur.lastrowid

    def update_position(self, position_id: int, **fields) -> None:
        allowed = {
            "remaining_qty", "stop_price", "status", "realized_pnl", "fees",
            "avg_exit_price", "exit_ts", "exit_reason",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update position columns: {sorted(bad)}")
        if not fields:
            return
        if "exit_ts" in fields and fields["exit_ts"] is not None:
            fields["exit_ts"] = _iso(fields["exit_ts"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._tx() as cur:
            cur.execute(
                f"UPDATE positions SET {sets} WHERE id = ?",
                (*fields.values(), position_id),
            )

    def close_position(
        self,
        position_id: int,
        *,
        realized_pnl: float,
        avg_exit_price: float,
        exit_reason: str,
        fees: float = 0.0,
        exit_ts=None,
    ) -> None:
        self.update_position(
            position_id,
            status="closed",
            remaining_qty=0.0,
            realized_pnl=realized_pnl,
            avg_exit_price=avg_exit_price,
            exit_reason=exit_reason,
            fees=fees,
            exit_ts=_iso(exit_ts),
        )

    def get_position(self, position_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()

    def list_open_positions(self, mode: Optional[str] = None) -> List[sqlite3.Row]:
        """Open positions — used by the PositionTracker to reconcile on start."""
        if mode is None:
            q = "SELECT * FROM positions WHERE status = 'open' ORDER BY id"
            return list(self.conn.execute(q))
        return list(
            self.conn.execute(
                "SELECT * FROM positions WHERE status = 'open' AND mode = ? ORDER BY id",
                (mode,),
            )
        )

    def list_closed_trades(
        self, mode: Optional[str] = None, limit: int = 500
    ) -> List[sqlite3.Row]:
        if mode is None:
            return list(
                self.conn.execute(
                    "SELECT * FROM positions WHERE status='closed' "
                    "ORDER BY exit_ts DESC LIMIT ?",
                    (limit,),
                )
            )
        return list(
            self.conn.execute(
                "SELECT * FROM positions WHERE status='closed' AND mode=? "
                "ORDER BY exit_ts DESC LIMIT ?",
                (mode, limit),
            )
        )

    # ------------------------------------------------------------------ #
    # Equity + events
    # ------------------------------------------------------------------ #
    def record_equity(
        self, equity: float, high_water_mark: float, mode: str, ts=None
    ) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO equity (ts, mode, equity, high_water_mark) VALUES (?,?,?,?)",
                (_iso(ts), mode, equity, high_water_mark),
            )

    def last_equity(self, mode: Optional[str] = None) -> Optional[sqlite3.Row]:
        if mode is None:
            return self.conn.execute(
                "SELECT * FROM equity ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return self.conn.execute(
            "SELECT * FROM equity WHERE mode = ? ORDER BY id DESC LIMIT 1", (mode,)
        ).fetchone()

    def log_event(
        self, level: str, kind: str, message: str, symbol: Optional[str] = None, ts=None
    ) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO events (ts, level, kind, symbol, message) VALUES (?,?,?,?,?)",
                (_iso(ts), level, kind, symbol, message),
            )

    def recent_events(self, limit: int = 100) -> List[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            )
        )
