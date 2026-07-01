"""Reporting tests — CSV/JSON/HTML exports and the SVG renderer."""

import json
from datetime import datetime, timezone

from momentum_scalp.backtester import BacktestResult, compute_stats
from momentum_scalp.reporting import render_equity_svg, write_reports

UTC = timezone.utc


def _result():
    curve = [
        (datetime(2024, 1, 1, tzinfo=UTC), 10_000.0),
        (datetime(2024, 1, 2, tzinfo=UTC), 10_150.0),
        (datetime(2024, 1, 3, tzinfo=UTC), 10_090.0),
        (datetime(2024, 1, 4, tzinfo=UTC), 10_300.0),
    ]
    trades = [
        {"id": 1, "symbol": "BTC/USDT", "side": "long", "realized_pnl": 150.0,
         "exit_reason": "tp2", "entry_price": 100, "avg_exit_price": 106},
        {"id": 2, "symbol": "ETH/USDT", "side": "short", "realized_pnl": -60.0,
         "exit_reason": "stop", "entry_price": 50, "avg_exit_price": 51},
        {"id": 3, "symbol": "SOL/USDT", "side": "long", "realized_pnl": 210.0,
         "exit_reason": "trail_stop", "entry_price": 20, "avg_exit_price": 22},
    ]
    stats = compute_stats(10_000, 10_300, trades, curve)
    return BacktestResult(10_000, 10_300, curve, trades, stats)


def test_write_reports_creates_all_files(tmp_path):
    paths = write_reports(_result(), tmp_path, meta={"symbols": ["BTC/USDT"],
                                                     "start": "2024-01-01", "end": "2024-02-01"})
    for key in ("trades", "equity_csv", "summary", "chart"):
        assert (tmp_path / paths[key].split("/")[-1]).exists()

    # trades.csv has a header + 3 rows
    lines = (tmp_path / "trades.csv").read_text().strip().splitlines()
    assert lines[0].startswith("id,symbol,side")
    assert len(lines) == 4

    # summary.json round-trips
    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["final_equity"] == 10_300
    assert data["stats"]["trades"] == 3

    # equity_curve.csv
    eq = (tmp_path / "equity_curve.csv").read_text().strip().splitlines()
    assert eq[0] == "ts,equity" and len(eq) == 5


def test_html_contains_svg_and_stats(tmp_path):
    paths = write_reports(_result(), tmp_path, meta={"symbols": ["BTC/USDT"]})
    html = (tmp_path / paths["chart"].split("/")[-1]).read_text()
    assert "<svg" in html and "polyline" in html
    assert "Win rate" in html and "Max drawdown" in html


def test_render_equity_svg_polyline_points():
    curve = [(0, 100.0), (1, 110.0), (2, 105.0)]
    svg = render_equity_svg(curve, initial=100.0)
    assert svg.startswith("<svg") and "polyline" in svg
    assert svg.count(",") >= 3  # several coordinate pairs


def test_render_equity_svg_handles_too_few_points():
    svg = render_equity_svg([(0, 100.0)], initial=100.0)
    assert "no equity data" in svg


def test_render_equity_svg_color_reflects_outcome():
    up = render_equity_svg([(0, 100.0), (1, 120.0)], initial=100.0)
    down = render_equity_svg([(0, 100.0), (1, 80.0)], initial=100.0)
    assert "#1a9850" in up      # green when final >= initial
    assert "#d73027" in down    # red when final < initial
