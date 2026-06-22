"""Fetch public (and private) OHLCV data from exchanges via ccxt."""
from __future__ import annotations

import pandas as pd


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
