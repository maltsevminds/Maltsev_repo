import streamlit as st
from datetime import date

# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Strategy Testing Lab",
    page_icon="⚗️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── SESSION STATE INIT ───────────────────────────────────────────────────────
_defaults = {
    "code_textarea": "",
    "ui_logs": [],
    "command_preview": "",
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _add_log(message: str) -> None:
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.ui_logs.insert(0, f"[{ts}]  {message}")
    st.session_state.ui_logs = st.session_state.ui_logs[:50]


# ─── HEADER ───────────────────────────────────────────────────────────────────
st.title("⚗️  Strategy Testing Lab")
st.markdown("*Local offline interface for future crypto strategy backtesting*")

b1, b2, b3, b4, b5 = st.columns(5)
b1.success("MVP — Data + Backtest")
b2.info("Offline Mode")
b3.info("Research Only")
b4.warning("Live Trading Disabled")
b5.warning("No API Execution")

st.divider()

# ─── THREE-COLUMN LAYOUT ──────────────────────────────────────────────────────
left_col, center_col, right_col = st.columns([1.2, 1.4, 1.0])

# ══════════════════════════════════════════════════════════════════════════════
# LEFT — Strategy Sandbox + Data Source
# ══════════════════════════════════════════════════════════════════════════════
with left_col:

    # ── Strategy Sandbox ──────────────────────────────────────────────────────
    st.subheader("🧪 Strategy Sandbox")

    strategy_name = st.text_input("Strategy Name", value="my_strategy")
    market = st.text_input("Market", value="BTC/USDT")
    timeframe = st.selectbox(
        "Timeframe", ["1m", "5m", "15m", "1h", "4h", "1d"], index=3
    )
    strategy_type = st.selectbox(
        "Strategy Type",
        ["Trend Following", "Mean Reversion", "Breakout", "Momentum", "Custom"],
    )

    st.markdown("**Upload Strategy File (.py)**")
    uploaded_file = st.file_uploader(
        "Upload .py", type=["py"], label_visibility="collapsed"
    )
    if uploaded_file is not None:
        st.session_state.code_textarea = uploaded_file.read().decode("utf-8")
        _add_log(f"Strategy file uploaded: {uploaded_file.name}")
        st.success(f"Loaded: {uploaded_file.name}")

    st.markdown("**Paste Strategy Code**")
    st.text_area(
        "Code",
        height=190,
        label_visibility="collapsed",
        placeholder=(
            "# Paste your strategy code here...\n"
            "# strategy_name = 'my_strategy'\n"
            "# def entry_condition(df): ...\n"
            "# def exit_condition(df): ...\n"
            "# stop_loss = 0.02"
        ),
        key="code_textarea",
    )

    if st.session_state.code_textarea.strip():
        with st.expander("Code Preview", expanded=False):
            st.code(st.session_state.code_textarea, language="python")

    st.divider()

    # ── Data Source Panel ─────────────────────────────────────────────────────
    st.subheader("📡 Data Source")

    data_source_mode = st.radio(
        "Data Source Mode",
        [
            "Local CSV Backtest",
            "Bybit Public OHLCV API — future",
            "Binance Public OHLCV API — future",
        ],
    )
    if "future" in data_source_mode:
        st.warning("⚠️  Future: public OHLCV data loading only. Not implemented yet.")
        st.error("🔒  Disabled: private API / order execution")

    exchange = st.selectbox("Exchange / Data Source", ["Local CSV", "Bybit", "Binance"])
    if exchange in ["Bybit", "Binance"]:
        st.caption(
            "📌 Future: public OHLCV only  |  🔒 Disabled: private API / orders"
        )

    symbol = st.text_input("Symbol / Pair", value="BTC/USDT")
    data_timeframe = st.selectbox(
        "Data Timeframe",
        ["1m", "5m", "15m", "1h", "4h", "1d"],
        index=3,
        key="data_tf",
    )

    st.markdown("**CSV Data File** *(Local CSV mode)*")
    csv_file = st.file_uploader("Upload CSV", type=["csv"], label_visibility="collapsed")
    if csv_file:
        _add_log(f"CSV file selected: {csv_file.name}")
        st.success(f"CSV selected: {csv_file.name}  *(not yet processed)*")

    st.info("Data status: ⏳  No data loaded — future backtest will load data here")


# ══════════════════════════════════════════════════════════════════════════════
# CENTER — Backtest Config + Execution Preview + Terminal
# ══════════════════════════════════════════════════════════════════════════════
with center_col:

    # ── Backtest Configuration ────────────────────────────────────────────────
    st.subheader("⚙️  Backtest Configuration")

    dc1, dc2 = st.columns(2)
    with dc1:
        start_date = st.date_input("Start Date", value=date(2023, 1, 1))
    with dc2:
        end_date = st.date_input("End Date", value=date(2024, 1, 1))

    cc1, cc2 = st.columns(2)
    with cc1:
        initial_capital = st.number_input(
            "Initial Capital (USDT)", min_value=100, value=10_000, step=100
        )
    with cc2:
        fee_pct = st.number_input(
            "Fee (%)", min_value=0.0, max_value=5.0, value=0.1, step=0.01, format="%.2f"
        )

    sc1, sc2 = st.columns(2)
    with sc1:
        slippage_pct = st.number_input(
            "Slippage (%)",
            min_value=0.0,
            max_value=5.0,
            value=0.05,
            step=0.01,
            format="%.2f",
        )
    with sc2:
        risk_per_trade = st.number_input(
            "Risk per Trade (%)",
            min_value=0.1,
            max_value=100.0,
            value=1.0,
            step=0.1,
            format="%.1f",
        )

    position_sizing = st.selectbox(
        "Position Sizing Mode",
        ["Fixed Size", "Percent of Equity", "Risk-based"],
    )
    st.text_input("Commission Model", value="Taker/Maker flat fee", disabled=True)
    st.text_input(
        "Execution Assumptions", value="Market orders at candle close", disabled=True
    )

    st.divider()

    # ── Execution Preview Panel ───────────────────────────────────────────────
    st.subheader("🚀  Execution Preview")

    ec1, ec2 = st.columns(2)
    save_draft_btn = ec1.button("💾  Save Strategy Draft", use_container_width=True)
    validate_btn = ec2.button("🔍  Validate Structure", use_container_width=True)
    prepare_btn = st.button("📋  Prepare Backtest Command", use_container_width=True)

    st.button(
        "▶️  Run Backtest — Disabled",
        disabled=True,
        use_container_width=True,
        help="Future: will trigger the backtesting pipeline — not yet connected",
    )
    st.button(
        "📄  Paper Trading — Disabled",
        disabled=True,
        use_container_width=True,
        help="Not implemented — future phase",
    )
    st.markdown(
        """
        <div style="
            background:#2a0a0a;border:1px solid #7a0000;border-radius:6px;
            padding:10px 16px;text-align:center;
            color:#ff6666;font-weight:600;margin-top:4px;
        ">
            🔒  Live Trading — LOCKED / DISABLED
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Button handlers ───────────────────────────────────────────────────────
    if save_draft_btn:
        _add_log(
            f"Strategy draft saved: '{strategy_name}'  "
            f"[{strategy_type} | {market} | {timeframe}]"
        )
        st.success(f"Draft saved to session: **{strategy_name}**")

    if validate_btn:
        code = st.session_state.code_textarea
        if not code.strip():
            st.warning("No code to validate — paste or upload strategy code first.")
        else:
            checks = {
                "strategy_name present": "strategy_name" in code,
                "entry condition hint": any(
                    kw in code.lower()
                    for kw in ["entry", "buy", "long", "open_position"]
                ),
                "exit condition hint": any(
                    kw in code.lower()
                    for kw in ["exit", "sell", "short", "close_position"]
                ),
                "risk / stop hint": any(
                    kw in code.lower()
                    for kw in ["stop", "risk", "sl", "tp", "stoploss"]
                ),
            }
            for label, passed in checks.items():
                st.markdown(f"{'✅' if passed else '❌'}  {label}")
            if all(checks.values()):
                st.success("Text-level structure check passed — no code was executed.")
            else:
                st.warning("Some hints missing — see above. No code executed.")
            _add_log("Structure validation prepared (text-level only — no code executed)")

    if prepare_btn:
        src_flag = (
            "local_csv"
            if "Local CSV" in data_source_mode
            else data_source_mode.lower()
            .replace(" — future", "")
            .replace(" ", "_")
        )
        cmd = (
            f"python -m backtesting.run_strategy \\\n"
            f"  --strategy {strategy_name} \\\n"
            f"  --market {symbol} \\\n"
            f"  --timeframe {data_timeframe} \\\n"
            f"  --data-source {src_flag} \\\n"
            f"  --start {start_date} \\\n"
            f"  --end {end_date} \\\n"
            f"  --capital {initial_capital} \\\n"
            f"  --fee {fee_pct} \\\n"
            f"  --slippage {slippage_pct} \\\n"
            f"  --risk-per-trade {risk_per_trade} \\\n"
            f"  --position-sizing \"{position_sizing.lower().replace(' ', '_')}\""
        )
        st.session_state.command_preview = cmd
        _add_log("Backtest command preview generated")

    # ── Terminal / Command Preview ─────────────────────────────────────────────
    st.divider()
    st.subheader("💻  Terminal / Command Preview")
    st.caption("Text preview only — no subprocess, no execution, no backtesting runner")

    if st.session_state.command_preview:
        st.code(st.session_state.command_preview, language="bash")
        st.caption("Configure settings above and click Prepare to refresh.")
    else:
        st.markdown(
            """
            <div style="
                background:#0d1117;border:1px solid #30363d;border-radius:6px;
                padding:16px;font-family:monospace;font-size:13px;
                color:#8b949e;line-height:1.7;
            ">
                $ <span style="color:#3fb950">awaiting configuration...</span><br>
                &gt; configure settings, then click<br>
                &gt; <strong style="color:#e6edf3">📋 Prepare Backtest Command</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# RIGHT — Results Placeholder + Logs
# ══════════════════════════════════════════════════════════════════════════════
with right_col:

    # ── Results Placeholder ───────────────────────────────────────────────────
    st.subheader("📊  Results")
    st.caption("Pending — backtest not yet executed")

    _METRICS = [
        "Total Return",
        "Max Drawdown",
        "Sharpe Ratio",
        "Sortino Ratio",
        "Profit Factor",
        "Win Rate",
        "Total Trades",
        "Avg Trade Return",
    ]
    for _m in _METRICS:
        mc, vc = st.columns([1.6, 1])
        mc.markdown(f"<small><b>{_m}</b></small>", unsafe_allow_html=True)
        vc.markdown(
            "<code style='color:#555;font-size:11px;'>Pending</code>",
            unsafe_allow_html=True,
        )

    st.divider()

    # ── UI Logs Panel ─────────────────────────────────────────────────────────
    st.subheader("📋  UI Logs")
    st.caption("Local session logs — no backend, exchange, or API logs")

    if st.button("🗑  Clear Logs", use_container_width=True):
        st.session_state.ui_logs = []
        st.rerun()

    if st.session_state.ui_logs:
        st.text_area(
            "logs",
            value="\n".join(st.session_state.ui_logs),
            height=210,
            label_visibility="collapsed",
            disabled=True,
        )
    else:
        st.markdown(
            "<small style='color:#666;'>No logs yet — interact with the UI.</small>",
            unsafe_allow_html=True,
        )


# ─── SAFETY / MODE BANNER ─────────────────────────────────────────────────────
st.divider()
st.subheader("🛡️  Safety / Mode Status")

sm_col, ss_col, sf_col = st.columns(3)

with sm_col:
    st.markdown("**Current Mode**")
    st.markdown(
        "- Offline UI shell\n"
        "- Backtest execution: **disabled**\n"
        "- API execution: **disabled**\n"
        "- Live trading: **disabled**"
    )

with ss_col:
    st.markdown("**Safe to do now**")
    st.markdown(
        "- ✅ Upload strategy code\n"
        "- ✅ Paste strategy code\n"
        "- ✅ Configure backtest settings\n"
        "- ✅ Prepare command preview\n"
        "- ✅ View result placeholders\n"
        "- ✅ View local UI logs"
    )

with sf_col:
    st.markdown("**Future only (not implemented)**")
    st.markdown(
        "- 🔜 Fetch OHLCV from Bybit / Binance\n"
        "- 🔜 Run backtest from UI\n"
        "- 🔜 Paper trading\n"
        "- 🔜 Live trading\n"
        "- 🔜 Strategy optimization\n"
        "- 🔜 AI strategy evaluation"
    )
