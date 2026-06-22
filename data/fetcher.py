"""Fetch public (and private) OHLCV data from exchanges via ccxt."""
from __future__ import annotations

import pandas as pd

_TF_MS: dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


def fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    start: str | None = None,
    end: str | None = None,
    limit: int = 1000,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> pd.DataFrame:
    try:
        import ccxt
    except ImportError:
        raise ImportError("ccxt not installed. Run: pip install ccxt")

    exchange_cls = getattr(ccxt, exchange_id.lower(), None)
    if exchange_cls is None:
        raise ValueError(f"Unknown exchange: '{exchange_id}'. Check ccxt docs.")

    config: dict = {"enableRateLimit": True}
    if api_key:
        config["apiKey"] = api_key
    if api_secret:
        config["secret"] = api_secret

    exchange = exchange_cls(config)

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


def fetch_ohlcv_all(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    api_key: str | None = None,
    api_secret: str | None = None,
    chunk_size: int = 1000,
    on_progress=None,
) -> pd.DataFrame:
    """Fetch ALL bars in [start, end] using paginated requests.

    on_progress: optional callable(fetched_bars: int, total_bars: int).
    """
    try:
        import ccxt
    except ImportError:
        raise ImportError("ccxt not installed. Run: pip install ccxt")

    exchange_cls = getattr(ccxt, exchange_id.lower(), None)
    if exchange_cls is None:
        raise ValueError(f"Unknown exchange: '{exchange_id}'. Check ccxt docs.")

    config: dict = {"enableRateLimit": True}
    if api_key:
        config["apiKey"] = api_key
    if api_secret:
        config["secret"] = api_secret

    exchange = exchange_cls(config)

    tf_ms = _TF_MS.get(timeframe)
    if tf_ms is None:
        raise ValueError(f"Unknown timeframe: '{timeframe}'. Supported: {list(_TF_MS)}")

    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    total_bars = max(1, (end_ms - start_ms) // tf_ms + 1)

    all_chunks: list[pd.DataFrame] = []
    since = start_ms
    fetched = 0

    while since <= end_ms:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=chunk_size)
        if not ohlcv:
            break

        chunk = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        chunk["timestamp"] = pd.to_datetime(chunk["timestamp"], unit="ms").dt.tz_localize(None)
        chunk = chunk.set_index("timestamp").sort_index().astype(float)

        # Only keep bars within the requested range
        chunk = chunk[chunk.index <= pd.Timestamp(end)]
        if chunk.empty:
            break

        all_chunks.append(chunk)
        fetched += len(chunk)

        if on_progress is not None:
            on_progress(fetched, total_bars)

        last_ts_ms = int(chunk.index[-1].timestamp() * 1000)
        since = last_ts_ms + tf_ms

        if len(ohlcv) < chunk_size:
            break

    if not all_chunks:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.concat(all_chunks)
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()
    df = df[df.index >= pd.Timestamp(start)]
    df = df[df.index <= pd.Timestamp(end)]
    return df


def check_connection(
    exchange_id: str,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> dict:
    """Validate connection; returns {"ok": bool, "message": str, "authenticated": bool}."""
    try:
        import ccxt
    except ImportError:
        return {"ok": False, "message": "ccxt не установлен", "authenticated": False}

    exchange_cls = getattr(ccxt, exchange_id.lower(), None)
    if exchange_cls is None:
        return {"ok": False, "message": f"Неизвестная биржа: {exchange_id}", "authenticated": False}

    config: dict = {"enableRateLimit": True}
    authenticated = bool(api_key and api_secret)
    if authenticated:
        config["apiKey"] = api_key
        config["secret"] = api_secret

    try:
        exchange = exchange_cls(config)
        exchange.fetch_time()
        msg = "Подключено (авторизован)" if authenticated else "Подключено (публично)"
        return {"ok": True, "message": msg, "authenticated": authenticated}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "authenticated": authenticated}
