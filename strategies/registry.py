from __future__ import annotations

from strategies.sma_cross import SMACrossStrategy
from strategies.ema_cross import EMACrossStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.base import BaseStrategy

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "sma_cross": SMACrossStrategy,
    "ema_cross": EMACrossStrategy,
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
}

STRATEGY_LABELS: dict[str, str] = {
    "sma_cross": "SMA Crossover",
    "ema_cross": "EMA Crossover",
    "rsi": "RSI Mean Reversion",
    "macd": "MACD Momentum",
}


def get_strategy(name: str, params: dict | None = None) -> BaseStrategy:
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {list(STRATEGY_REGISTRY)}"
        )
    return cls(**(params or {}))


def list_strategies() -> list[str]:
    return list(STRATEGY_REGISTRY.keys())
