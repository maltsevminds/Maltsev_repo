import pandas as pd
from strategies.base import BaseStrategy


class BollingerScalpStrategy(BaseStrategy):
    name = "bollinger_scalp"
    description = (
        "Scalping (M15) — Bollinger Bands mean reversion. Buy when price snaps "
        "back above the lower band, exit on return to the middle band."
    )

    def __init__(self, period: int = 20, num_std: float = 2.0):
        self.period = period
        self.num_std = num_std

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        middle = close.rolling(self.period).mean()
        std = close.rolling(self.period).std()
        lower = middle - self.num_std * std

        signal = pd.Series(0, index=df.index, name="signal")
        # Entry: close crosses back up through the lower band (oversold bounce)
        signal[(close.shift(1) <= lower.shift(1)) & (close > lower)] = 1
        # Exit: close reverts up through the middle band (target reached)
        signal[(close.shift(1) <= middle.shift(1)) & (close > middle)] = -1
        return signal

    def get_params(self) -> dict:
        return {"period": self.period, "num_std": self.num_std}
