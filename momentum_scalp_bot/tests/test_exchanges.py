"""Multi-exchange wiring — Binance USDM and Bybit testnet build correctly."""

import pytest

from momentum_scalp.config import Config, load_config
from momentum_scalp.data_feed import build_exchange


def test_bybit_testnet_config_loads():
    cfg = load_config("config.bybit.testnet.yaml")
    assert cfg.exchange.id == "bybit"
    assert cfg.exchange.default_type == "swap"
    assert cfg.exchange.options.get("defaultSubType") == "linear"
    assert cfg.symbols[0] == "BTC/USDT:USDT"
    assert cfg.cluster_of("BTC/USDT:USDT") == "majors"
    assert cfg.mode.value == "testnet"


def test_build_bybit_testnet_exchange_is_sandboxed(monkeypatch):
    monkeypatch.setenv("BYBIT_TESTNET_API_KEY", "k")
    monkeypatch.setenv("BYBIT_TESTNET_API_SECRET", "s")
    cfg = load_config("config.bybit.testnet.yaml")
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
