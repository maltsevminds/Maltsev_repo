import pandas as pd
from strategies.base import BaseStrategy


class Raschke8020Strategy(BaseStrategy):
    name = "raschke_80_20"
    description = (
        "Raschke 80-20 reversal — buy when a bar opens in the bottom 20% of its range "
        "and closes in the top 20% (bullish key reversal bar). Exit on a bearish reversal "
        "bar or when close falls below a short-term EMA."
    )

    def __init__(self, threshold: float = 0.20, exit_ema: int = 5):
        self.threshold = threshold
        self.exit_ema = exit_ema

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        bar_range = (df["high"] - df["low"]).replace(0, 1e-10)

        # Bullish key reversal: opens in bottom X%, closes in top X%
        bullish = (
            (df["open"] <= df["low"] + self.threshold * bar_range) &
            (df["close"] >= df["high"] - self.threshold * bar_range)
        )

        # Bearish key reversal: opens in top X%, closes in bottom X%
        bearish = (
            (df["open"] >= df["high"] - self.threshold * bar_range) &
            (df["close"] <= df["low"] + self.threshold * bar_range)
        )

        # Fast EMA as secondary exit
        fast_ema = df["close"].ewm(span=self.exit_ema, adjust=False).mean()
        ema_exit = df["close"] < fast_ema

        signal = pd.Series(0, index=df.index, name="signal")
        # Buy on the bar after a bullish reversal
        entry_mask = bullish.shift(1).fillna(False)
        # Exit on the bar after a bearish reversal, or EMA cross
        exit_mask = (bearish.shift(1).fillna(False) | ema_exit) & ~entry_mask

        signal[entry_mask] = 1
        signal[exit_mask] = -1
        return signal

    def get_params(self) -> dict:
        return {"threshold": self.threshold, "exit_ema": self.exit_ema}
