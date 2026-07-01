"""RiskManager tests: sizing math + every risk-gate limit."""

from datetime import datetime, timedelta, timezone

import pytest

from momentum_scalp.config import Config
from momentum_scalp.risk_manager import RiskManager

UTC = timezone.utc


def make_config(**risk_overrides) -> Config:
    risk = {
        "clusters": {"majors": {"symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT"], "max": 2}},
        **risk_overrides,
    }
    return Config.model_validate(
        {
            "symbols": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"],
            "risk": risk,
        }
    )


def make_rm(equity=10_000.0, **risk_overrides) -> RiskManager:
    rm = RiskManager(make_config(**risk_overrides), equity)
    rm.on_equity_update(equity, datetime(2024, 1, 1, tzinfo=UTC))  # Monday
    return rm


# --------------------------------------------------------------------------- #
# Sizing
# --------------------------------------------------------------------------- #
def test_sizing_basic_risk_and_notional():
    rm = make_rm(10_000)
    # entry 100, stop 98 -> stop_pct = 2%. risk = 1.5% * 10k = $150.
    size = rm.compute_size(entry_price=100.0, stop_price=98.0)
    assert size.risk_usd == pytest.approx(150.0)
    assert size.stop_pct == pytest.approx(0.02)
    # notional = 150 / 0.02 = 7500 -> leverage 0.75 -> floored to min 1.
    assert size.notional == pytest.approx(7500.0)
    assert size.leverage == 1
    assert size.quantity == pytest.approx(75.0)


def test_sizing_loss_at_stop_equals_risk_usd():
    """The whole point of risk-based sizing: hitting the stop loses ~risk_usd."""
    rm = make_rm(10_000)
    entry, stop = 100.0, 97.0
    size = rm.compute_size(entry_price=entry, stop_price=stop)
    modeled_loss = size.quantity * (entry - stop)
    assert modeled_loss == pytest.approx(size.risk_usd, rel=1e-9)


def test_sizing_leverage_capped():
    rm = make_rm(10_000)
    # Very tight stop (0.05%) -> huge notional -> leverage must cap at 20.
    size = rm.compute_size(entry_price=100.0, stop_price=99.95)
    assert size.leverage == 20
    # And notional is bounded by leverage*equity so risk is never exceeded.
    assert size.notional <= 20 * 10_000 + 1e-6


def test_sizing_short_uses_abs_distance():
    rm = make_rm(10_000)
    long = rm.compute_size(entry_price=100.0, stop_price=98.0)
    short = rm.compute_size(entry_price=100.0, stop_price=102.0)
    assert short.stop_pct == pytest.approx(long.stop_pct)
    assert short.notional == pytest.approx(long.notional)


def test_sizing_rejects_bad_inputs():
    rm = make_rm(10_000)
    with pytest.raises(ValueError):
        rm.compute_size(entry_price=100.0, stop_price=100.0)  # zero distance
    with pytest.raises(ValueError):
        rm.compute_size(entry_price=0.0, stop_price=1.0)


# --------------------------------------------------------------------------- #
# Position / cluster limits
# --------------------------------------------------------------------------- #
def test_max_positions_gate():
    rm = make_rm(max_positions=3)
    now = datetime(2024, 1, 1, 12, tzinfo=UTC)
    assert rm.can_open("XRP/USDT", now, ["BNB/USDT", "DOGE/USDT"]).allowed
    d = rm.can_open("XRP/USDT", now, ["BNB/USDT", "DOGE/USDT", "ETH/USDT"])
    assert not d.allowed and "max positions" in d.reason


def test_cluster_cap_majors_max_two():
    rm = make_rm()
    now = datetime(2024, 1, 1, 12, tzinfo=UTC)
    # BTC + ETH already open (2 majors) -> SOL blocked, but non-major allowed.
    assert not rm.can_open("SOL/USDT", now, ["BTC/USDT", "ETH/USDT"]).allowed
    assert rm.can_open("XRP/USDT", now, ["BTC/USDT", "ETH/USDT"]).allowed
    # Only one major open -> second major fine.
    assert rm.can_open("SOL/USDT", now, ["BTC/USDT"]).allowed


def test_no_averaging_down():
    rm = make_rm()
    now = datetime(2024, 1, 1, 12, tzinfo=UTC)
    d = rm.can_open("BTC/USDT", now, ["BTC/USDT"])
    assert not d.allowed and "averaging" in d.reason


