"""Watchdog tests — kill-switch logic, single-fire, re-arm, notifier."""

import asyncio

import pytest

from momentum_scalp.config import Config
from momentum_scalp.db import Database
from momentum_scalp.watchdog import (
    NullNotifier,
    TelegramNotifier,
    Watchdog,
    build_notifier,
)


def cfg(**wd):
    raw = {"symbols": ["BTC/USDT"]}
    if wd:
        raw["watchdog"] = wd
    return Config.model_validate(raw)


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


# --------------------------------------------------------------------------- #
# Pure decision
# --------------------------------------------------------------------------- #
def test_should_kill_only_with_open_position_and_stale_feed():
    wd = Watchdog(cfg(ws_timeout_seconds=30))
    assert wd.should_kill(31, has_open_position=True) is True
    assert wd.should_kill(31, has_open_position=False) is False   # nothing at risk
    assert wd.should_kill(29, has_open_position=True) is False    # feed still fresh


# --------------------------------------------------------------------------- #
# tick(): fire once, run callback, log, re-arm
# --------------------------------------------------------------------------- #
def test_tick_fires_once_and_calls_on_kill():
    db = Database(":memory:")
    note = RecordingNotifier()
    killed = []

    async def on_kill(reason):
        killed.append(reason)

    wd = Watchdog(cfg(ws_timeout_seconds=30), db=db, notifier=note, on_kill=on_kill)

    async def scenario():
        first = await wd.tick(40, has_open_position=True)   # trips
        second = await wd.tick(45, has_open_position=True)  # already tripped -> no refire
        return first, second

    first, second = asyncio.run(scenario())
    assert first is True and second is False
    assert len(killed) == 1                     # callback ran exactly once
    assert len(note.sent) == 1                  # one alert
    events = db.recent_events()
    assert events[0]["kind"] == "kill_switch" and events[0]["level"] == "CRITICAL"
    db.close()


def test_tick_rearms_after_recovery():
    note = RecordingNotifier()
    wd = Watchdog(cfg(ws_timeout_seconds=30), notifier=note)

    async def scenario():
        await wd.tick(40, True)          # trip
        await wd.tick(5, True)           # feed recovered -> re-arm
        return await wd.tick(40, True)   # trips again

    refired = asyncio.run(scenario())
    assert refired is True
    assert len(note.sent) == 2           # two separate outages alerted
    assert wd.triggered is True          # currently in tripped state again


def test_tick_no_position_never_fires():
    note = RecordingNotifier()
    wd = Watchdog(cfg(ws_timeout_seconds=30), notifier=note)
    fired = asyncio.run(wd.tick(9999, has_open_position=False))
    assert fired is False and note.sent == []


# --------------------------------------------------------------------------- #
# Notifiers
# --------------------------------------------------------------------------- #
def test_null_notifier_is_safe():
    asyncio.run(NullNotifier().send("hello"))  # must not raise


def test_telegram_notifier_uses_injected_send():
    calls = []

    async def fake_send(chat_id, text):
        calls.append((chat_id, text))

    n = TelegramNotifier("tok", "123", send_fn=fake_send)
    asyncio.run(n.send("ping"))
    assert calls == [("123", "ping")]


def test_telegram_notifier_swallows_errors():
    async def boom(chat_id, text):
        raise RuntimeError("network down")

    n = TelegramNotifier("tok", "123", send_fn=boom)
    asyncio.run(n.send("ping"))  # must not raise


def test_build_notifier_disabled_returns_null():
    assert isinstance(build_notifier(cfg()), NullNotifier)


def test_build_notifier_enabled_without_env_falls_back(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    c = Config.model_validate({"symbols": ["BTC/USDT"], "telegram": {"enabled": True}})
    assert isinstance(build_notifier(c), NullNotifier)


def test_build_notifier_enabled_with_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    c = Config.model_validate({"symbols": ["BTC/USDT"], "telegram": {"enabled": True}})
    assert isinstance(build_notifier(c), TelegramNotifier)
