from __future__ import annotations

import pandas as pd

from data.csv_loader import load_ohlcv_csv
from data.fetcher import fetch_ohlcv


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
    ) -> pd.DataFrame:
        return fetch_ohlcv(exchange, symbol, timeframe, start, end, limit)

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
