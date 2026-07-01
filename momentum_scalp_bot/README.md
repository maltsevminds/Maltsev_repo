# Momentum-Scalp Bot — Binance USDⓈ-M Futures (perpetual)

Automated momentum-scalp trading bot for Binance USDⓈ-M perpetual futures.
Python 3.11, `ccxt` (REST + WebSocket), pandas/numpy/pandas-ta, pydantic config,
SQLite logging, pytest, Telegram alerts.

> ⚠️ **Trading futures with leverage can lose you money fast.** Live trading is
> hard-locked (`confirm_live: false`). The mandatory progression is
> **backtest → paper → testnet → live**. Do not skip steps.

## Build status (staged delivery)

| Module | File | Status |
| --- | --- | --- |
| Config (pydantic + YAML) | `momentum_scalp/config.py` | ✅ done |
| RiskManager (sizing + gate) | `momentum_scalp/risk_manager.py` | ✅ done |
| DataFeed (ccxt.pro WS) | `momentum_scalp/data_feed.py` | ✅ done |
| Indicators | `momentum_scalp/indicators.py` | ✅ done |
| SignalEngine | `momentum_scalp/signal_engine.py` | ✅ done |
| Executor (idempotent+retry) | `momentum_scalp/executor.py` | ✅ done |
| PositionTracker (reconcile) | `momentum_scalp/position_tracker.py` | ✅ done |
| Watchdog (kill-switch/TG) | `momentum_scalp/watchdog.py` | ✅ done |
| Logger/DB (SQLite) | `momentum_scalp/db.py` | ✅ done |
| CLI entrypoint | `momentum_scalp/main.py` | ⏳ stub |

## Install (macOS)

```bash
# 1. Python 3.11 (Homebrew)
brew install python@3.11

# 2. Clone & enter this folder
cd momentum_scalp_bot

# 3. Virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# 4. Dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

## Where to put your keys

Secrets never live in `config.yaml` — the YAML only names the env vars.
Copy the template and fill it in:

```bash
cp .env.example .env
# then edit .env:
#   BINANCE_TESTNET_API_KEY / _SECRET   -> from https://testnet.binancefuture.com
#   BINANCE_API_KEY / _SECRET           -> live keys (only for mode=live)
#   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (optional alerts)
```

`.env` is git-ignored. Never commit real keys.

## Running each mode

All modes are selected with `--mode`; strategy/risk parameters come from
`config.yaml`.

```bash
# 1) Backtest — historical OHLCV (downloaded via ccxt), virtual fills
python -m momentum_scalp.main --mode backtest

# 2) Paper — live market data, virtual balance, NO real orders
python -m momentum_scalp.main --mode paper

# 3) Testnet — Binance Futures testnet, REAL orders on fake money
python -m momentum_scalp.main --mode testnet

# 4) Live — real money. LOCKED until you set confirm_live: true in config.yaml
python -m momentum_scalp.main --mode live
```

*(The CLI is delivered in a later stage; modes 3–4 need keys in `.env`.)*

## Tests

```bash
pytest            # from momentum_scalp_bot/
```

Current coverage: `RiskManager` (sizing, leverage cap, daily/weekly limits,
consecutive-stop pause, cluster/position caps), `Config` validation,
`Indicators` (EMA/SMA/RSI/ATR/ADX/Donchian + bias/entry feature bundles) and
`DataFeed` helpers (OHLCV framing, gap detection, paginated history, closed-bar
streaming), `SignalEngine` (every entry gate + stop/target math) and the
`Database` (order idempotency, position lifecycle, equity/events) and the
`PositionTracker` (TP1/TP2/runner ladder, stop-outs, restart reconcile).

## Strategy summary

- **Pairs:** BTC, ETH, SOL, BNB, XRP, DOGE (USDT perps). Leverage cap 20×.
- **Bias:** 1h close vs EMA200 (long only above, short only below).
- **Entry (5m):** Donchian(20) breakout + volume > 1.5× SMA20 + ADX(14) > 23
  + RSI(14) in 58–78 (long) / 22–42 (short) + `|funding| < 0.05%`.
- **Stop:** entry ∓ 1.2 × ATR(14, 5m), placed immediately as a reduce-only
  `STOP_MARKET`.
- **Sizing:** `risk_usd = equity × 1.5%`, `notional = risk_usd / stop_pct`,
  `leverage = min(notional/equity, 20)`.
- **Targets:** TP1 +1R close 50% (stop → break-even); TP2 +2R close 30%;
  runner 20% trails 1.5× ATR.
- **Risk gate:** daily −5% → halt to next UTC day; weekly −14% → flat + off till
  Monday; 2 stops in a row → 90-min pause; weekly drawdown > 7% → risk cut to 1%.
  No averaging down. Max 3 positions; BTC/ETH/SOL cluster capped at 2.
