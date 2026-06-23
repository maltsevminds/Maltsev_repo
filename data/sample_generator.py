"""Generate synthetic OHLCV data for testing without real market data."""
import numpy as np
import pandas as pd

# Map common exchange-style timeframe aliases to valid pandas offset strings.
_FREQ_ALIASES = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1d": "1D",
}


def generate_sample_ohlcv(
    n_bars: int = 2000,
    start: str = "2023-01-01",
    freq: str = "1h",
    initial_price: float = 30_000.0,
    drift: float = 0.0001,
    volatility: float = 0.015,
    seed: int = 42,
) -> pd.DataFrame:
    np.random.seed(seed)
    pandas_freq = _FREQ_ALIASES.get(freq, freq)
    index = pd.date_range(start=start, periods=n_bars, freq=pandas_freq)

    log_returns = np.random.normal(drift, volatility, n_bars)
    close = initial_price * np.exp(np.cumsum(log_returns))

    noise = np.abs(np.random.normal(0, volatility * 0.5, n_bars))
    open_ = np.roll(close, 1)
    open_[0] = initial_price
    # High/low must envelope BOTH open and close, otherwise bars are invalid
    # (open outside [low, high]) and intrabar SL/TP fills become meaningless.
    high = np.maximum(open_, close) * (1.0 + noise)
    low = np.minimum(open_, close) * (1.0 - noise)
    volume = np.random.lognormal(mean=10.0, sigma=0.8, size=n_bars)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
    df.index.name = "timestamp"
    return df
