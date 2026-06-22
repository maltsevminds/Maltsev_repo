# Maltsev Trading System — Strategy Testing Lab

Local cryptoasset trading research & backtesting platform. Offline, safe, no API keys.

**Status:** MVP (Data + Backtest engine fully functional)

---

## 🎯 What This Is

A complete system for testing crypto trading strategies **before risking real money**.

```
Data → Strategies → Backtest Engine → Performance Metrics → UI Results
```

**4 built-in strategies ready to test:**
- SMA Crossover (Trend Following)
- EMA Crossover (Trend Following)
- RSI Mean Reversion
- MACD Momentum

**3 data sources:**
- Synthetic sample data (100% offline, instant)
- Upload CSV (Binance, Kraken, other exchanges)
- Public OHLCV from Binance/Bybit (no API key needed)

**Full metrics:**
- Total Return, Max Drawdown
- Sharpe Ratio, Sortino Ratio
- Profit Factor, Win Rate
- Equity curve (interactive)
- Trade-by-trade log

---

## 🚀 Quick Start

### Installation

```bash
git clone https://github.com/maltsevminds/Maltsev_repo.git
cd Maltsev_repo
python3 -m pip install -r requirements.txt
```

### Run the UI

```bash
python3 -m streamlit run dashboard/strategy_testing_lab.py
```

Opens at: `http://localhost:8501`

### Test in 2 minutes

1. **Strategy:** SMA Crossover (defaults: fast=20, slow=50)
2. **Data:** "Sample Data" → 2000 bars, 1h → Generate
3. **Config:** Defaults (Capital 10k, Fee 0.1%, Slippage 0.05%)
4. **Run:** Click "▶️ Run Backtest"
5. **See:** Equity curve, metrics, trade log

---

## 📖 Full Documentation

- **[USER_GUIDE.md](USER_GUIDE.md)** — Complete walkthrough with examples
- **[dashboard/README.md](dashboard/README.md)** — UI reference

---

## 🏗️ Project Structure

```
Maltsev_repo/
├── config/                    # Settings & paths
│   ├── __init__.py
│   └── settings.py           # Defaults: fees, capital, timeframes
│
├── data/                      # Data handling
│   ├── csv_loader.py         # Load from CSV
│   ├── fetcher.py            # Fetch from Binance/Bybit (ccxt, no key)
│   ├── data_manager.py       # Unified interface
│   └── sample_generator.py   # Synthetic OHLCV generator
│
├── strategies/                # Strategy implementations
│   ├── base.py               # BaseStrategy abstract class
│   ├── sma_cross.py          # SMA Crossover
│   ├── ema_cross.py          # EMA Crossover
│   ├── rsi_strategy.py       # RSI Mean Reversion
│   ├── macd_strategy.py      # MACD Momentum
│   └── registry.py           # Strategy registry & factory
│
├── backtesting/               # Backtest engine
│   ├── engine.py             # Vectorised single-asset long-only engine
│   ├── performance.py        # Metrics calculator
│   └── run_strategy.py       # CLI entry point
│
├── dashboard/                 # Streamlit UI
│   └── strategy_testing_lab.py  # Main app (800+ lines)
│
├── requirements.txt           # Dependencies
├── USER_GUIDE.md             # Complete documentation
└── README.md                 # This file
```

---

## 🎮 Usage Examples

### Example 1: Quick Test with Sample Data

```bash
# Via UI
1. Open dashboard
2. Strategy: SMA Crossover (defaults)
3. Data: Sample 2000 bars, 1h
4. Run Backtest
# Result in 2 seconds
```

### Example 2: Test Your CSV

```bash
# Prepare: export OHLCV from your exchange
# Columns: timestamp, open, high, low, close, volume

1. Open dashboard
2. Strategy: MACD Momentum
3. Data: Upload CSV
4. Configure dates & fees
5. Run Backtest
```

### Example 3: CLI Batch Test

```bash
python -m backtesting.run_strategy \
  --strategy ema_cross \
  --data-source local_csv \
  --csv historical_data.csv \
  --start 2023-01-01 --end 2024-01-01 \
  --capital 10000 --fee 0.001 --slippage 0.0005 \
  --param fast=12 --param slow=26
```

Output:
```
[backtest] data-source=local_csv  market=BTC/USDT  tf=1h
[backtest] bars=8760  range=2023-01-01 → 2024-01-01
[backtest] strategy=ema_cross(fast=12, slow=26)
[backtest] signals — entries=45  exits=45

==========================================
  BACKTEST RESULTS
==========================================
  Total Return (%)             +18.24
  Max Drawdown (%)             -8.50
  Sharpe Ratio                 0.845
  Sortino Ratio                1.230
  Profit Factor                1.85
  Win Rate (%)                 62.22
  Total Trades                 45
  Avg Trade Return (%)         +0.405
  Final Equity (USDT)          11824.00
==========================================
```

