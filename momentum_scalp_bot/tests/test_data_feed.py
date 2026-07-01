"""DataFeed tests — pure helpers + async pagination/stream logic against a
fake exchange (no network)."""

import asyncio

import pandas as pd
import pytest

from momentum_scalp.config import Config
from momentum_scalp.data_feed import (
    DataFeed,
    detect_gaps,
    ohlcv_to_df,
    timeframe_to_ms,
)

STEP = 300_000  # 5m in ms


def cfg() -> Config:
    return Config.model_validate({"symbols": ["BTC/USDT"]})


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_timeframe_to_ms():
    assert timeframe_to_ms("5m") == 300_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("1d") == 86_400_000
    with pytest.raises(ValueError):
        timeframe_to_ms("5x")


def test_ohlcv_to_df_sorted_deduped_utc():
    rows = [
        [STEP * 2, 3, 3, 3, 3, 30],
        [0, 1, 1, 1, 1, 10],
        [STEP, 2, 2, 2, 2, 20],
        [STEP, 9, 9, 9, 9, 99],  # duplicate ts -> last wins
    ]
    df = ohlcv_to_df(rows)
    assert list(df.index) == sorted(df.index)
    assert str(df.index.tz) == "UTC"
    assert len(df) == 3
    assert df.loc[df.index[1], "close"] == 9.0  # dedup kept the later row
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_ohlcv_to_df_empty():
    df = ohlcv_to_df([])
    assert df.empty and str(df.index.tz) == "UTC"


def test_detect_gaps_finds_missing_bar():
    rows = [[0, 1, 1, 1, 1, 1], [STEP, 1, 1, 1, 1, 1], [STEP * 3, 1, 1, 1, 1, 1]]
    gaps = detect_gaps(ohlcv_to_df(rows), "5m")
    assert len(gaps) == 1
    assert gaps[0] == pd.to_datetime(STEP * 2, unit="ms", utc=True)


def test_detect_gaps_none_when_contiguous():
    rows = [[i * STEP, 1, 1, 1, 1, 1] for i in range(5)]
    assert detect_gaps(ohlcv_to_df(rows), "5m") == []


# --------------------------------------------------------------------------- #
# Fake exchange for async tests
# --------------------------------------------------------------------------- #
class FakeHistExchange:
    """Serves a contiguous master series, at most `limit` rows per call."""

    def __init__(self, n_bars: int):
        self.master = [[i * STEP, i, i, i, i, i * 10] for i in range(n_bars)]
        self.calls = 0

    def milliseconds(self) -> int:
        return self.master[-1][0] + STEP  # "now" = just after last bar closes

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        self.calls += 1
        rows = [r for r in self.master if r[0] >= (since or 0)]
        return rows[:limit]


def test_fetch_ohlcv_history_paginates_and_dedupes():
    ex = FakeHistExchange(n_bars=25)
    feed = DataFeed(cfg(), exchange=ex)
    df = asyncio.run(feed.fetch_ohlcv_history("BTC/USDT", "5m", since_ms=0, limit=10))
    assert len(df) == 25            # all bars retrieved across pages
    assert ex.calls >= 3            # 25 bars / 10 per call -> multiple pages
    assert list(df.index) == sorted(df.index)
    assert not df.index.duplicated().any()


class FakeWSExchange:
    """Returns preset watch_ohlcv frames; 'now' is fixed so we can test that a
    still-forming candle is withheld."""

    def __init__(self, frames, now_ms):
        self.frames = list(frames)
        self._now = now_ms

    def milliseconds(self) -> int:
        return self._now

    async def watch_ohlcv(self, symbol, timeframe):
        if not self.frames:
            await asyncio.sleep(0)
            raise asyncio.CancelledError
        return self.frames.pop(0)


def test_stream_emits_only_closed_bars():
    # bars at t0,t1 closed; t2 still forming (now < t2 + step).
    t0, t1, t2 = 0, STEP, STEP * 2
    frame = [[t0, 1, 1, 1, 1, 10], [t1, 2, 2, 2, 2, 20], [t2, 3, 3, 3, 3, 30]]
    now = t2 + 1  # t2 not yet closed
    ex = FakeWSExchange([frame], now_ms=now)
    feed = DataFeed(cfg(), exchange=ex)

    async def collect_two():
        agen = feed.stream_closed_bars("BTC/USDT", "5m")
        bars = [await agen.__anext__(), await agen.__anext__()]
        await agen.aclose()
        return bars

    bars = asyncio.run(collect_two())
    assert [b["close"] for b in bars] == [1.0, 2.0]      # t2 withheld
    # both closed bars landed in the rolling buffer
    assert len(feed.buffer("BTC/USDT", "5m")) == 2


def test_seconds_since_last_message_infinite_before_first_frame():
    feed = DataFeed(cfg(), exchange=FakeHistExchange(1))
    assert feed.seconds_since_last_message() == float("inf")
