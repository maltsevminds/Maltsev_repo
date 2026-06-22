import pandas as pd
from strategies.base import BaseStrategy


class StochEMAScalpStrategy(BaseStrategy):
    name = "stoch_ema_scalp"
    description = (
        "Scalping (M15) — Stochastic crossover filtered by EMA trend. Enter long "
        "when %K crosses above %D in the oversold zone while price is above the "
        "trend EMA; exit when %K crosses below %D in the overbought zone."
    )

    def __init__(
        self,
        k_period: int = 14,
        d_period: int = 3,
        trend_period: int = 50,
        oversold: float = 25.0,
        overbought: float = 75.0,
    ):
        self.k_period = k_period
        self.d_period = d_period
        self.trend_period = trend_period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        low_min = df["low"].rolling(self.k_period).min()
        high_max = df["high"].rolling(self.k_period).max()
        rng = (high_max - low_min).replace(0, 1e-10)

        k = 100.0 * (close - low_min) / rng
        d = k.rolling(self.d_period).mean()
        ema = close.ewm(span=self.trend_period, adjust=False).mean()
        uptrend = close > ema

        cross_up = (k.shift(1) <= d.shift(1)) & (k > d)
        cross_down = (k.shift(1) >= d.shift(1)) & (k < d)

        signal = pd.Series(0, index=df.index, name="signal")
        signal[cross_up & (k < self.oversold) & uptrend] = 1
        signal[cross_down & (k > self.overbought)] = -1
        return signal

    def get_params(self) -> dict:
        return {
            "k_period": self.k_period,
            "d_period": self.d_period,
            "trend_period": self.trend_period,
            "oversold": self.oversold,
            "overbought": self.overbought,
        }