---

## 📊 Backtest Engine

### Assumptions

- **Position:** Long only (buy & hold until exit signal)
- **Execution:** Market order at candle close
- **Sizing:** 100% of available capital per trade
- **Fees:** Apply to both entry & exit
- **Slippage:** Applied to execution price
- **Timeframe:** Any (1m, 5m, 1h, 1d, etc.)

### Limitations (MVP)

- No shorts (short selling)
- No multi-position (only 1 open at a time)
- No margin / leverage
- No limit orders
- No commission tiers
- No taxes / P&L tracking across positions

### Metrics

| Metric | Formula | Interpretation |
|--------|---------|-----------------|
| **Total Return** | (Final - Initial) / Initial × 100 | Overall profit % |
| **Max Drawdown** | Min(Equity - Peak) / Peak × 100 | Worst peak-to-trough % |
| **Sharpe Ratio** | Mean(daily_returns) / Std(daily_returns) × √252 | Return per unit risk (annualised) |
| **Sortino Ratio** | Mean(daily_returns) / Std(downside) × √252 | Return per unit downside risk |
| **Profit Factor** | Sum(winning trades) / Sum(losing trades) | Ratio of gross profit to loss |
| **Win Rate** | # Winning trades / Total trades × 100 | % of profitable trades |

---

## 🛡️ Safety & Limitations

### What It Does NOT Do

- ❌ Execute real trades (no live trading)
- ❌ Run on paper trading (no simulation of real market)
- ❌ Execute uploaded strategy code
- ❌ Connect to exchange private APIs
- ❌ Store API keys
- ❌ Place orders
- ❌ Require API keys

### What It DOES Do

- ✅ Load historical OHLCV data (CSV or public API)
- ✅ Run vectorised backtests (fast)
- ✅ Calculate realistic performance metrics
- ✅ Generate trade logs & equity curves
- ✅ Prepare CLI commands for reproducibility

### Disclaimer

**Backtest results do NOT guarantee future performance.** Past performance ≠ future results.

- 📈 Backtests can have look-ahead bias
- 📉 Market conditions change
- ⚠️ Slippage & fees are estimates only
- 🔴 Use this for research & learning, not real trading without extensive stress-testing

---

## 🔌 Technologies

- **Python 3.8+**
- **Streamlit** — Interactive web UI
- **Pandas & NumPy** — Data processing
- **Plotly** — Interactive charts
- **ccxt** — Exchange data fetching (public API only)

---

## 🚀 Future Roadmap

**Phase 2 (Planned):**
- Parameter optimization (grid search, Bayesian)
- Multi-strategy comparison
- Custom strategy code execution (sandboxed)
- Multi-asset portfolios

**Phase 3 (Planned):**
- Paper trading (live data, simulated orders)
- Risk management & position sizing
- AI-powered strategy evaluation

**Phase 4 (Future):**
- Live trading integration (with strict safeguards)
- Real-time alerts
- Optimization via ML

---

## 💡 Tips for Best Results

1. **Start with sample data** — test your workflow before loading real data
2. **Use realistic parameters** — Binance: 0.1% fee, 0.05% slippage
3. **Test long periods** — 1+ years to see multiple market cycles
4. **Compare strategies** — Same data, different strategies
5. **Vary parameters** — Find what works best for YOUR strategy
6. **Check metrics holistically** — Don't just maximize return

---

## 📞 Support & Contributing

- **Issues:** GitHub Issues
- **Questions:** See USER_GUIDE.md

---

## 📄 License

MIT — Open source, educational use only.

**NOT FOR PRODUCTION TRADING WITHOUT EXTENSIVE TESTING & RISK REVIEW.**

---

## 👨‍💻 Author

Built by Maltsev for crypto trading research.

**Last Updated:** 2026-06-22

---

## 📚 References

### Learning Resources
- [Backtest.py](https://kernc.github.io/backtesting.py/) — Python backtesting library
- [VectorBT](https://polaarity.github.io/vectorbt/) — Vectorised backtesting
- [QuantConnect Docs](https://www.quantconnect.com/docs/v2/) — Strategy development

### Market Data
- [Binance API](https://binance-docs.github.io/apidocs/) — OHLCV data
- [Bybit API](https://bybit-exchange.github.io/docs/) — Public market data
- [ccxt](https://docs.ccxt.com/) — Unified exchange interface

---

**Start testing your strategies now:**

```bash
python3 -m streamlit run dashboard/strategy_testing_lab.py
```
