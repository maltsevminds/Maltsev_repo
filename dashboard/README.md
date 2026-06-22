# Dashboard

## Strategy Testing Lab

Local offline Streamlit UI for future crypto strategy backtesting.

### Run locally

```bash
# from the repo root
streamlit run dashboard/strategy_testing_lab.py
```

Or with an explicit port:

```bash
streamlit run dashboard/strategy_testing_lab.py --server.port 8501
```

### Install dependency

```bash
pip install streamlit
```

### What this UI does (MVP)

- Upload or paste a Python strategy file (display only — never executed)
- Configure market, timeframe, exchange/data source
- Set backtest parameters (dates, capital, fees, slippage)
- Generate a terminal command preview (text only — never run)
- View placeholder result cards
- View local session UI logs

### What this UI does NOT do

- Execute any strategy code
- Run real backtests
- Connect to Bybit or Binance
- Use API keys
- Place orders
- Perform any network calls
