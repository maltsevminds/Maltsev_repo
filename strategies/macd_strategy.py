import pandas as pd
from strategies.base import BaseStrategy


class MACDStrategy(BaseStrategy):
    name = "macd"
    description = "Momentum — buy when MACD line crosses above signal line, sell on cross below"

    def __init__(self, fast: int = 12, slow: int = 26, signal_period: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal_period = signal_period

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        ema_fast = df["close"].ewm(span=self.fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.signal_period, adjust=False).mean()
        above = (macd_line > signal_line).astype(int)
        signal = above.diff().fillna(0).astype(int)
        signal.name = "signal"
        return signal

    def get_params(self) -> dict:
        return {
            "fast": self.fast,
            "slow": self.slow,
            "signal_period": self.signal_period,
        }
