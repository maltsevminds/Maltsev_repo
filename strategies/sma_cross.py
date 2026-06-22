import pandas as pd
from strategies.base import BaseStrategy


class SMACrossStrategy(BaseStrategy):
    name = "sma_cross"
    description = "Trend Following — buy when fast SMA crosses above slow SMA, sell on cross below"

    def __init__(self, fast: int = 20, slow: int = 50):
        self.fast = fast
        self.slow = slow

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        fast_ma = df["close"].rolling(self.fast).mean()
        slow_ma = df["close"].rolling(self.slow).mean()
        above = (fast_ma > slow_ma).astype(int)
        signal = above.diff().fillna(0).astype(int)
        signal.name = "signal"
        return signal

    def get_params(self) -> dict:
        return {"fast": self.fast, "slow": self.slow}
