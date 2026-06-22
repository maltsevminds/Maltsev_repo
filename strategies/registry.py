from __future__ import annotations

from strategies.sma_cross import SMACrossStrategy
from strategies.ema_cross import EMACrossStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.bollinger_scalp import BollingerScalpStrategy
from strategies.stoch_ema_scalp import StochEMAScalpStrategy
from strategies.vwap_bounce import VWAPBounceStrategy
from strategies.base import BaseStrategy

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "sma_cross": SMACrossStrategy,
    "ema_cross": EMACrossStrategy,
    "rsi": RSIStrategy,
    "macd": MACDStrategy,
    "bollinger_scalp": BollingerScalpStrategy,
    "stoch_ema_scalp": StochEMAScalpStrategy,
    "vwap_bounce": VWAPBounceStrategy,
}

STRATEGY_LABELS: dict[str, str] = {
    "sma_cross": "SMA Crossover",
    "ema_cross": "EMA Crossover",
    "rsi": "RSI Mean Reversion",
    "macd": "MACD Momentum",
    "bollinger_scalp": "Bollinger Scalp (M15)",
    "stoch_ema_scalp": "Stochastic + EMA Scalp (M15)",
    "vwap_bounce": "VWAP Bounce Scalp (M15)",
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
