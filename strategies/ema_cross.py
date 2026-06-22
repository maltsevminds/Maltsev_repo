import pandas as pd
from strategies.base import BaseStrategy


class EMACrossStrategy(BaseStrategy):
    name = "ema_cross"
    description = "Trend Following — buy when fast EMA crosses above slow EMA"

    def __init__(self, fast: int = 12, slow: int = 26):
        self.fast = fast
        self.slow = slow

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        fast_ema = df["close"].ewm(span=self.fast, adjust=False).mean()
        slow_ema = df["close"].ewm(span=self.slow, adjust=False).mean()
        above = (fast_ema > slow_ema).astype(int)
        signal = above.diff().fillna(0).astype(int)
        signal.name = "signal"
        return signal

    def get_params(self) -> dict:
        return {"fast": self.fast, "slow": self.slow}
