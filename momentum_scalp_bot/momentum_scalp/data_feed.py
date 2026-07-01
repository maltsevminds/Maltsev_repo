"""DataFeed — market data via ccxt / ccxt.pro.

One class covers all four modes:

* **backtest**  -> :meth:`fetch_ohlcv_history` (REST, paginated download).
* **paper / testnet / live** -> :meth:`stream_closed_bars` (WebSocket
  ``watch_ohlcv``) with automatic reconnect + exponential backoff, plus
  :meth:`fetch_ohlcv_history` to warm up indicator buffers on start.

Design notes
------------
* A single ``ccxt.pro`` exchange instance serves both REST and WS (pro
  exchanges inherit the REST methods), so there is one connection to manage.
* The stream only ever emits **closed** bars. ``watch_ohlcv`` returns the
  still-forming candle as its last element; we withhold it until a newer
  timestamp proves it closed.
* ``last_message_ts`` is updated on every WS frame so the Watchdog can trip
  the kill-switch when the feed goes silent (> ws_timeout_seconds).
* The exchange is injectable (``exchange=``) so the pure logic is testable
  without a network.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Dict, List, Optional

import pandas as pd

from .config import Config, Mode
from .indicators import OHLCV_COLS

log = logging.getLogger(__name__)

# ccxt.pro is optional at import time so unit tests (and backtests on machines
# without it) don't hard-fail; it's only required to actually open a stream.
try:  # pragma: no cover - trivial import guard
    import ccxt.pro as ccxtpro
except Exception:  # noqa: BLE001
    ccxtpro = None


# --------------------------------------------------------------------------- #
# Pure helpers (fully unit-tested)
# --------------------------------------------------------------------------- #
def timeframe_to_ms(timeframe: str) -> int:
    """'5m' -> 300000, '1h' -> 3600000. Uses ccxt's parser when available."""
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    unit = timeframe[-1]
    if unit not in units:
        raise ValueError(f"unsupported timeframe: {timeframe!r}")
    return int(timeframe[:-1]) * units[unit] * 1000


def ohlcv_to_df(rows: List[list]) -> pd.DataFrame:
    """Convert ccxt OHLCV rows ([ts_ms, o, h, l, c, v]) to the standard frame.

    Sorted ascending, UTC-indexed, duplicate timestamps dropped (last wins).
    """
    if not rows:
        return pd.DataFrame(
            columns=list(OHLCV_COLS),
            index=pd.DatetimeIndex([], tz="UTC", name="ts"),
        )
    df = pd.DataFrame(rows, columns=["ts", *OHLCV_COLS])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.astype(float)


def detect_gaps(df: pd.DataFrame, timeframe: str) -> List[pd.Timestamp]:
    """Return the timestamps of bars that are missing from an otherwise
    contiguous series (helps flag WS drops / exchange holes)."""
    if len(df) < 2:
        return []
    step = pd.Timedelta(milliseconds=timeframe_to_ms(timeframe))
    expected = pd.date_range(df.index[0], df.index[-1], freq=step)
    return list(expected.difference(df.index))


# --------------------------------------------------------------------------- #
# Exchange factory
# --------------------------------------------------------------------------- #
def build_exchange(config: Config):
    """Instantiate a configured ccxt.pro exchange for the active mode."""
    if ccxtpro is None:  # pragma: no cover
        raise RuntimeError(
            "ccxt.pro is not installed; run `pip install -r requirements.txt`"
        )
    if not hasattr(ccxtpro, config.exchange.id):
        raise ValueError(f"unknown ccxt.pro exchange id: {config.exchange.id!r}")

    options = {"defaultType": config.exchange.default_type}
    options.update(config.exchange.options)  # per-exchange extras (e.g. bybit)
    params: dict = {
        "enableRateLimit": True,
        "options": options,
        "newUpdates": True,  # watch_* returns only fresh deltas
    }
    if config.mode in (Mode.testnet, Mode.live):
        key, secret = config.resolve_credentials()
        params["apiKey"] = key
        params["secret"] = secret

    exchange = getattr(ccxtpro, config.exchange.id)(params)
    if config.mode is Mode.testnet:
        exchange.set_sandbox_mode(True)
    return exchange


