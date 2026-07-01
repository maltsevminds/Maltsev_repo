"""Dashboard tests — rendering, KPIs, tables, CLI output."""

from datetime import datetime, timezone

import pytest

from momentum_scalp import dashboard as dash
from momentum_scalp.db import Database, PositionRow

UTC = timezone.utc


def seeded_db():
    db = Database(":memory:")
    db.record_equity(10_000, 10_000, "paper", ts=datetime(2024, 1, 1, tzinfo=UTC))
    db.record_equity(10_200, 10_250, "paper", ts=datetime(2024, 1, 2, tzinfo=UTC))

    # one open position
    db.open_position(PositionRow(
        symbol="BTC/USDT", side="long", mode="paper",
        entry_ts=datetime(2024, 1, 2, tzinfo=UTC), entry_price=42000.0, qty=0.1,
        leverage=5, stop_price=41000.0, r=1000.0, risk_usd=150.0))

    # one closed winner
    pid = db.open_position(PositionRow(
        symbol="ETH/USDT", side="short", mode="paper",
        entry_ts=datetime(2024, 1, 1, tzinfo=UTC), entry_price=2500.0, qty=1.0,
        leverage=4, stop_price=2560.0, r=60.0, risk_usd=150.0))
    db.close_position(pid, realized_pnl=120.0, avg_exit_price=2440.0,
                      exit_reason="tp2", exit_ts=datetime(2024, 1, 2, tzinfo=UTC))

    db.log_event("WARNING", "kill_switch", "feed silent 31s", symbol="BTC/USDT")
    db.log_event("INFO", "entry", "long BTC/USDT", symbol="BTC/USDT")
    return db


def test_render_contains_kpis_and_tables():
    db = seeded_db()
    html = dash.render_dashboard(db, mode="paper")
    assert "momentum-scalp dashboard" in html
    assert "Equity" in html and "High-water mark" in html and "Win rate" in html
    assert "<svg" in html                         # equity chart
    assert "BTC/USDT" in html and "ETH/USDT" in html
    assert "kill_switch" in html                  # event log
    assert "tp2" in html                          # closed trade reason
    db.close()


def test_kpis_values():
    db = seeded_db()
    curve = dash._equity_curve(db, "paper")
    k = dash._kpis(db, "paper", curve)
    assert k["equity"] == 10_200
    assert k["high_water_mark"] == 10_250
    assert k["open_positions"] == 1
    assert k["closed_trades"] == 1
    assert k["realized_pnl"] == pytest.approx(120.0)
    assert k["win_rate"] == pytest.approx(1.0)
    db.close()


def test_refresh_meta_present_only_when_requested():
    db = seeded_db()
    assert 'http-equiv="refresh"' in dash.render_dashboard(db, "paper", refresh=5)
    assert 'http-equiv="refresh"' not in dash.render_dashboard(db, "paper", refresh=0)
    db.close()


def test_empty_db_renders_without_error():
    db = Database(":memory:")
    html = dash.render_dashboard(db)
    assert "momentum-scalp dashboard" in html
    assert "— none —" in html   # empty tables
    db.close()


def test_html_escaping_of_event_message():
    db = Database(":memory:")
    db.log_event("INFO", "note", "<script>alert(1)</script>")
    html = dash.render_dashboard(db)
    assert "<script>alert(1)</script>" not in html      # raw not injected
    assert "&lt;script&gt;" in html                     # escaped
    db.close()


def test_dashboard_html_from_file_and_cli_out(tmp_path):
    dbpath = tmp_path / "bot.sqlite"
    db = Database(str(dbpath))
    db.record_equity(10_000, 10_000, "paper")
    db.close()

    html = dash.dashboard_html(str(dbpath), mode="paper")
    assert "momentum-scalp dashboard" in html

    out = tmp_path / "dash.html"
    rc = dash.main(["--db", str(dbpath), "--mode", "paper", "--out", str(out)])
    assert rc == 0 and out.exists()
    assert "<svg" in out.read_text()
