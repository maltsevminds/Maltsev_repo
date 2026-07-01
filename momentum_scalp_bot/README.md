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
| CLI entrypoint | `momentum_scalp/main.py` | ✅ done |
| Backtest engine | `momentum_scalp/backtester.py` | ✅ done |
| Reporting (CSV + equity chart) | `momentum_scalp/reporting.py` | ✅ done |
| Optimizer (grid + walk-forward) | `momentum_scalp/optimizer.py` | ✅ done |
| Dashboard (live monitoring) | `momentum_scalp/dashboard.py` | ✅ done |

## Quick start (one click)

Double-click a launcher in Finder — it creates the venv, installs deps, deploys
the dashboard, opens it in your browser, and starts the bot:

| Shortcut | What it does |
| --- | --- |
| **Start Bot (paper).command** | paper on Binance (live data, virtual balance, no real orders) + dashboard |
| **Start Bot (Bybit paper).command** | paper on **Bybit** (live Bybit data, virtual balance, no keys needed) + dashboard |
| **Start Bot (Bybit testnet).command** | Bybit **testnet** (real orders, fake money) + dashboard |
| **Backtest + report.command** | runs a backtest and opens the equity-curve report |

> First run of a `.command` may be blocked by Gatekeeper — right-click → **Open**
> once, or `chmod +x *.command` in Terminal. Add API keys to `.env` before
> testnet.

Or use `make`:

```bash
make setup            # venv + deps + .env
make paper            # dashboard + bot (paper)
make testnet-bybit    # dashboard + bot (Bybit testnet)
make backtest         # backtest + open report
make dashboard        # just the dashboard server
make test             # run the test suite
```

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

### Exchanges

The bot is exchange-agnostic via ccxt. Ships with two configs:

- `config.yaml` — Binance USDⓈ-M futures (default).
- `config.bybit.yaml` — Bybit USDT-perpetual (linear). **One file, every mode**
  — `--mode` selects it:

  ```bash
  # backtest — downloads Bybit candles for the window in the config (no keys)
  python -m momentum_scalp.main --mode backtest --config config.bybit.yaml --report-dir results
  # paper — live Bybit data, virtual balance, no real orders (no keys)
  python -m momentum_scalp.main --mode paper   --config config.bybit.yaml
  # testnet — real orders on fake money (needs BYBIT_TESTNET_* keys)
  python -m momentum_scalp.main --mode testnet --config config.bybit.yaml
  ```

  Keys (testnet only): `BYBIT_TESTNET_API_KEY` / `_SECRET` from
  https://testnet.bybit.com. Bybit's ccxt symbols carry a settle suffix
  (`BTC/USDT:USDT`). Order placement uses ccxt-unified trigger params
  (`stopLossPrice`/`takeProfitPrice`), so the reduce-only stop and TP1/TP2 work
  identically on both venues.

## Running each mode

All modes are selected with `--mode`; strategy/risk parameters come from
`config.yaml`.

```bash
# 1) Backtest — historical OHLCV (downloaded via ccxt), virtual fills
python -m momentum_scalp.main --mode backtest
#    ...or fully offline from local CSVs named <SYMBOL>_<tf>.csv
#    (e.g. BTCUSDT_5m.csv, BTCUSDT_1h.csv) with a timestamp column:
python -m momentum_scalp.main --mode backtest --data-dir ./data/hist
#    ...and write a report (trades.csv, equity_curve.csv/.html, summary.json):
python -m momentum_scalp.main --mode backtest --data-dir ./data/hist \
       --report-dir ./results
#    open ./results/equity_curve.html in a browser (self-contained SVG, no deps)

# Parameter optimization (search space in config.yaml -> optimize.grid)
python -m momentum_scalp.main --mode backtest --data-dir ./data/hist --optimize grid
python -m momentum_scalp.main --mode backtest --data-dir ./data/hist --optimize walkforward

# 2) Paper — live market data, virtual balance, NO real orders
python -m momentum_scalp.main --mode paper

# 3) Testnet — REAL orders on fake money
python -m momentum_scalp.main --mode testnet                               # Binance
python -m momentum_scalp.main --mode testnet --config config.bybit.yaml           # Bybit

# 4) Live — real money. LOCKED until you set confirm_live: true in config.yaml
python -m momentum_scalp.main --mode live
```

*(The CLI is delivered in a later stage; modes 3–4 need keys in `.env`.)*

## Dashboard

Live monitoring over the bot's SQLite DB — KPIs, equity curve, open positions,
recent trades and the event log. Self-contained HTML (no Streamlit/Flask).

```bash
# auto-refreshing server (reads the DB live while the bot runs)
python -m momentum_scalp.dashboard --db data/bot.sqlite --serve 8787
#   -> open http://localhost:8787

# or write a one-off snapshot
python -m momentum_scalp.dashboard --db data/bot.sqlite --out dashboard.html --mode paper
```

## Tests

```bash
pytest            # from momentum_scalp_bot/
```

Current coverage: `RiskManager` (sizing, leverage cap, daily/weekly limits,
consecutive-stop pause, cluster/position caps), `Config` validation,
`Indicators` (EMA/SMA/RSI/ATR/ADX/Donchian + bias/entry feature bundles) and
`DataFeed` helpers (OHLCV framing, gap detection, paginated history, closed-bar
streaming), `SignalEngine` (every entry gate + stop/target math) and the
`Database` (order idempotency, position lifecycle, equity/events),
`PositionTracker` (TP1/TP2/runner ladder, stop-outs, restart reconcile),
`Executor` (simulated + live fills, idempotency, retry), `Watchdog`
(kill-switch, notifiers), the `BacktestEngine` (end-to-end run + stats) and the
`main` CLI (CSV loading, backtest dispatch, live safety lock). **112 tests.**

## Execution model

- **backtest / paper** — fills are *simulated* by crossing price: the
  PositionTracker's price-driven ladder (`on_bar`) decides when TP1/TP2/stop
  hit, and the Executor books them at the level with configured fee/slippage.
- **testnet / live** — exits are *real resting orders*. On entry the Executor
  places the reduce-only `STOP_MARKET` and reduce-only `TAKE_PROFIT_MARKET`
  TP1/TP2 on the exchange; fills stream back via `watch_orders` and drive the
  ladder event-driven (TP1 fill → move stop to break-even; TP2 fill → start the
  runner; stop fill → close). The runner's trailing stop is ratcheted per
  closed bar and re-placed via cancel+replace. The Watchdog kill-switch
  flattens everything if the feed goes silent with a position open.

## Optimization

`optimize.grid` in `config.yaml` maps dotted config paths to candidate values
(e.g. `strategy.adx_min: [20, 23, 26]`). `--optimize grid` backtests every
combination and ranks by `optimize.metric` (`max_drawdown_pct` is minimized, any
other metric maximized). `--optimize walkforward` rolls a `(train, test)`
window: it grid-searches each in-sample train slice, then evaluates the single
winner on the following **out-of-sample** test slice — the engine's `trade_from`
guard warms indicators on a prefix without letting those bars count as trades —
and chains the OOS folds into a compounded return, the honest generalization
measure.

## Backtest note

Funding-rate history is not replayed in backtest, so the `|funding| < 0.05%`
gate is treated as passing there; it is fully enforced in paper/testnet/live
(fetched live per bar). Backtest fills are simulated at the bar close with the
configured `fee_rate` and `slippage_pct`, and bars from all symbols share one
global timeline so cross-symbol risk limits apply exactly as they would live.

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
