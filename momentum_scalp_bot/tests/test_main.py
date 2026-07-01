"""CLI tests — CSV loading, backtest dispatch, live safety lock."""

import numpy as np
import pandas as pd
import pytest

from momentum_scalp import main as cli
from momentum_scalp.config import load_config


def _write_csv(path, n, freq, start="2024-01-01"):
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = 100 + 0.1 * np.arange(n)
    df = pd.DataFrame({
        "timestamp": (idx.view("int64") // 1_000_000),  # epoch ms
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": np.full(n, 100.0),
    })
    df.to_csv(path, index=False)


def test_read_ohlcv_csv_epoch_ms(tmp_path):
    p = tmp_path / "BTCUSDT_5m.csv"
    _write_csv(p, 10, "5min")
    df = cli.read_ohlcv_csv(p)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert len(df) == 10


def test_load_from_dir_matches_symbol_files(tmp_path):
    _write_csv(tmp_path / "BTCUSDT_5m.csv", 5, "5min")
    data = cli.load_from_dir(str(tmp_path), ["BTC/USDT", "ETH/USDT"], "5m")
    assert "BTC/USDT" in data and "ETH/USDT" not in data  # ETH file missing


def test_backtest_via_cli_with_csv(tmp_path):
    # Config file
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "symbols: [BTC/USDT]\n"
        "strategy: {ema_bias_period: 20}\n"
        "backtest: {initial_equity: 10000, fee_rate: 0.0, slippage_pct: 0.0}\n"
    )
    _write_csv(tmp_path / "BTCUSDT_5m.csv", 300, "5min")
    _write_csv(tmp_path / "BTCUSDT_1h.csv", 60, "1h", start="2023-12-20")

    rc = cli.main(["--mode", "backtest", "--config", str(cfg_path),
                   "--data-dir", str(tmp_path), "--log-level", "WARNING"])
    assert rc == 0  # runs to completion (0 trades on this flat data is fine)


def test_backtest_report_dir_writes_files(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "symbols: [BTC/USDT]\n"
        "strategy: {ema_bias_period: 20}\n"
        "backtest: {initial_equity: 10000}\n"
    )
    _write_csv(tmp_path / "BTCUSDT_5m.csv", 300, "5min")
    _write_csv(tmp_path / "BTCUSDT_1h.csv", 60, "1h", start="2023-12-20")
    report = tmp_path / "report"
    rc = cli.main(["--mode", "backtest", "--config", str(cfg_path),
                   "--data-dir", str(tmp_path), "--report-dir", str(report),
                   "--log-level", "WARNING"])
    assert rc == 0
    assert (report / "equity_curve.html").exists()
    assert (report / "trades.csv").exists()
    assert (report / "summary.json").exists()


def test_backtest_missing_data_returns_error(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("symbols: [BTC/USDT]\n")
    rc = cli.main(["--mode", "backtest", "--config", str(cfg_path),
                   "--data-dir", str(tmp_path)])
    assert rc == 2  # no usable data


def test_live_mode_locked(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("symbols: [BTC/USDT]\nconfirm_live: false\n")
    # load_config itself refuses to build a live config while locked.
    with pytest.raises(ValueError, match="LOCKED"):
        load_config(cfg_path, mode="live")


def test_run_live_guard_rejects_unconfirmed():
    # A config object that somehow reached run_live without confirmation.
    from momentum_scalp.config import Config, Mode

    c = Config.model_validate({"symbols": ["BTC/USDT"], "mode": "live", "confirm_live": True})
    object.__setattr__(c, "confirm_live", False)  # simulate tampering
    assert cli.run_live(c) == 3


def test_parser_requires_mode():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])
