# Strategy Testing Lab — Dashboard

Live Streamlit UI for crypto strategy backtesting. Local, offline, no API keys.

## Run

```bash
python3 -m streamlit run dashboard/strategy_testing_lab.py
```

Opens at `http://localhost:8501`

## Features

✅ **4 Built-in Strategies:**
- SMA Crossover (Trend Following)
- EMA Crossover (Trend Following) 
- RSI Mean Reversion
- MACD Momentum

✅ **Data Sources:**
- Synthetic sample data (100% local, instant)
- Upload CSV (Binance, Kraken, etc. exports)
- Fetch public OHLCV from Binance/Bybit (no API key)

✅ **Real Backtesting:**
- Vectorised engine (fast)
- Configurable fees & slippage
- Full performance metrics (Sharpe, Sortino, MDD, Win Rate, Profit Factor)
- Equity curve (Plotly interactive graph)
- Trade log export

✅ **Safe & Offline:**
- No API keys required
- No live trading
- No strategy code execution
- No network calls (except public OHLCV)

## Full Documentation

👉 **[USER_GUIDE.md](../USER_GUIDE.md)** — Complete walkthrough with examples

## Quick Example

1. **Strategy:** SMA Crossover (defaults: fast=20, slow=50)
2. **Data:** "Sample Data" → 2000 bars, 1h → Generate
3. **Config:** Keep defaults (Capital 10k, Fee 0.1%, Slippage 0.05%)
4. **Run:** Click "▶️ Run Backtest"
5. **Result:** See equity curve, metrics, and trade log

**Total time:** ~2 seconds

## CLI Usage (Advanced)

```bash
python -m backtesting.run_strategy \
  --strategy sma_cross \
  --data-source local_csv \
  --csv path/to/ohlcv.csv \
  --start 2023-01-01 --end 2024-01-01 \
  --capital 10000 --fee 0.001 --slippage 0.0005 \
  --param fast=20 --param slow=50
```

See `USER_GUIDE.md` for complete CLI reference.

## Project Structure

```
config/              — settings & paths
data/                — CSV loader, OHLCV fetcher (ccxt), sample generator
strategies/          — BaseStrategy, SMA, EMA, RSI, MACD, registry
backtesting/         — vectorised engine, metrics, CLI runner
dashboard/           — Streamlit UI (this directory)
USER_GUIDE.md        — Complete user documentation
```

## Limitations (MVP)

- Long-only positions (no shorts)
- Single position at a time
- Market orders at candle close only
- Uniform fees (no volume-based discounts)
- No parameter optimization
- No custom strategy code execution (yet)

## Not Implemented (Future)

- Live trading
- Paper trading
- Optimization
- Multi-strategy backtests
- Risk management features
- AI strategy evaluation
