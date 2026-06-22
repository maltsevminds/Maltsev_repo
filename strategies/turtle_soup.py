import pandas as pd
from strategies.base import BaseStrategy


class TurtleSoupStrategy(BaseStrategy):
    name = "turtle_soup"
    description = (
        "Counter-trend (Raschke/Connors) — fade false breakouts of N-bar lows. "
        "Buy when price dips below the rolling N-bar low then closes back above it. "
        "Exit when close falls back below a fast EMA."
    )

    def __init__(self, n_bars: int = 20, exit_ema: int = 5):
        self.n_bars = n_bars
        self.exit_ema = exit_ema

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        low = df["low"]

        # N-bar low of lows, not including current bar
        rolling_low = low.rolling(self.n_bars).min().shift(1)

        # Entry: previous close at/below N-bar low → current close recovers above it
        entry_mask = (close.shift(1) <= rolling_low) & (close > rolling_low)

        # Exit: close falls back below a fast EMA (failed reversal)
        fast_ema = close.ewm(span=self.exit_ema, adjust=False).mean()
        exit_mask = (close < fast_ema) & ~entry_mask

        signal = pd.Series(0, index=df.index, name="signal")
        signal[entry_mask] = 1
        signal[exit_mask] = -1
        return signal

    def get_params(self) -> dict:
        return {"n_bars": self.n_bars, "exit_ema": self.exit_ema}
