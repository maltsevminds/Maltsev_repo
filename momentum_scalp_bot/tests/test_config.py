"""Config validation tests."""

import textwrap

import pytest

from momentum_scalp.config import Config, Mode, load_config

BASE = {
    "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "risk": {
        "clusters": {"majors": {"symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"], "max": 2}}
    },
}


def test_defaults_apply():
    cfg = Config.model_validate(BASE)
    assert cfg.mode is Mode.backtest
    assert cfg.confirm_live is False
    assert cfg.leverage_cap == 20
    assert cfg.risk.risk_per_trade == 0.015
    assert cfg.strategy.atr_stop_mult == 1.2


def test_live_is_locked_by_default():
    with pytest.raises(ValueError, match="LOCKED"):
        Config.model_validate({**BASE, "mode": "live"})


def test_live_allowed_with_confirmation():
    cfg = Config.model_validate({**BASE, "mode": "live", "confirm_live": True})
    assert cfg.mode is Mode.live
    assert cfg.is_live_orders is True


def test_testnet_places_orders_paper_does_not():
    assert Config.model_validate({**BASE, "mode": "testnet"}).is_live_orders is True
    assert Config.model_validate({**BASE, "mode": "paper"}).is_live_orders is False


def test_cluster_must_reference_known_symbols():
    bad = {"symbols": ["BTC/USDT"], "risk": {"clusters": {"x": {"symbols": ["FOO/USDT"], "max": 1}}}}
    with pytest.raises(ValueError, match="unknown symbols"):
        Config.model_validate(bad)


def test_target_fractions_must_sum_to_one():
    bad = {**BASE, "targets": {"tp1_close_pct": 0.5, "tp2_close_pct": 0.5, "runner_pct": 0.2}}
    with pytest.raises(ValueError, match="must equal 1.0"):
        Config.model_validate(bad)


def test_rsi_band_order_enforced():
    bad = {**BASE, "strategy": {"rsi_long": [78, 58]}}
    with pytest.raises(ValueError, match="must be <"):
        Config.model_validate(bad)


def test_unknown_key_rejected():
    with pytest.raises(ValueError):
        Config.model_validate({**BASE, "nonsense": 1})


def test_cluster_of_lookup():
    cfg = Config.model_validate(BASE)
    assert cfg.cluster_of("BTC/USDT") == "majors"
    assert cfg.cluster_of("XRP/USDT") is None


def test_resolve_credentials_missing(monkeypatch):
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
    cfg = Config.model_validate({**BASE, "mode": "testnet"})
    with pytest.raises(RuntimeError, match="Missing API credentials"):
        cfg.resolve_credentials()


def test_resolve_credentials_ok(monkeypatch):
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "k")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "s")
    cfg = Config.model_validate({**BASE, "mode": "testnet"})
    assert cfg.resolve_credentials() == ("k", "s")


def test_load_config_from_yaml_with_mode_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        textwrap.dedent(
            """
            symbols: [BTC/USDT, ETH/USDT]
            mode: backtest
            """
        )
    )
    cfg = load_config(p, mode="paper")
    assert cfg.mode is Mode.paper


def test_load_config_live_override_still_locked(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("symbols: [BTC/USDT]\n")
    with pytest.raises(ValueError, match="LOCKED"):
        load_config(p, mode="live")
