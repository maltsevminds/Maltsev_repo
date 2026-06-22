"""Strategy Testing Lab — local offline Streamlit UI for crypto strategy backtesting."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so backend modules are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Strategy Testing Lab",
    page_icon="⚗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── LAZY BACKEND IMPORTS (show friendly error if missing) ────────────────────
@st.cache_resource(show_spinner=False)
def _load_backend():
    from data.data_manager import DataManager
    from data.sample_generator import generate_sample_ohlcv
    from strategies.registry import STRATEGY_LABELS, get_strategy
    from backtesting.engine import run_backtest
    from backtesting.performance import calculate_metrics
    return DataManager, generate_sample_ohlcv, STRATEGY_LABELS, get_strategy, run_backtest, calculate_metrics

try:
    DataManager, generate_sample_ohlcv, STRATEGY_LABELS, get_strategy, run_backtest, calculate_metrics = _load_backend()
    _backend_ok = True
except Exception as _e:
    _backend_ok = False
    _backend_error = str(_e)

# ─── SESSION STATE ────────────────────────────────────────────────────────────
_defaults: dict = {
    "df": None,
    "data_label": "",
    "backtest_results": None,
    "ui_logs": [],
    "command_preview": "",
    "custom_code": "",
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _log(msg: str) -> None:
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.ui_logs.insert(0, f"[{ts}]  {msg}")
    st.session_state.ui_logs = st.session_state.ui_logs[:80]


# ─── SIDEBAR HELP ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📖 Help & Documentation")

    with st.expander("⚡ Quick Start (2 min)", expanded=True):
        st.markdown("""
        **1. Choose Strategy**
        - Select from 4 built-in strategies
        - Adjust parameters if needed

        **2. Load Data**
        - "Sample Data" for instant testing
        - Or upload CSV / fetch from exchange

        **3. Configure Backtest**
        - Set date range, capital, fees
        - Keep defaults if unsure

        **4. Run & Analyze**
        - Click "▶️ Run Backtest"
        - See equity curve & metrics
        """)

    with st.expander("📊 Metrics Guide"):
        st.markdown("""
        **Total Return** — Overall profit %

        **Max Drawdown** — Worst peak-to-bottom %

        **Sharpe Ratio** — Return per unit risk
        (> 1.0 is good, > 2.0 is excellent)

        **Win Rate** — % of profitable trades
        (> 50% is good)

        **Profit Factor** — Gains / Losses ratio
        (> 1.5 is good, > 2.0 is excellent)
        """)

    with st.expander("🎯 Strategy Descriptions"):
        st.markdown("""
        **SMA Crossover**
        Fast MA crosses slow MA = trend following
        Good for: strong trends

        **EMA Crossover**
        Like SMA but faster reaction
        Good for: mid-range trends

        **RSI Mean Reversion**
        Buy oversold, sell overbought
        Good for: ranging/flat markets

        **MACD Momentum**
        MACD line crosses signal line
        Good for: momentum moves, more signals
        """)

    with st.expander("📁 Data Format"):
        st.markdown("""
        **CSV must have columns:**
        - timestamp
        - open
        - high
        - low
        - close
        - volume

        **Example:**
        ```
        timestamp,open,high,low,close,volume
        2023-01-01 00:00:00,16500,16550,16450,16490,45.2
        2023-01-01 01:00:00,16490,16550,16480,16540,38.9
        ```
        """)

    st.divider()
    st.markdown("**📚 Full Docs**")
    st.markdown("[USER_GUIDE.md](https://github.com/maltsevminds/Maltsev_repo/blob/main/USER_GUIDE.md) — Complete reference")
    st.markdown("[README.md](https://github.com/maltsevminds/Maltsev_repo/blob/main/README.md) — Project overview")
    st.markdown("[GitHub](https://github.com/maltsevminds/Maltsev_repo) — Source code")


# ─── HEADER ───────────────────────────────────────────────────────────────────
st.title("⚗️  Strategy Testing Lab")
st.markdown("*Local offline crypto strategy backtesting cockpit*")

b1, b2, b3, b4, b5 = st.columns(5)
b1.success("MVP — Data + Backtest")
b2.info("Offline Mode")
b3.info("Research Only")
b4.warning("Live Trading Disabled")
b5.warning("No API Keys")

# Quick help expander at top
with st.expander("📖 **Quick Start & Help** — Click to expand", expanded=False):
    col_qs, col_metrics, col_strats = st.columns(3)

    with col_qs:
        st.markdown("**⚡ Quick Start (2 min)**")
        st.markdown("""
        1. **Choose Strategy** — Select from 4 options
        2. **Load Data** — Sample/CSV/Exchange
        3. **Configure** — Set dates & fees
        4. **Run** — Click "▶️ Run Backtest"
        """)

    with col_metrics:
        st.markdown("**📊 Key Metrics**")
        st.markdown("""
        - **Sharpe** > 1.0 = good
        - **Sortino** > 1.0 = good
        - **Win Rate** > 50% = good
        - **Profit Factor** > 1.5 = good
        - **MDD** < 20% = good
        """)

    with col_strats:
        st.markdown("**🎯 Strategies**")
        st.markdown("""
        - **SMA** — Trend, slow
        - **EMA** — Trend, fast
        - **RSI** — Reversion, ranges
        - **MACD** — Momentum, signals
        """)

    st.divider()
    st.markdown("**📚 Full Docs:** [USER_GUIDE.md](https://github.com/maltsevminds/Maltsev_repo) | [README.md](https://github.com/maltsevminds/Maltsev_repo)")

if not _backend_ok:
    st.error(f"⚠️  Backend modules not loaded: {_backend_error}")
    st.info("Run from repo root: `streamlit run dashboard/strategy_testing_lab.py`")
    st.stop()

st.divider()

# ─── THREE-COLUMN LAYOUT ──────────────────────────────────────────────────────
left_col, center_col, right_col = st.columns([1.2, 1.4, 1.1])

# ══════════════════════════════════════════════════════════════════════════════
# LEFT — Strategy Selection + Data Source
# ══════════════════════════════════════════════════════════════════════════════
with left_col:

    # ── Built-in Strategy Selector ────────────────────────────────────────────
    st.subheader("🎯  Strategy")

    strategy_label_to_key = {v: k for k, v in STRATEGY_LABELS.items()}
    selected_label = st.selectbox(
        "Select built-in strategy",
        list(STRATEGY_LABELS.values()),
    )
    selected_key = strategy_label_to_key[selected_label]

    st.caption(
        {
            "sma_cross": "Trend Following — SMA crossover (fast crosses slow).",
            "ema_cross": "Trend Following — EMA crossover, reacts faster than SMA.",
            "rsi": "Mean Reversion — enter on RSI oversold exit, close on overbought exit.",
            "macd": "Momentum — MACD line / signal line crossover.",
        }[selected_key]
    )

    # Dynamic parameter inputs per strategy
    strategy_params: dict = {}
    if selected_key == "sma_cross":
        pc1, pc2 = st.columns(2)
        strategy_params["fast"] = pc1.number_input("Fast MA", 3, 200, 20, step=1)
        strategy_params["slow"] = pc2.number_input("Slow MA", 5, 500, 50, step=1)

    elif selected_key == "ema_cross":
        pc1, pc2 = st.columns(2)
        strategy_params["fast"] = pc1.number_input("Fast EMA", 3, 100, 12, step=1)
        strategy_params["slow"] = pc2.number_input("Slow EMA", 5, 200, 26, step=1)

    elif selected_key == "rsi":
        pc1, pc2, pc3 = st.columns(3)
        strategy_params["period"] = pc1.number_input("Period", 5, 50, 14, step=1)
        strategy_params["oversold"] = float(pc2.number_input("Oversold", 10, 45, 30, step=1))
        strategy_params["overbought"] = float(pc3.number_input("Overbought", 55, 90, 70, step=1))

    elif selected_key == "macd":
        pc1, pc2, pc3 = st.columns(3)
        strategy_params["fast"] = pc1.number_input("Fast EMA", 5, 50, 12, step=1)
        strategy_params["slow"] = pc2.number_input("Slow EMA", 10, 100, 26, step=1)
        strategy_params["signal_period"] = pc3.number_input("Signal", 3, 20, 9, step=1)

    st.divider()

    # ── Custom Strategy Sandbox (display only) ────────────────────────────────
    with st.expander("📝  Custom Strategy Sandbox (display only)", expanded=False):
        st.caption(
            "Upload or paste your strategy code for reference. "
            "Custom code is **not executed** in this version."
        )
        uploaded_file = st.file_uploader("Upload .py file", type=["py"])
        if uploaded_file is not None:
            st.session_state.custom_code = uploaded_file.read().decode("utf-8")
            _log(f"Strategy file uploaded: {uploaded_file.name}")
            st.success(f"Loaded: {uploaded_file.name}")

        st.text_area(
            "Paste strategy code",
            height=160,
            label_visibility="collapsed",
            placeholder="# Paste strategy code here for reference...\n# Not executed.",
            key="custom_code",
        )

    st.divider()

    # ── Data Source Panel ─────────────────────────────────────────────────────
    st.subheader("📡  Data Source")

    data_mode = st.radio(
        "Source",
        ["Sample Data (synthetic)", "Upload CSV", "Fetch from Exchange"],
        label_visibility="collapsed",
    )

    df_loaded: pd.DataFrame | None = None

    if data_mode == "Sample Data (synthetic)":
        n_bars = st.slider("Number of bars", 500, 5000, 2000, step=100)
        sample_freq = st.selectbox("Timeframe", ["1h", "4h", "1d", "15m"], index=0)
        if st.button("⚡  Generate Sample Data", use_container_width=True):
            df_loaded = generate_sample_ohlcv(
                n_bars=n_bars, start="2023-01-01", freq=sample_freq
            )
            st.session_state.df = df_loaded
            st.session_state.data_label = f"Sample BTC/USDT {sample_freq} — {n_bars} bars"
            st.session_state.backtest_results = None
            _log(f"Sample data generated — {n_bars} bars  tf={sample_freq}")

    elif data_mode == "Upload CSV":
        st.caption(
            "CSV must have columns: timestamp, open, high, low, close, volume"
        )
        csv_file = st.file_uploader("Upload OHLCV CSV", type=["csv"])
        if csv_file is not None:
            try:
                dm = DataManager()
                df_loaded = dm.load_from_csv(csv_file.read())
                st.session_state.df = df_loaded
                st.session_state.data_label = f"CSV: {csv_file.name}  ({len(df_loaded)} bars)"
                st.session_state.backtest_results = None
                _log(f"CSV loaded: {csv_file.name}  bars={len(df_loaded)}")
                st.success(f"Loaded {len(df_loaded):,} bars")
            except Exception as exc:
                st.error(f"CSV error: {exc}")

    else:  # Fetch from Exchange
        exc_sel = st.selectbox("Exchange", ["binance", "bybit"])
        sym_sel = st.text_input("Symbol", value="BTC/USDT")
        tf_sel = st.selectbox("Timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"], index=3)
        limit_sel = st.slider("Bars to fetch", 100, 1000, 500)
        st.info("📌 Public OHLCV only — no API key required")
        if st.button("📥  Fetch OHLCV", use_container_width=True):
            with st.spinner(f"Fetching from {exc_sel}…"):
                try:
                    dm = DataManager()
                    df_loaded = dm.load_from_exchange(exc_sel, sym_sel, tf_sel, limit=limit_sel)
                    st.session_state.df = df_loaded
                    st.session_state.data_label = (
                        f"{exc_sel.title()} {sym_sel} {tf_sel} — {len(df_loaded)} bars"
                    )
                    st.session_state.backtest_results = None
                    _log(f"Fetched {len(df_loaded)} bars from {exc_sel} {sym_sel} {tf_sel}")
                    st.success(f"Fetched {len(df_loaded):,} bars")
                except Exception as exc:
                    st.error(f"Fetch error: {exc}")

    # Data status
    if st.session_state.df is not None:
        _df = st.session_state.df
        st.success(
            f"✅  {st.session_state.data_label}\n\n"
            f"Range: {_df.index[0].date()} → {_df.index[-1].date()}"
        )
    else:
        st.info("⏳  No data loaded — select a source above")


# ══════════════════════════════════════════════════════════════════════════════
# CENTER — Backtest Config + Execution
# ══════════════════════════════════════════════════════════════════════════════
with center_col:

    st.subheader("⚙️  Backtest Configuration")

    dc1, dc2 = st.columns(2)
    with dc1:
        start_date = st.date_input("Start Date", value=pd.Timestamp("2023-01-01"))
    with dc2:
        end_date = st.date_input("End Date", value=pd.Timestamp("2024-01-01"))

    cc1, cc2 = st.columns(2)
    with cc1:
        initial_capital = st.number_input("Initial Capital (USDT)", 100, 10_000_000, 10_000, step=500)
    with cc2:
        fee_pct = st.number_input("Fee (%)", 0.0, 5.0, 0.1, step=0.01, format="%.3f")

    sc1, sc2 = st.columns(2)
    with sc1:
        slippage_pct = st.number_input("Slippage (%)", 0.0, 5.0, 0.05, step=0.01, format="%.3f")
    with sc2:
        st.number_input("Risk per Trade (%)", 0.1, 100.0, 1.0, step=0.1, format="%.1f")

    st.selectbox("Position Sizing", ["Percent of Equity", "Fixed Size", "Risk-based"])
    st.text_input("Commission Model", value="Taker/Maker flat fee", disabled=True)
    st.text_input("Execution Assumptions", value="Market orders at candle close", disabled=True)

    st.divider()

    # ── Execution Buttons ─────────────────────────────────────────────────────
    st.subheader("🚀  Execution")

    prepare_btn = st.button("📋  Prepare Command Preview", use_container_width=True)

    data_ready = st.session_state.df is not None
    run_btn = st.button(
        "▶️  Run Backtest",
        use_container_width=True,
        disabled=not data_ready,
        type="primary",
        help="Load data first, then run." if not data_ready else "Click to run backtest",
    )

    st.button("📄  Paper Trading — Disabled", disabled=True, use_container_width=True)
    st.markdown(
        """
        <div style="
            background:#2a0a0a;border:1px solid #7a0000;border-radius:6px;
            padding:10px 16px;text-align:center;color:#ff6666;font-weight:600;
        ">
            🔒  Live Trading — LOCKED / DISABLED
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Prepare Command ───────────────────────────────────────────────────────
    if prepare_btn:
        fee_frac = fee_pct / 100.0
        slip_frac = slippage_pct / 100.0
        cmd = (
            f"python -m backtesting.run_strategy \\\n"
            f"  --strategy {selected_key} \\\n"
            f"  --data-source local_csv \\\n"
            f"  --csv data/historical/btc_usdt_1h.csv \\\n"
            f"  --start {start_date} \\\n"
            f"  --end {end_date} \\\n"
            f"  --capital {initial_capital} \\\n"
            f"  --fee {fee_frac} \\\n"
            f"  --slippage {slip_frac}"
        )
        for k, v in strategy_params.items():
            cmd += f" \\\n  --param {k}={v}"
        st.session_state.command_preview = cmd
        _log("Command preview generated")

    # ── Run Backtest ──────────────────────────────────────────────────────────
    if run_btn and data_ready:
        with st.spinner("Running backtest…"):
            try:
                df_bt = st.session_state.df.copy()
                df_bt = df_bt[
                    (df_bt.index >= pd.Timestamp(start_date))
                    & (df_bt.index <= pd.Timestamp(end_date))
                ]
                if df_bt.empty:
                    st.error("No data in selected date range.")
                else:
                    strategy = get_strategy(selected_key, strategy_params)
                    signals = strategy.generate_signals(df_bt)

                    equity_curve, trades = run_backtest(
                        df_bt,
                        signals,
                        initial_capital=float(initial_capital),
                        fee=fee_pct / 100.0,
                        slippage=slippage_pct / 100.0,
                    )

                    metrics = calculate_metrics(equity_curve, trades, float(initial_capital))

                    st.session_state.backtest_results = {
                        "equity_curve": equity_curve,
                        "trades": trades,
                        "metrics": metrics,
                        "strategy_label": selected_label,
                        "strategy_params": strategy_params,
                        "bars": len(df_bt),
                    }
                    _log(
                        f"Backtest complete — {selected_label}  "
                        f"trades={metrics['total_trades']}  "
                        f"return={metrics['total_return']}%"
                    )
            except Exception as exc:
                st.error(f"Backtest error: {exc}")
                _log(f"Backtest error: {exc}")

    # ── Terminal Preview ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("💻  Terminal Preview")
    st.caption("Text only — no subprocess is called")

    if st.session_state.command_preview:
        st.code(st.session_state.command_preview, language="bash")
    else:
        st.markdown(
            """
            <div style="
                background:#0d1117;border:1px solid #30363d;border-radius:6px;
                padding:14px;font-family:monospace;font-size:12px;
                color:#8b949e;line-height:1.7;
            ">
                $ <span style="color:#3fb950">awaiting configuration…</span><br>
                &gt; click <strong style="color:#e6edf3">📋 Prepare Command Preview</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# RIGHT — Results + Logs
# ══════════════════════════════════════════════════════════════════════════════
with right_col:

    st.subheader("📊  Results")

    res = st.session_state.backtest_results
    if res:
        m = res["metrics"]
        st.caption(
            f"{res['strategy_label']}  |  {res['bars']:,} bars  |  "
            f"params: {res['strategy_params']}"
        )

        METRIC_DEFS = [
            ("Total Return", f"{m['total_return']:+.2f}%",
             "green" if m["total_return"] >= 0 else "red"),
            ("Max Drawdown", f"{m['max_drawdown']:.2f}%", "red"),
            ("Sharpe Ratio", str(m["sharpe_ratio"]),
             "green" if m["sharpe_ratio"] >= 1 else "orange"),
            ("Sortino Ratio", str(m["sortino_ratio"]),
             "green" if m["sortino_ratio"] >= 1 else "orange"),
            ("Profit Factor", str(m["profit_factor"]),
             "green" if str(m["profit_factor"]) == "∞" or float(str(m["profit_factor"]).replace("∞", "99")) >= 1.5 else "orange"),
            ("Win Rate", f"{m['win_rate']:.1f}%",
             "green" if m["win_rate"] >= 50 else "orange"),
            ("Total Trades", str(m["total_trades"]), "default"),
            ("Avg Trade Ret.", f"{m['avg_trade_return']:+.2f}%",
             "green" if m["avg_trade_return"] >= 0 else "red"),
            ("Final Equity", f"${m['final_equity']:,.2f}", "default"),
        ]

        for label, value, colour in METRIC_DEFS:
            mc, vc = st.columns([1.5, 1])
            mc.markdown(f"<small><b>{label}</b></small>", unsafe_allow_html=True)
            colour_map = {"green": "#4caf50", "red": "#f44336",
                          "orange": "#ff9800", "default": "#aaaaaa"}
            hex_c = colour_map.get(colour, "#aaaaaa")
            vc.markdown(
                f"<span style='font-family:monospace;color:{hex_c};font-size:13px;'>"
                f"{value}</span>",
                unsafe_allow_html=True,
            )
    else:
        PENDING_METRICS = [
            "Total Return", "Max Drawdown", "Sharpe Ratio", "Sortino Ratio",
            "Profit Factor", "Win Rate", "Total Trades", "Avg Trade Return",
        ]
        for label in PENDING_METRICS:
            mc, vc = st.columns([1.5, 1])
            mc.markdown(f"<small><b>{label}</b></small>", unsafe_allow_html=True)
            vc.markdown(
                "<code style='color:#444;font-size:11px;'>Pending</code>",
                unsafe_allow_html=True,
            )

    st.divider()

    # ── UI Logs ───────────────────────────────────────────────────────────────
    st.subheader("📋  UI Logs")
    st.caption("Session logs only — no exchange or API logs")

    if st.button("🗑  Clear Logs", use_container_width=True):
        st.session_state.ui_logs = []
        st.rerun()

    if st.session_state.ui_logs:
        st.text_area(
            "logs",
            value="\n".join(st.session_state.ui_logs),
            height=220,
            label_visibility="collapsed",
            disabled=True,
        )
    else:
        st.markdown(
            "<small style='color:#555;'>No logs yet.</small>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# FULL-WIDTH RESULTS (shown only after a successful backtest)
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.backtest_results:
    res = st.session_state.backtest_results
    equity_curve: pd.DataFrame = res["equity_curve"]
    trades = res["trades"]

    st.divider()
    st.subheader("📈  Equity Curve")

    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=equity_curve.index,
                y=equity_curve["equity"],
                mode="lines",
                name="Equity",
                line=dict(color="#00e676", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(0,230,118,0.06)",
            )
        )
        fig.add_hline(
            y=res["metrics"]["final_equity"] * 0 + float(equity_curve["equity"].iloc[0]),
            line_dash="dot",
            line_color="#555",
            annotation_text="Initial Capital",
        )
        fig.update_layout(
            template="plotly_dark",
            height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title=None,
            yaxis_title="Portfolio (USDT)",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.line_chart(equity_curve["equity"])

    # ── Trade Log ─────────────────────────────────────────────────────────────
    if trades:
        with st.expander(f"📄  Trade Log  ({len(trades)} trades)", expanded=False):
            trade_rows = []
            for t in trades:
                trade_rows.append(
                    {
                        "Entry Time": str(t.entry_ts)[:16],
                        "Exit Time": str(t.exit_ts)[:16] if t.exit_ts else "Open",
                        "Entry Price": f"{t.entry_price:,.2f}",
                        "Exit Price": f"{t.exit_price:,.2f}" if t.exit_price else "—",
                        "PnL (USDT)": f"{t.pnl:+.2f}",
                        "Return (%)": f"{t.pnl_pct:+.2f}%",
                    }
                )
            st.dataframe(
                pd.DataFrame(trade_rows),
                use_container_width=True,
                hide_index=True,
            )


# ─── SAFETY BANNER ────────────────────────────────────────────────────────────
st.divider()
st.subheader("🛡️  Safety / Mode Status")

sm_col, ss_col, sf_col = st.columns(3)
with sm_col:
    st.markdown(
        "**Current Mode**\n"
        "- Offline UI shell\n"
        "- ✅ Backtest engine: **active**\n"
        "- 🔒 API execution: **disabled**\n"
        "- 🔒 Live trading: **disabled**"
    )
with ss_col:
    st.markdown(
        "**Available now**\n"
        "- ✅ Generate synthetic data\n"
        "- ✅ Upload CSV data\n"
        "- ✅ Fetch public OHLCV (ccxt)\n"
        "- ✅ Run built-in strategy backtests\n"
        "- ✅ View equity curve + trade log\n"
        "- ✅ Prepare CLI command preview"
    )
with sf_col:
    st.markdown(
        "**Future only (not implemented)**\n"
        "- 🔜 Custom strategy execution\n"
        "- 🔜 Multi-strategy comparison\n"
        "- 🔜 Parameter optimisation\n"
        "- 🔜 Paper trading\n"
        "- 🔜 Live trading\n"
        "- 🔜 AI strategy evaluation"
    )
