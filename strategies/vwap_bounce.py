import pandas as pd
from strategies.base import BaseStrategy


class VWAPBounceStrategy(BaseStrategy):
    name = "vwap_bounce"
    description = (
        "Scalping (M15) — rolling VWAP bounce. Buy when price stretches below a "
        "VWAP deviation band and snaps back, exit when price returns to VWAP."
    )

    def __init__(self, window: int = 48, deviation: float = 0.004):
        # window ~ 48 bars ≈ 12h on M15; deviation = 0.4% band below VWAP
        self.window = window
        self.deviation = deviation

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
        volume = df["volume"]

        pv = (typical * volume).rolling(self.window).sum()
        vol_sum = volume.rolling(self.window).sum().replace(0, 1e-10)
        vwap = pv / vol_sum
        lower_band = vwap * (1.0 - self.deviation)

        signal = pd.Series(0, index=df.index, name="signal")
        # Entry: close crosses back up through the lower deviation band
        signal[(close.shift(1) <= lower_band.shift(1)) & (close > lower_band)] = 1
        # Exit: close reverts up through VWAP (mean reached)
        signal[(close.shift(1) < vwap.shift(1)) & (close >= vwap)] = -1
        return signal

    def get_params(self) -> dict:
        return {"window": self.window, "deviation": self.deviation}
