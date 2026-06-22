"""Generate synthetic OHLCV data for testing without real market data."""
import numpy as np
import pandas as pd


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
    index = pd.date_range(start=start, periods=n_bars, freq=freq)

    log_returns = np.random.normal(drift, volatility, n_bars)
    close = initial_price * np.exp(np.cumsum(log_returns))

    noise = np.abs(np.random.normal(0, volatility * 0.5, n_bars))
    high = close * (1.0 + noise)
    low = close * (1.0 - noise)
    open_ = np.roll(close, 1)
    open_[0] = initial_price
    volume = np.random.lognormal(mean=10.0, sigma=0.8, size=n_bars)

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
    df.index.name = "timestamp"
    return df