# --------------------------------------------------------------------------- #
# Daily / weekly limits
# --------------------------------------------------------------------------- #
def test_daily_loss_limit_halts_same_day_and_resets_next_day():
    rm = make_rm(10_000)
    day = datetime(2024, 1, 2, 9, tzinfo=UTC)  # Tuesday
    rm.register_close("BTC/USDT", pnl=-600.0, now=day, was_stop=True)  # -6% > 5%
    assert rm.daily_halted()
    assert not rm.can_open("XRP/USDT", day, []).allowed
    # Next UTC day: anchor rolls, halt clears.
    nxt = datetime(2024, 1, 3, 0, 1, tzinfo=UTC)
    rm.on_equity_update(rm.equity, nxt)
    assert not rm.daily_halted()
    assert rm.can_open("XRP/USDT", nxt, []).allowed


def test_daily_limit_not_triggered_below_threshold():
    rm = make_rm(10_000)
    day = datetime(2024, 1, 2, 9, tzinfo=UTC)
    rm.register_close("BTC/USDT", pnl=-400.0, now=day, was_stop=True)  # -4%
    assert not rm.daily_halted()


def test_weekly_loss_limit_flattens_and_locks_until_monday():
    rm = make_rm(10_000)
    tue = datetime(2024, 1, 2, 9, tzinfo=UTC)
    rm.register_close("BTC/USDT", pnl=-1500.0, now=tue, was_stop=True)  # -15% > 14%
    assert rm.weekly_halted(tue)
    assert rm.must_flatten(tue)
    assert not rm.can_open("XRP/USDT", tue, []).allowed
    # Still locked later in the same week even if equity recovers a bit.
    fri = datetime(2024, 1, 5, 9, tzinfo=UTC)
    rm.on_equity_update(rm.equity + 300, fri)
    assert rm.weekly_halted(fri)
    # Next Monday: lock clears.
    mon = datetime(2024, 1, 8, 0, 1, tzinfo=UTC)
    rm.on_equity_update(rm.equity, mon)
    assert not rm.weekly_halted(mon)


def test_weekly_drawdown_reduces_risk_fraction():
    rm = make_rm(10_000)
    tue = datetime(2024, 1, 2, 9, tzinfo=UTC)
    assert rm.current_risk_fraction() == 0.015
    rm.register_close("BTC/USDT", pnl=-800.0, now=tue, was_stop=True)  # -8% > 7%
    assert rm.current_risk_fraction() == 0.01
    # And new sizing uses the reduced fraction.
    size = rm.compute_size(entry_price=100.0, stop_price=98.0)
    assert size.risk_fraction == 0.01


# --------------------------------------------------------------------------- #
# Consecutive-stop pause
# --------------------------------------------------------------------------- #
def test_two_stops_trigger_pause_then_expire():
    rm = make_rm(10_000, consecutive_stops_pause=2, pause_minutes=90)
    t0 = datetime(2024, 1, 2, 9, tzinfo=UTC)
    rm.register_close("BTC/USDT", pnl=-50.0, now=t0, was_stop=True)
    assert not rm.is_paused(t0)
    t1 = t0 + timedelta(minutes=5)
    rm.register_close("ETH/USDT", pnl=-50.0, now=t1, was_stop=True)
    assert rm.is_paused(t1)
    assert not rm.can_open("XRP/USDT", t1, []).allowed
    # Still paused at +89m, free at +91m.
    assert rm.is_paused(t1 + timedelta(minutes=89))
    later = t1 + timedelta(minutes=91)
    assert not rm.is_paused(later)
    assert rm.can_open("XRP/USDT", later, []).allowed


def test_win_resets_stop_streak():
    rm = make_rm(10_000, consecutive_stops_pause=2)
    t0 = datetime(2024, 1, 2, 9, tzinfo=UTC)
    rm.register_close("BTC/USDT", pnl=-50.0, now=t0, was_stop=True)
    rm.register_close("ETH/USDT", pnl=+120.0, now=t0 + timedelta(minutes=1), was_stop=False)
    assert rm.consecutive_stops == 0
    rm.register_close("SOL/USDT", pnl=-50.0, now=t0 + timedelta(minutes=2), was_stop=True)
    assert not rm.is_paused(t0 + timedelta(minutes=2))  # only 1 stop since the win


# --------------------------------------------------------------------------- #
# Bookkeeping
# --------------------------------------------------------------------------- #
def test_high_water_mark_tracks_peak():
    rm = make_rm(10_000)
    t = datetime(2024, 1, 2, tzinfo=UTC)
    rm.on_equity_update(11_000, t)
    rm.on_equity_update(10_500, t)
    assert rm.high_water_mark == 11_000


def test_rolling_7d_pnl_window():
    rm = make_rm(10_000)
    now = datetime(2024, 1, 10, 12, tzinfo=UTC)
    rm.register_close("BTC/USDT", pnl=100.0, now=now - timedelta(days=8), was_stop=False)  # outside
    rm.register_close("ETH/USDT", pnl=50.0, now=now - timedelta(days=2), was_stop=False)   # inside
    assert rm.rolling_7d_pnl(now) == pytest.approx(50.0)
