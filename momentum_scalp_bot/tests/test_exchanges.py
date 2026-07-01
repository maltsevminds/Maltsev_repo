"""Multi-exchange wiring — Binance USDM and Bybit testnet build correctly."""

import pytest

from momentum_scalp.config import Config, load_config
from momentum_scalp.data_feed import build_exchange


def test_bybit_config_loads_and_is_mode_flexible():
    cfg = load_config("config.bybit.yaml")
    assert cfg.exchange.id == "bybit"
    assert cfg.exchange.default_type == "swap"
    assert cfg.exchange.options.get("defaultSubType") == "linear"
    assert cfg.symbols[0] == "BTC/USDT:USDT"
    assert cfg.cluster_of("BTC/USDT:USDT") == "majors"
    # No hard-coded mode -> defaults to backtest; --mode overrides it.
    assert cfg.mode.value == "backtest"
    assert load_config("config.bybit.yaml", mode="paper").mode.value == "paper"
    assert load_config("config.bybit.yaml", mode="testnet").mode.value == "testnet"


def test_build_bybit_testnet_exchange_is_sandboxed(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET_API_KEY", "k")
    monkeypatch.setenv("BYBIT_TESTNET_API_SECRET", "s")
    cfg = load_config("config.bybit.yaml", mode="testnet")
    ex = build_exchange(cfg)
    try:
        assert ex.id == "bybit"
        assert ex.options.get("defaultType") == "swap"
        assert ex.options.get("defaultSubType") == "linear"
        assert ex.apiKey == "k"
        # sandbox mode points the REST/WS urls at testnet
        assert "testnet" in str(ex.urls.get("api"))
    finally:
        # ccxt.pro exchanges hold an aiohttp session; close it if possible
        close = getattr(ex, "close", None)
        if close:
            import asyncio
            try:
                asyncio.run(close())
            except Exception:
                pass


def test_bybit_backtest_runs_on_usdt_symbols():
    """The whole backtest pipeline handles Bybit's :USDT settle-suffix symbols
    (enrich, cluster lookup, sizing, engine) without special-casing."""
    import numpy as np
    import pandas as pd

    from momentum_scalp.backtester import BacktestEngine

    cfg = load_config("config.bybit.yaml", mode="backtest",
                      overrides={"strategy": {"ema_bias_period": 20}})

    def frame(n, freq):
        idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
        c = 100 + 0.1 * np.arange(n)
        return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1,
                             "close": c, "volume": np.full(n, 100.0)}, index=idx)

    syms = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    data5 = {s: frame(300, "5min") for s in syms}
    data1h = {s: frame(60, "1h") for s in syms}
    res = BacktestEngine(cfg).run(data5, data1h)
    assert "trades" in res.stats
    assert res.initial_equity == 10_000  # from config.bybit.yaml backtest block
    assert cfg.cluster_of("BTC/USDT:USDT") == "majors"


def test_build_binance_default_is_future_type():
    cfg = Config.model_validate({"symbols": ["BTC/USDT"], "mode": "backtest"})
    ex = build_exchange(cfg)
    try:
        assert ex.id == "binanceusdm"
        assert ex.options.get("defaultType") == "future"
    finally:
        close = getattr(ex, "close", None)
        if close:
            import asyncio
            try:
                asyncio.run(close())
            except Exception:
                pass
