"""Fetch public OHLCV data from exchanges via ccxt (no API key required)."""
from __future__ import annotations

import pandas as pd


def fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 1000,
) -> pd.DataFrame:
    try:
        import ccxt
    except ImportError:
        raise ImportError("ccxt not installed. Run: pip install ccxt")

    exchange_cls = getattr(ccxt, exchange_id.lower(), None)
    if exchange_cls is None:
        raise ValueError(f"Unknown exchange: '{exchange_id}'. Check ccxt docs.")

    exchange = exchange_cls({"enableRateLimit": True})

    since = None
    if start:
        since = int(pd.Timestamp(start).timestamp() * 1000)

    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    df = pd.DataFrame(
        ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms").dt.tz_localize(None)
    df = df.set_index("timestamp").sort_index().astype(float)

    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]

    return df
