"""Executor — turns decisions into orders.

Two fill models behind one interface:

* **simulated** (backtest, paper): fills immediately at a reference price with
  configured slippage + taker fee. No network, fully deterministic. The
  protective stop is recorded as a resting intent; the PositionTracker decides
  when it fills by crossing price.
* **live** (testnet, live): sends real orders through a ccxt exchange with

    - **idempotency** — a deterministic ``client_order_id`` per
      (position, purpose) recorded in SQLite *before* the send. record_order is
      an idempotent insert, and an already-``filled`` order is returned from the
      DB instead of being re-sent, so a crash-replay or retry can never create a
      duplicate; and
    - **retry** — transient network/exchange errors are retried with
      exponential backoff.

Entry is a market order; the protective stop is a **reduce-only STOP_MARKET**
placed immediately after. Ladder exits (TP1/TP2/runner, stop moves) arrive as
:class:`PositionAction`\\ s from the PositionTracker and are executed here.
Replaced stops get an incrementing purpose suffix so cancel+replace never
collides with a prior id.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .db import Database, OrderRow
from .position_tracker import PositionAction, TrackedPosition
from .signal_engine import Side

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    side: str        # "buy" | "sell"
    qty: float
    price: float
    fee: float
    purpose: str
    exchange_order_id: Optional[str] = None
    resting: bool = False   # True for a placed-but-unfilled stop


class Executor:
    def __init__(self, config: Config, db: Database, exchange=None):
        self.cfg = config
        self.db = db
        self.exchange = exchange
        self.mode = config.mode
        self._live = config.is_live_orders  # testnet or live -> real orders
        bt = config.backtest
        self._fee_rate = bt.fee_rate
        self._slippage = bt.slippage_pct

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def open_position(
        self, position: TrackedPosition, leverage: int
    ) -> tuple[Fill, Fill]:
        """Enter (market) then immediately place the reduce-only stop.

        In live/testnet the TP1/TP2 take-profits are also placed as resting
        reduce-only orders so exits happen on the exchange (fills arrive via
        watch_orders). In sim mode TPs are handled by the price-driven ladder,
        so they are not pre-placed here."""
        entry_side = "buy" if position.side is Side.long else "sell"
        exit_side = "sell" if position.side is Side.long else "buy"
        pid, sym, qty = position.position_id, position.symbol, position.initial_qty

        if self._live:
            await self._set_leverage(sym, leverage)

        entry = await self._market(
            pid, sym, entry_side, qty, position.entry, "entry", reduce_only=False
        )
        stop = await self._stop_market(
            pid, sym, exit_side, qty, position.stop, self._stop_purpose(pid),
        )
        if self._live:
            await self._take_profit_market(
                pid, sym, exit_side, position.tp1_close_pct * qty, position.tp1, "tp1"
            )
            await self._take_profit_market(
                pid, sym, exit_side, position.tp2_close_pct * qty, position.tp2, "tp2"
            )
        return entry, stop

    async def execute_action(
        self, position: TrackedPosition, action: PositionAction
    ) -> Optional[Fill]:
        """Carry out one ladder action."""
        exit_side = "sell" if position.side is Side.long else "buy"
        pid, sym = position.position_id, position.symbol

        if action.kind == "move_stop":
            return await self._stop_market(
                pid, sym, exit_side, position.remaining_qty, action.price,
                self._stop_purpose(pid), replace=True,
            )
        if action.kind in ("partial_close", "close"):
            return await self._market(
                pid, sym, exit_side, action.qty, action.price,
                action.reason, reduce_only=True,
            )
        raise ValueError(f"unknown action kind: {action.kind}")

    # ------------------------------------------------------------------ #
    # Order primitives
    # ------------------------------------------------------------------ #
    async def _market(
        self, pid: int, symbol: str, side: str, qty: float, ref_price: float,
        purpose: str, *, reduce_only: bool,
    ) -> Fill:
        cid = self._client_id(pid, purpose)

        # Replay safety: an already-filled order is never re-sent.
        existing = self.db.get_order(cid)
        if existing is not None and existing["status"] == "filled":
            return _fill_from_row(existing)

        self.db.record_order(OrderRow(
            client_order_id=cid, position_id=pid, symbol=symbol, side=side,
            type="market", qty=qty, mode=self.mode.value, purpose=purpose,
            reduce_only=reduce_only,
        ))

        if not self._live:
            price = self._slip(ref_price, side)
            fee = abs(price * qty) * self._fee_rate
            self.db.update_order(cid, status="filled", filled_qty=qty, avg_fill_price=price)
            return Fill(cid, symbol, side, qty, price, fee, purpose)

        params = {"clientOrderId": cid}
        if reduce_only:
            params["reduceOnly"] = True
        order = await self._send(lambda: self.exchange.create_order(
            symbol, "market", side, qty, None, params))
        price = float(order.get("average") or order.get("price") or ref_price)
        fee = _fee_of(order)
        self.db.update_order(cid, status="filled", exchange_order_id=str(order.get("id")),
                             filled_qty=qty, avg_fill_price=price)
        return Fill(cid, symbol, side, qty, price, fee, purpose, str(order.get("id")))

    async def _stop_market(
        self, pid: int, symbol: str, side: str, qty: float, stop_price: float,
        purpose: str, *, replace: bool = False,
    ) -> Fill:
        cid = self._client_id(pid, purpose)
        self.db.record_order(OrderRow(
            client_order_id=cid, position_id=pid, symbol=symbol, side=side,
            type="stop_market", qty=qty, price=stop_price, mode=self.mode.value,
            purpose=purpose, reduce_only=True,
        ))

        if not self._live:
            self.db.update_order(cid, status="open")
            return Fill(cid, symbol, side, qty, stop_price, 0.0, purpose, resting=True)

        if replace:
            await self._cancel_open_stops(symbol)
        # ccxt-unified trigger params -> portable across binanceusdm & bybit.
        params = {
            "clientOrderId": cid,
            "reduceOnly": True,
            "stopLossPrice": stop_price,
        }
        order = await self._send(lambda: self.exchange.create_order(
            symbol, "market", side, qty, None, params))
        self.db.update_order(cid, status="open", exchange_order_id=str(order.get("id")))
        return Fill(cid, symbol, side, qty, stop_price, 0.0, purpose,
                    str(order.get("id")), resting=True)

    async def _take_profit_market(
        self, pid: int, symbol: str, side: str, qty: float, tp_price: float,
        purpose: str,
    ) -> Fill:
        """Resting reduce-only TAKE_PROFIT_MARKET (live only; sim uses the
        price-driven ladder)."""
        cid = self._client_id(pid, purpose)
        self.db.record_order(OrderRow(
            client_order_id=cid, position_id=pid, symbol=symbol, side=side,
            type="take_profit_market", qty=qty, price=tp_price, mode=self.mode.value,
            purpose=purpose, reduce_only=True,
        ))
        if not self._live:
            self.db.update_order(cid, status="open")
            return Fill(cid, symbol, side, qty, tp_price, 0.0, purpose, resting=True)

        params = {
            "clientOrderId": cid,
            "reduceOnly": True,
            "takeProfitPrice": tp_price,
        }
        order = await self._send(lambda: self.exchange.create_order(
            symbol, "market", side, qty, None, params))
        self.db.update_order(cid, status="open", exchange_order_id=str(order.get("id")))
        return Fill(cid, symbol, side, qty, tp_price, 0.0, purpose,
                    str(order.get("id")), resting=True)

    async def cancel_symbol_orders(self, symbol: str) -> None:
        """Cancel all resting orders for a symbol (cleanup after a close)."""
        if self._live:
            await self._cancel_open_stops(symbol)

    # ------------------------------------------------------------------ #
    # Live helpers
    # ------------------------------------------------------------------ #
    async def _set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            await self._send(lambda: self.exchange.set_leverage(leverage, symbol))
        except Exception as exc:  # noqa: BLE001 - non-fatal; log and continue
            log.warning("set_leverage(%s, %s) failed: %s", leverage, symbol, exc)

    async def _cancel_open_stops(self, symbol: str) -> None:
        try:
            await self._send(lambda: self.exchange.cancel_all_orders(symbol))
        except Exception as exc:  # noqa: BLE001
            log.warning("cancel_all_orders(%s) failed: %s", symbol, exc)

    async def _send(self, factory):
        """Call an exchange coroutine with exponential-backoff retry."""
        wcfg = self.cfg.watchdog
        attempt = 0
        while True:
            try:
                return await factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry any transient failure
                attempt += 1
                if attempt > wcfg.reconnect_max_retries:
                    log.error("order send failed after %d attempts: %s", attempt - 1, exc)
                    raise
                backoff = min(wcfg.reconnect_backoff_seconds * (2 ** (attempt - 1)), 30.0)
                log.warning("order send error (%s); retry #%d in %.1fs", exc, attempt, backoff)
                await asyncio.sleep(backoff)

    # ------------------------------------------------------------------ #
    # Utils
    # ------------------------------------------------------------------ #
    def _client_id(self, pid: int, purpose: str) -> str:
        """Deterministic idempotency key: one id per (position, purpose)."""
        return f"msb-{self.mode.value}-{pid}-{purpose}"

    def _stop_purpose(self, pid: int) -> str:
        """A fresh stop label each time the stop is (re)placed, so replaced
        stops get distinct client ids while the initial stop is 'stop-0'."""
        n = 0
        while self.db.get_order(self._client_id(pid, f"stop-{n}")) is not None:
            n += 1
        return f"stop-{n}"

    def _slip(self, price: float, side: str) -> float:
        # Buys fill a touch higher, sells a touch lower.
        return price * (1 + self._slippage) if side == "buy" else price * (1 - self._slippage)


def _fill_from_row(row) -> Fill:
    return Fill(
        client_order_id=row["client_order_id"],
        symbol=row["symbol"],
        side=row["side"],
        qty=row["filled_qty"],
        price=row["avg_fill_price"] or 0.0,
        fee=0.0,
        purpose=row["purpose"],
        exchange_order_id=row["exchange_order_id"],
    )


def parse_client_id(cid: str) -> Optional[tuple[int, str]]:
    """Decode 'msb-{mode}-{pid}-{purpose}' -> (position_id, purpose).

    Returns None for ids that aren't ours. ``purpose`` may itself contain a
    dash (e.g. 'stop-1')."""
    if not cid or not cid.startswith("msb-"):
        return None
    parts = cid.split("-")
    if len(parts) < 4:
        return None
    try:
        pid = int(parts[2])
    except ValueError:
        return None
    return pid, "-".join(parts[3:])


def fill_kind(purpose: str) -> Optional[str]:
    """Map an order purpose to a ladder event kind for TrackedPosition.on_fill."""
    if purpose.startswith("tp1"):
        return "tp1"
    if purpose.startswith("tp2"):
        return "tp2"
    if purpose.startswith("stop"):
        return "stop"
    return None  # 'entry' and anything else are not ladder-advancing fills


def _fee_of(order: dict) -> float:
    fee = order.get("fee") or {}
    try:
        return float(fee.get("cost") or 0.0)
    except (TypeError, ValueError):
        return 0.0
