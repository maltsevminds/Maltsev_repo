import io
from pathlib import Path
import pandas as pd

REQUIRED_COLS = {"open", "high", "low", "close", "volume"}
TS_CANDIDATES = ["timestamp", "datetime", "date", "time", "open_time"]


def load_ohlcv_csv(source) -> pd.DataFrame:
    """Load OHLCV data from a file path, Path object, or raw bytes."""
    if isinstance(source, (str, Path)):
        df = pd.read_csv(source)
    elif isinstance(source, bytes):
        df = pd.read_csv(io.BytesIO(source))
    else:
        df = pd.read_csv(source)

    df.columns = [c.lower().strip() for c in df.columns]

    ts_col = next((c for c in TS_CANDIDATES if c in df.columns), None)
    if ts_col is None:
        raise ValueError(
            f"No timestamp column found. Expected one of {TS_CANDIDATES}. "
            f"Got: {list(df.columns)}"
        )

    df[ts_col] = pd.to_datetime(df[ts_col], utc=False)
    df = df.set_index(ts_col)
    df.index.name = "timestamp"
    df = df.sort_index()

    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Got: {list(df.columns)}")

    df = df[list(REQUIRED_COLS)].astype(float).dropna()
    return df
