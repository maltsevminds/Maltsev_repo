import pandas as pd
from strategies.base import BaseStrategy


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100.0 - (100.0 / (1.0 + rs))


class RSIStrategy(BaseStrategy):
    name = "rsi"
    description = "Mean Reversion — buy when RSI exits oversold, sell when RSI exits overbought"

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        rsi = _rsi(df["close"], self.period)
        signal = pd.Series(0, index=df.index, name="signal")
        signal[(rsi.shift(1) <= self.oversold) & (rsi > self.oversold)] = 1
        signal[(rsi.shift(1) >= self.overbought) & (rsi < self.overbought)] = -1
        return signal

    def get_params(self) -> dict:
        return {
            "period": self.period,
            "oversold": self.oversold,
            "overbought": self.overbought,
        }
