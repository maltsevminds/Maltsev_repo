"""Typed configuration for the momentum-scalp bot.

The whole config lives in ``config.yaml``; this module loads it, validates it
with pydantic v2, and exposes a single immutable :class:`Config` object.

Secrets never live in the YAML — the YAML only names the ENV VARS that hold the
keys (see ``.env.example``). :func:`Config.resolve_credentials` reads those env
vars at runtime for the active mode.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Dict, List, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Mode(str, Enum):
    """Run modes, in the mandatory progression order."""

    backtest = "backtest"
    paper = "paper"
    testnet = "testnet"
    live = "live"


# --------------------------------------------------------------------------- #
# Nested sections
# --------------------------------------------------------------------------- #
class _Base(BaseModel):
    # Reject unknown keys so typos in the YAML fail loudly instead of silently.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExchangeCfg(_Base):
    id: str = "binanceusdm"            # any ccxt.pro exchange id (e.g. bybit)
    testnet: bool = True
    default_type: str = "future"       # 'future' (binance USDM) | 'swap' (bybit linear)
    options: Dict[str, object] = Field(default_factory=dict)  # extra ccxt options
    live_key_env: str = "BINANCE_API_KEY"
    live_secret_env: str = "BINANCE_API_SECRET"
    testnet_key_env: str = "BINANCE_TESTNET_API_KEY"
    testnet_secret_env: str = "BINANCE_TESTNET_API_SECRET"


class TimeframesCfg(_Base):
    bias: str = "1h"
    entry: str = "5m"


class StrategyCfg(_Base):
    ema_bias_period: int = 200
    donchian_period: int = 20
    volume_sma_period: int = 20
    volume_mult: float = 1.5
    adx_period: int = 14
    adx_min: float = 23.0
    rsi_period: int = 14
    rsi_long: Tuple[float, float] = (58.0, 78.0)
    rsi_short: Tuple[float, float] = (22.0, 42.0)
    funding_max: float = 0.0005
    atr_period: int = 14
    atr_stop_mult: float = 1.2

    @field_validator("rsi_long", "rsi_short")
    @classmethod
    def _ordered_band(cls, v: Tuple[float, float]) -> Tuple[float, float]:
        lo, hi = v
        if lo >= hi:
            raise ValueError(f"RSI band low ({lo}) must be < high ({hi})")
        return v


class TargetsCfg(_Base):
    tp1_r: float = 1.0
    tp1_close_pct: float = 0.5
    move_stop_to_be_after_tp1: bool = True
    tp2_r: float = 2.0
    tp2_close_pct: float = 0.3
    runner_pct: float = 0.2
    runner_trail_atr_mult: float = 1.5

    @model_validator(mode="after")
    def _fractions_sum_to_one(self) -> "TargetsCfg":
        total = self.tp1_close_pct + self.tp2_close_pct + self.runner_pct
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"tp1_close_pct + tp2_close_pct + runner_pct must equal 1.0 "
                f"(got {total})"
            )
        return self


class ClusterCfg(_Base):
    symbols: List[str]
    max: int = Field(ge=1)


class RiskCfg(_Base):
    risk_per_trade: float = Field(0.015, gt=0, le=0.1)
    reduced_risk_per_trade: float = Field(0.01, gt=0, le=0.1)
    daily_loss_limit: float = Field(0.05, gt=0, le=1)
    weekly_loss_limit: float = Field(0.14, gt=0, le=1)
    weekly_drawdown_reduce: float = Field(0.07, gt=0, le=1)
    max_positions: int = Field(3, ge=1)
    consecutive_stops_pause: int = Field(2, ge=1)
    pause_minutes: int = Field(90, ge=0)
    no_averaging_down: bool = True
    clusters: Dict[str, ClusterCfg] = Field(default_factory=dict)


class DatabaseCfg(_Base):
    path: str = "data/bot.sqlite"


class TelegramCfg(_Base):
    enabled: bool = False
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"


class WatchdogCfg(_Base):
    ws_timeout_seconds: int = Field(30, ge=1)
    reconnect_max_retries: int = Field(10, ge=0)
    reconnect_backoff_seconds: float = Field(2.0, gt=0)


class BacktestCfg(_Base):
    start: str = "2024-01-01"
    end: str = "2024-06-01"
    initial_equity: float = Field(10000, gt=0)
    fee_rate: float = Field(0.0004, ge=0)
    slippage_pct: float = Field(0.0002, ge=0)


class PaperCfg(_Base):
    initial_equity: float = Field(10000, gt=0)


class WalkForwardCfg(_Base):
    train: int = Field(3000, ge=1)   # in-sample bars
    test: int = Field(1000, ge=1)    # out-of-sample bars
    step: int = Field(1000, ge=1)    # roll size


class OptimizeCfg(_Base):
    # Objective metric (a key of the backtest stats dict). max_drawdown_pct is
    # minimized; every other metric is maximized.
    metric: str = "total_return_pct"
    top: int = Field(10, ge=1)       # how many leaders to report
    walk_forward: WalkForwardCfg = Field(default_factory=WalkForwardCfg)
    # Dotted config paths -> list of candidate values, e.g.
    #   {"strategy.adx_min": [20, 23, 26], "strategy.atr_stop_mult": [1.0, 1.2]}
    grid: Dict[str, List] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Root config
# --------------------------------------------------------------------------- #
class Config(_Base):
    mode: Mode = Mode.backtest
    confirm_live: bool = False
    exchange: ExchangeCfg = Field(default_factory=ExchangeCfg)
    symbols: List[str]
    timeframes: TimeframesCfg = Field(default_factory=TimeframesCfg)
    leverage_cap: int = Field(20, ge=1, le=125)
    strategy: StrategyCfg = Field(default_factory=StrategyCfg)
    targets: TargetsCfg = Field(default_factory=TargetsCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    database: DatabaseCfg = Field(default_factory=DatabaseCfg)
    telegram: TelegramCfg = Field(default_factory=TelegramCfg)
    watchdog: WatchdogCfg = Field(default_factory=WatchdogCfg)
    backtest: BacktestCfg = Field(default_factory=BacktestCfg)
    paper: PaperCfg = Field(default_factory=PaperCfg)
    optimize: OptimizeCfg = Field(default_factory=OptimizeCfg)

    @field_validator("symbols")
    @classmethod
    def _non_empty_symbols(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("`symbols` must not be empty")
        return v

    @model_validator(mode="after")
    def _validate_clusters_reference_symbols(self) -> "Config":
        known = set(self.symbols)
        for name, cluster in self.risk.clusters.items():
            unknown = set(cluster.symbols) - known
            if unknown:
                raise ValueError(
                    f"cluster '{name}' references unknown symbols: {sorted(unknown)}"
                )
        return self

    @model_validator(mode="after")
    def _live_requires_confirmation(self) -> "Config":
        # The hard safety lock: refuse to construct a live config unless the
        # operator has explicitly flipped confirm_live.
        if self.mode is Mode.live and not self.confirm_live:
            raise ValueError(
                "mode=live is LOCKED. Set confirm_live: true in config.yaml to "
                "trade real money (do this only after backtest -> paper -> testnet)."
            )
        return self

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @property
    def is_live_orders(self) -> bool:
        """True when the mode places real orders on an exchange."""
        return self.mode in (Mode.testnet, Mode.live)

    def cluster_of(self, symbol: str) -> str | None:
        """Return the cluster name a symbol belongs to, or None."""
        for name, cluster in self.risk.clusters.items():
            if symbol in cluster.symbols:
                return name
        return None

    def resolve_credentials(self) -> Tuple[str, str]:
        """Read API key/secret from the environment for the active mode.

        Only meaningful for testnet/live. Raises if the env vars are missing.
        """
        if self.mode is Mode.testnet:
            key_env, sec_env = (
                self.exchange.testnet_key_env,
                self.exchange.testnet_secret_env,
            )
        elif self.mode is Mode.live:
            key_env, sec_env = (
                self.exchange.live_key_env,
                self.exchange.live_secret_env,
            )
        else:
            raise RuntimeError(
                f"resolve_credentials() called in mode={self.mode.value}; "
                "no keys are needed for backtest/paper."
            )
        key, secret = os.getenv(key_env), os.getenv(sec_env)
        if not key or not secret:
            raise RuntimeError(
                f"Missing API credentials: set {key_env} and {sec_env} in .env"
            )
        return key, secret


# --------------------------------------------------------------------------- #
# Loader
# --------------------------------------------------------------------------- #
def load_config(
    path: str | Path = "config.yaml",
    *,
    mode: str | Mode | None = None,
    overrides: dict | None = None,
) -> Config:
    """Load and validate the YAML config.

    ``mode`` (typically from the CLI ``--mode`` flag) overrides the YAML value.
    ``overrides`` is a shallow dict merged on top, handy for tests.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if mode is not None:
        raw["mode"] = mode.value if isinstance(mode, Mode) else mode
    if overrides:
        raw.update(overrides)
    return Config.model_validate(raw)
