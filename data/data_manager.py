from __future__ import annotations

import pandas as pd

from data.csv_loader import load_ohlcv_csv
from data.fetcher import fetch_ohlcv, fetch_ohlcv_all, check_connection


class DataManager:
    def load_from_csv(self, source) -> pd.DataFrame:
        return load_ohlcv_csv(source)

    def load_from_exchange(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = 1000,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> pd.DataFrame:
        return fetch_ohlcv(
            exchange, symbol, timeframe, start, end, limit, api_key, api_secret
        )

    def load_from_exchange_all(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        chunk_size: int = 1000,
        on_progress=None,
    ) -> pd.DataFrame:
        return fetch_ohlcv_all(
            exchange, symbol, timeframe, start, end,
            api_key, api_secret, chunk_size, on_progress,
        )

    def check_connection(
        self,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> dict:
        return check_connection(exchange, api_key, api_secret)

    def filter_dates(
        self,
        df: pd.DataFrame,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        return df
