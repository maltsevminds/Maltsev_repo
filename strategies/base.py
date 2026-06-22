from abc import ABC, abstractmethod
import pandas as pd


class BaseStrategy(ABC):
    name: str = "base"
    description: str = ""

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Produce entry/exit signals aligned to df.index.
        Return values: 1 = enter long, -1 = exit long, 0 = hold.
        """

    def get_params(self) -> dict:
        return {}

    def __repr__(self) -> str:
        return f"{self.name}({self.get_params()})"