# --------------------------------------------------------------------------- #
# DataFeed
# --------------------------------------------------------------------------- #
class DataFeed:
    def __init__(self, config: Config, exchange=None, buffer_size: int = 1500):
        self.cfg = config
        self.exchange = exchange  # lazily built on first use if None
        self.buffer_size = buffer_size
        # (symbol, timeframe) -> DataFrame of recent closed bars
        self._buffers: Dict[tuple, pd.DataFrame] = {}
        self.last_message_ts: float = 0.0

    # -- lifecycle ----------------------------------------------------- #
    def _ensure_exchange(self):
        if self.exchange is None:
            self.exchange = build_exchange(self.cfg)
        return self.exchange

    async def close(self) -> None:
        if self.exchange is not None and hasattr(self.exchange, "close"):
            await self.exchange.close()

    def seconds_since_last_message(self) -> float:
        """Feed silence in seconds. ``inf`` until the first frame arrives."""
        if self.last_message_ts == 0.0:
            return float("inf")
        return time.monotonic() - self.last_message_ts

    def buffer(self, symbol: str, timeframe: str) -> pd.DataFrame:
        return self._buffers.get((symbol, timeframe), ohlcv_to_df([]))

    # -- historical (REST, paginated) ---------------------------------- #
    async def fetch_ohlcv_history(
        self,
        symbol: str,
        timeframe: str,
        since_ms: int,
        until_ms: Optional[int] = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Download closed OHLCV from ``since_ms`` up to ``until_ms`` (exclusive
        of the future), paginating past the exchange's per-call limit."""
        ex = self._ensure_exchange()
        step = timeframe_to_ms(timeframe)
        until_ms = until_ms if until_ms is not None else ex.milliseconds()
        cursor = since_ms
        collected: List[list] = []

        while cursor < until_ms:
            batch = await ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
            if not batch:
                break
            collected.extend(batch)
            last_ts = batch[-1][0]
            next_cursor = last_ts + step
            if next_cursor <= cursor:  # no forward progress -> stop
                break
            cursor = next_cursor
            if len(batch) < limit:  # exchange had nothing more
                break

        df = ohlcv_to_df(collected)
        return df[df.index < pd.to_datetime(until_ms, unit="ms", utc=True)]

    async def warm_up(self, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        """Preload the buffer with the last ``bars`` closed candles."""
        ex = self._ensure_exchange()
        step = timeframe_to_ms(timeframe)
        since = ex.milliseconds() - (bars + 2) * step
        df = await self.fetch_ohlcv_history(symbol, timeframe, since)
        df = df.tail(bars)
        self._buffers[(symbol, timeframe)] = df
        return df

    # -- live stream (WS, reconnecting) -------------------------------- #
    async def stream_closed_bars(
        self, symbol: str, timeframe: str
    ) -> AsyncIterator[pd.Series]:
        """Yield each newly *closed* bar (as a pandas Series) forever.

        Reconnects with exponential backoff on network/exchange errors. Each
        emitted bar is also appended to the rolling buffer for that stream.
        """
        ex = self._ensure_exchange()
        step_ms = timeframe_to_ms(timeframe)
        last_closed_ms: Optional[int] = None
        retries = 0
        wcfg = self.cfg.watchdog

        while True:
            try:
                candles = await ex.watch_ohlcv(symbol, timeframe)
                self.last_message_ts = time.monotonic()
                retries = 0  # a good frame resets the backoff

                # Every candle strictly older than the last row is closed.
                for row in candles:
                    ts_ms = row[0]
                    if ts_ms + step_ms > _now_ms(ex):
                        continue  # still forming
                    if last_closed_ms is not None and ts_ms <= last_closed_ms:
                        continue  # already emitted
                    last_closed_ms = ts_ms
                    bar = self._append_to_buffer(symbol, timeframe, row)
                    yield bar

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything transient
                retries += 1
                if retries > wcfg.reconnect_max_retries:
                    log.error("WS %s %s: giving up after %d retries: %s",
                              symbol, timeframe, retries - 1, exc)
                    raise
                backoff = min(
                    wcfg.reconnect_backoff_seconds * (2 ** (retries - 1)), 60.0
                )
                log.warning("WS %s %s error (%s); reconnect #%d in %.1fs",
                            symbol, timeframe, exc, retries, backoff)
                await asyncio.sleep(backoff)

    def _append_to_buffer(self, symbol: str, timeframe: str, row: list) -> pd.Series:
        df = self._buffers.get((symbol, timeframe))
        new = ohlcv_to_df([row])
        if df is None or df.empty:
            df = new
        else:
            df = pd.concat([df, new])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        if len(df) > self.buffer_size:
            df = df.iloc[-self.buffer_size :]
        self._buffers[(symbol, timeframe)] = df
        return df.iloc[-1]

    # -- order updates (WS, reconnecting) ------------------------------ #
    async def stream_orders(self):
        """Yield exchange order-update dicts (fills, cancels) forever, with the
        same exponential-backoff reconnect as the OHLCV stream. Used in
        testnet/live so ladder exits are driven by real fills."""
        ex = self._ensure_exchange()
        retries = 0
        wcfg = self.cfg.watchdog
        while True:
            try:
                orders = await ex.watch_orders()
                self.last_message_ts = time.monotonic()
                retries = 0
                for order in orders:
                    yield order
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything transient
                retries += 1
                if retries > wcfg.reconnect_max_retries:
                    log.error("watch_orders: giving up after %d retries: %s",
                              retries - 1, exc)
                    raise
                backoff = min(
                    wcfg.reconnect_backoff_seconds * (2 ** (retries - 1)), 60.0
                )
                log.warning("watch_orders error (%s); reconnect #%d in %.1fs",
                            exc, retries, backoff)
                await asyncio.sleep(backoff)

    # -- funding ------------------------------------------------------- #
    async def fetch_funding_rate(self, symbol: str) -> float:
        """Current funding rate as a fraction (0.0001 == 0.01%)."""
        ex = self._ensure_exchange()
        info = await ex.fetch_funding_rate(symbol)
        rate = info.get("fundingRate")
        return float(rate) if rate is not None else 0.0


def _now_ms(exchange) -> int:
    try:
        return exchange.milliseconds()
    except Exception:  # noqa: BLE001 - fallback for injected fakes
        return int(time.time() * 1000)
