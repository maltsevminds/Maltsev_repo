"""Watchdog — connection health, kill-switch and Telegram alerts.

The core rule: if the market feed goes silent for longer than
``ws_timeout_seconds`` **while a position is open**, trip the kill-switch — an
open position with no live data is the dangerous state (we can't manage the
stop). Tripping fires an ``on_kill`` callback (the bot flattens) and an alert.
The switch re-arms automatically once the feed recovers, so a brief blip that
self-heals before the timeout does nothing.

Notifiers are pluggable: :class:`NullNotifier` (default / tests), or
:class:`TelegramNotifier` when ``telegram.enabled`` and the env vars are set.
Everything is clock-injectable and side-effects go through the notifier + the
SQLite event log, so the whole thing is unit-testable without a network.
"""

from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable, Optional

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

OnKill = Callable[[str], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Notifiers
# --------------------------------------------------------------------------- #
class NullNotifier:
    """Does nothing (used when Telegram is disabled or in tests)."""

    async def send(self, text: str) -> None:  # noqa: D401
        log.debug("notify (null): %s", text)


class TelegramNotifier:
    """Sends alerts to Telegram. ``send_fn`` is injectable for tests; in
    production it lazily uses python-telegram-bot."""

    def __init__(self, token: str, chat_id: str, send_fn=None):
        self.token = token
        self.chat_id = chat_id
        self._send_fn = send_fn

    async def send(self, text: str) -> None:
        try:
            if self._send_fn is not None:
                await self._send_fn(self.chat_id, text)
                return
            from telegram import Bot  # lazy import; optional dependency

            await Bot(self.token).send_message(chat_id=self.chat_id, text=text)
        except Exception as exc:  # noqa: BLE001 - alerts must never crash the bot
            log.warning("telegram send failed: %s", exc)


def build_notifier(config: Config):
    """Return a Telegram notifier if enabled + configured, else a NullNotifier."""
    tg = config.telegram
    if not tg.enabled:
        return NullNotifier()
    token, chat_id = os.getenv(tg.bot_token_env), os.getenv(tg.chat_id_env)
    if not token or not chat_id:
        log.warning(
            "telegram.enabled but %s/%s not set; alerts disabled",
            tg.bot_token_env, tg.chat_id_env,
        )
        return NullNotifier()
    return TelegramNotifier(token, chat_id)


# --------------------------------------------------------------------------- #
# Watchdog
# --------------------------------------------------------------------------- #
class Watchdog:
    def __init__(
        self,
        config: Config,
        db: Optional[Database] = None,
        notifier=None,
        on_kill: Optional[OnKill] = None,
    ):
        self.cfg = config
        self.db = db
        self.notifier = notifier or NullNotifier()
        self.on_kill = on_kill
        self.timeout = config.watchdog.ws_timeout_seconds
        self.triggered = False  # latched so we don't re-fire every tick

    # -- pure decision ------------------------------------------------- #
    def should_kill(self, seconds_since_last: float, has_open_position: bool) -> bool:
        """Kill only when a position is exposed AND the feed is stale."""
        return has_open_position and seconds_since_last > self.timeout

    # -- periodic check ------------------------------------------------ #
    async def tick(self, seconds_since_last: float, has_open_position: bool) -> bool:
        """Call this on a timer. Returns True on the tick that trips the switch.

        Fires ``on_kill`` + an alert exactly once per outage; re-arms when the
        feed is healthy again."""
        if self.should_kill(seconds_since_last, has_open_position):
            if not self.triggered:
                self.triggered = True
                reason = (
                    f"feed silent {seconds_since_last:.0f}s "
                    f"(> {self.timeout}s) with an open position"
                )
                await self.alert(f"🛑 KILL-SWITCH: {reason}", level="CRITICAL",
                                 kind="kill_switch")
                if self.on_kill is not None:
                    await self.on_kill(reason)
                return True
            return False
        # Feed healthy (or nothing at risk): re-arm.
        if seconds_since_last <= self.timeout:
            self.triggered = False
        return False

    # -- alerts -------------------------------------------------------- #
    async def alert(
        self, message: str, *, level: str = "WARNING", kind: str = "alert",
        symbol: Optional[str] = None,
    ) -> None:
        if self.db is not None:
            self.db.log_event(level, kind, message, symbol=symbol)
        await self.notifier.send(message)
