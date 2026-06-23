"""Лаборатория тестирования стратегий — локальный Streamlit UI для бэктестинга."""
from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

# ─── КОНФИГУРАЦИЯ СТРАНИЦЫ ────────────────────────────────────────────────────
st.set_page_config(
    page_title="Лаборатория стратегий",
    page_icon="⚗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* Typography */
html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; }
h1 { font-size: 1.6rem !important; font-weight: 700 !important; letter-spacing: -.5px; }
h2 { font-size: 1.1rem !important; font-weight: 600 !important; }
h3 { font-size: 1rem !important; font-weight: 600 !important; }
/* Dividers */
hr { border-color: #21262d !important; margin: 14px 0 !important; }
/* Buttons */
.stButton > button { border-radius: 7px !important; font-weight: 500 !important; letter-spacing: .2px; }
.stButton > button[kind="primary"] { background: linear-gradient(135deg,#1565c0,#0288d1) !important; border: none !important; }
/* Expanders */
[data-testid="stExpander"] > details { border: 1px solid #21262d !important; border-radius: 8px !important; }
[data-testid="stExpander"] > details > summary { font-weight: 500; }
/* DataFrames */
[data-testid="stDataFrame"] { border-radius: 8px; }
/* Info/success/warning blocks */
.stAlert { border-radius: 8px !important; }
/* Metric widgets */
[data-testid="metric-container"] { background: #0d1117; border: 1px solid #21262d; border-radius: 8px; padding: 10px 14px !important; }
/* Number inputs */
[data-testid="stNumberInput"] { border-radius: 6px; }
/* Pills */
[data-testid="stPills"] button { border-radius: 20px !important; font-size: 12px !important; }
</style>
""", unsafe_allow_html=True)

# ─── ЗАГРУЗКА БЭКЕНДА ─────────────────────────────────────────────────────────
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

# ─── СОСТОЯНИЕ СЕССИИ ─────────────────────────────────────────────────────────
_defaults: dict = {
    "df": None,
    "data_label": "",
    "backtest_results": None,
    "ui_logs": [],
    "command_preview": "",
    "custom_code": "",
    "custom_strategy_cls": None,       # compat — not used by new multi-strategy UI
    "custom_strategy_params": {},
    "custom_strategy_name": "",
    "custom_strategies": {},           # {cls_name: {"cls": cls, "params": {}, "code": ""}}
    "api_keys": {
        "binance": {"key": "", "secret": ""},
        "bybit":   {"key": "", "secret": ""},
    },
    "api_status": {},                  # {"binance": {"ok": bool, "message": str}}
    "selected_strategy": "sma_cross",  # persists selectbox selection across reruns
    "data_symbol": "BTC/USDT",
    "data_source": "",
    "pt_active": False,
    "pt_session": None,
    "opt_results": None,
    "opt_strategy": "sma_cross",
    "comparison_results": None,
    "wf_results": None,
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _log(msg: str) -> None:
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.ui_logs.insert(0, f"[{ts}]  {msg}")
    st.session_state.ui_logs = st.session_state.ui_logs[:80]


# ─── ЗАГРУЗКА КАСТОМНОЙ СТРАТЕГИИ ─────────────────────────────────────────────
def _load_custom_strategy(code: str):
    """
    Exec user code, find first BaseStrategy subclass, return (cls, default_params).
    Raises ValueError with a user-friendly message on failure.
    """
    from strategies.base import BaseStrategy
    import numpy as np

    namespace: dict = {
        "__builtins__": __builtins__,
        "BaseStrategy": BaseStrategy,
        "pd": pd,
        "np": np,
    }
    try:
        exec(compile(code, "<custom_strategy>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(f"Синтаксическая ошибка: {exc}")
    except Exception as exc:
        raise ValueError(f"Ошибка при загрузке кода: {exc}")

    from strategies.base import BaseStrategy as _Base
    found = [
        v for v in namespace.values()
        if isinstance(v, type) and issubclass(v, _Base) and v is not _Base
    ]
    if not found:
        raise ValueError(
            "Класс, наследующий BaseStrategy, не найден. "
            "Убедитесь, что ваш класс содержит: class MyStrategy(BaseStrategy): ..."
        )

    cls = found[0]
    # Determine default params from __init__ signature
    sig = inspect.signature(cls.__init__)
    params: dict = {}
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if param.default is not inspect.Parameter.empty:
            params[name] = param.default
    return cls, params


# ─── ТОП-50 ТОРГОВЫХ ПАР ──────────────────────────────────────────────────────
TOP_50_PAIRS = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "ADA/USDT", "AVAX/USDT", "DOGE/USDT", "TON/USDT", "DOT/USDT",
    "MATIC/USDT", "LINK/USDT", "SHIB/USDT", "LTC/USDT", "UNI/USDT",
    "BCH/USDT", "XLM/USDT", "ATOM/USDT", "ETC/USDT", "NEAR/USDT",
    "APT/USDT", "ICP/USDT", "FIL/USDT", "HBAR/USDT", "ARB/USDT",
    "VET/USDT", "OP/USDT", "MKR/USDT", "ALGO/USDT", "GRT/USDT",
    "AAVE/USDT", "STX/USDT", "EOS/USDT", "THETA/USDT", "SAND/USDT",
    "MANA/USDT", "AXS/USDT", "FTM/USDT", "RUNE/USDT", "CAKE/USDT",
    "LDO/USDT", "CRV/USDT", "DYDX/USDT", "SNX/USDT", "1INCH/USDT",
    "COMP/USDT", "ZEC/USDT", "DASH/USDT", "XMR/USDT", "CHZ/USDT",
]

# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ──────────────────────────────────────────────────
_TF_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


def _calc_bars(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> int:
    seconds = (end - start).total_seconds()
    bar_sec = _TF_SECONDS.get(freq, 3600)
    return max(100, int(seconds / bar_sec) + 1)


def _get_api(exchange_id: str) -> tuple[str | None, str | None]:
    keys = st.session_state.api_keys.get(exchange_id, {})
    k = keys.get("key", "").strip() or None
    s = keys.get("secret", "").strip() or None
    return k, s


# ─── УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЬСКИМИ СТРАТЕГИЯМИ ─────────────────────────────────
_USER_STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "user_strategies"


def _get_user_strategies_dir() -> Path:
    _USER_STRATEGIES_DIR.mkdir(exist_ok=True)
    return _USER_STRATEGIES_DIR


def _save_user_strategy(cls_name: str, code: str) -> None:
    (_get_user_strategies_dir() / f"{cls_name}.py").write_text(code, encoding="utf-8")


def _delete_user_strategy_file(cls_name: str) -> None:
    f = _get_user_strategies_dir() / f"{cls_name}.py"
    if f.exists():
        f.unlink()


def _load_all_user_strategies() -> None:
    for py_file in sorted(_get_user_strategies_dir().glob("*.py")):
        cls_name = py_file.stem
        if cls_name in st.session_state.custom_strategies:
            continue
        try:
            code = py_file.read_text(encoding="utf-8")
            cls, params = _load_custom_strategy(code)
            st.session_state.custom_strategies[cls.__name__] = {
                "cls": cls, "params": params, "code": code,
            }
        except Exception:
            pass


# ─── ОПТИМИЗАЦИЯ: ДИАПАЗОНЫ ПАРАМЕТРОВ ПО УМОЛЧАНИЮ ──────────────────────────
_OPT_DEFAULTS: dict = {
    "sma_cross":      {"fast": (5, 50, 5, "int"),    "slow": (20, 200, 20, "int")},
    "ema_cross":      {"fast": (5, 30, 5, "int"),    "slow": (15, 100, 15, "int")},
    "rsi":            {"period": (7, 21, 7, "int"),  "oversold": (20, 35, 5, "int"),  "overbought": (65, 80, 5, "int")},
    "macd":           {"fast": (8, 20, 4, "int"),    "slow": (20, 40, 5, "int"),      "signal_period": (7, 12, 2, "int")},
    "bollinger_scalp":{"period": (10, 30, 5, "int"), "num_std": (1.5, 3.0, 0.5, "float")},
    "stoch_ema_scalp":{"k_period": (9, 21, 6, "int"),"oversold": (15, 30, 5, "int")},
    "vwap_bounce":    {"window": (20, 100, 20, "int"),"deviation": (0.2, 0.8, 0.2, "float")},
    "turtle_soup":    {"n_bars": (10, 40, 5, "int"),  "exit_ema": (3, 15, 3, "int")},
    "raschke_80_20":  {"threshold": (0.10, 0.30, 0.05, "float"), "exit_ema": (3, 10, 2, "int")},
}


def _build_param_values(pmin: float, pmax: float, pstep: float, ptype: str) -> list:
    if ptype == "int":
        return list(range(int(pmin), int(pmax) + 1, max(1, int(pstep))))
    n = round((pmax - pmin) / pstep) + 1
    return [round(pmin + i * pstep, 6) for i in range(n)]


def _pt_run(
    exchange: str,
    symbol: str,
    timeframe: str,
    strategy_key: str,
    strategy_params: dict,
    capital: float,
    lookback: int,
    api_key: str | None = None,
    api_secret: str | None = None,
    fee: float = 0.001,
    slippage: float = 0.0005,
) -> dict:
    """Fetch recent bars from exchange and simulate paper trades.

    Executes on CLOSED bars only (the still-forming last candle is dropped to
    avoid look-ahead) and applies the same fee/slippage as the backtest so the
    virtual PnL stays consistent with backtest results.
    """
    from datetime import datetime, timedelta, timezone
    dm = DataManager()
    tf_sec = _TF_SECONDS.get(timeframe, 3600)
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(seconds=tf_sec * (lookback + 60))

    df = dm.load_from_exchange_all(
        exchange, symbol, timeframe,
        start=start_dt.strftime("%Y-%m-%d %H:%M"),
        end=end_dt.strftime("%Y-%m-%d %H:%M"),
        api_key=api_key,
        api_secret=api_secret,
    )
    if df.empty:
        raise ValueError("Биржа не вернула данных — проверьте пару и таймфрейм.")
    # The most recent bar is still forming — drop it so signals use only
    # completed candles (otherwise the latest signal could flip mid-bar).
    if len(df) > 1:
        df = df.iloc[:-1]
    df = df.tail(lookback)

    if strategy_key.startswith("custom__"):
        cn = strategy_key[8:]
        ci = st.session_state.custom_strategies.get(cn, {})
        if not ci.get("cls"):
            raise ValueError(f"Стратегия '{cn}' не найдена.")
        strategy = ci["cls"](**strategy_params)
    else:
        strategy = get_strategy(strategy_key, strategy_params)

    signals = strategy.generate_signals(df)

    in_position = False
    entry_price = 0.0          # execution price at entry (with slippage)
    entry_cash = 0.0           # cash committed on entry (incl. fee)
    entry_time = None
    virtual_capital = float(capital)
    qty = 0.0
    trades: list[dict] = []

    for ts, sig in signals.items():
        price = float(df.loc[ts, "close"])
        if not in_position and int(sig) == 1:
            entry_price = price * (1.0 + slippage)
            entry_cash = virtual_capital
            qty = virtual_capital * (1.0 - fee) / entry_price
            entry_time = ts
            virtual_capital = 0.0
            in_position = True
        elif in_position and int(sig) == -1:
            exit_price = price * (1.0 - slippage)
            proceeds = qty * exit_price * (1.0 - fee)
            pnl = proceeds - entry_cash
            virtual_capital = proceeds
            trades.append({
                "Вход": str(entry_time)[:16],
                "Цена входа": round(entry_price, 4),
                "Выход": str(ts)[:16],
                "Цена выхода": round(exit_price, 4),
                "PnL (USDT)": round(pnl, 2),
                "Доходность %": round((proceeds / entry_cash - 1.0) * 100, 2) if entry_cash else 0.0,
                "Капитал": round(virtual_capital, 2),
            })
            in_position = False
            qty = 0.0

    current_price = float(df["close"].iloc[-1])
    # Mark-to-market the open position net of the exit fee/slippage it would pay.
    if in_position and entry_cash:
        cur_value = qty * current_price * (1.0 - slippage) * (1.0 - fee)
        unrealized_pnl = cur_value - entry_cash
        unrealized_pct = (cur_value / entry_cash - 1.0) * 100
        equity_now = cur_value
    else:
        unrealized_pnl = 0.0
        unrealized_pct = 0.0
        equity_now = virtual_capital

    return {
        "df": df, "signals": signals,
        "in_position": in_position,
        "entry_price": round(entry_price, 4),
        "entry_time": str(entry_time)[:16] if entry_time else None,
        "qty": round(qty, 8),
        "trades": trades,
        "capital": round(equity_now, 2),
        "realized_capital": round(virtual_capital, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "unrealized_pct": round(unrealized_pct, 2),
        "current_price": round(current_price, 4),
        "last_updated": str(pd.Timestamp.now())[:19],
        "last_bar": str(df.index[-1])[:19],
        "bars_loaded": len(df),
        "exchange": exchange, "symbol": symbol, "timeframe": timeframe,
    }


def _opt_run(
    df: pd.DataFrame,
    strategy_key: str,
    param_grid: dict,
    optimize_by: str,
    initial_capital: float,
    fee_pct: float,
    slippage_pct: float,
    sl_pct: float,
    tp_pct: float,
    trail_pct: float,
    hold_bars: int = 0,
    progress_cb=None,
) -> pd.DataFrame:
    """Grid-search over param_grid = {name: [val, ...]}; returns sorted DataFrame."""
    import itertools
    names = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))
    total = len(combos)
    rows: list[dict] = []

    for i, combo in enumerate(combos):
        params = dict(zip(names, combo))
        try:
            if strategy_key.startswith("custom__"):
                cn = strategy_key[8:]
                ci = st.session_state.custom_strategies.get(cn, {})
                if not ci.get("cls"):
                    continue
                strategy = ci["cls"](**params)
            else:
                strategy = get_strategy(strategy_key, params)

            signals = strategy.generate_signals(df)
            ec, trades = run_backtest(
                df, signals,
                initial_capital=float(initial_capital),
                fee=fee_pct / 100.0, slippage=slippage_pct / 100.0,
                stop_loss=sl_pct / 100.0, take_profit=tp_pct / 100.0,
                trailing_stop=trail_pct / 100.0,
                hold_bars=int(hold_bars),
            )
            m = calculate_metrics(ec, trades, float(initial_capital))
            pf_raw = m.get("profit_factor", 0)
            pf_val = 99.0 if str(pf_raw) == "∞" else float(pf_raw or 0)

            rows.append({
                **{k: round(float(v), 4) if isinstance(v, float) else int(v) for k, v in params.items()},
                "доходность_%": round(float(m.get("total_return", 0) or 0), 2),
                "шарп":         round(float(m.get("sharpe_ratio",  0) or 0), 3),
                "просадка_%":   round(float(m.get("max_drawdown",  0) or 0), 2),
                "проф_фактор":  round(pf_val, 3),
                "побед_%":      round(float(m.get("win_rate",      0) or 0), 1),
                "сделок":       int(m.get("total_trades", 0) or 0),
            })
        except Exception:
            pass
        if progress_cb:
            progress_cb(i + 1, total)

    if not rows:
        return pd.DataFrame()

    sort_col = {"Доходность": "доходность_%", "Шарп": "шарп", "Профит-фактор": "проф_фактор"}.get(
        optimize_by, "доходность_%"
    )
    return pd.DataFrame(rows).sort_values(sort_col, ascending=False).reset_index(drop=True)


# ─── ЭКСПОРТ АНАЛИЗА ──────────────────────────────────────────────────────────

def export_analysis_safe_filename(value: str) -> str:
    if not value or not value.strip():
        return "unknown"
    v = re.sub(r'[/\\:*?"<>|]', "", value.strip())
    v = v.replace(" ", "_")
    v = re.sub(r"_+", "_", v).strip("_")
    return v or "unknown"


def export_analysis_to_json_bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def export_analysis_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def export_analysis_build_trades_df(
    trades: list,
    symbol: str,
    timeframe: str,
) -> pd.DataFrame:
    _COLS = [
        "trade_id", "symbol", "timeframe", "side",
        "entry_time", "entry_price", "exit_time", "exit_price",
        "qty", "gross_pnl", "net_pnl", "pnl_pct",
        "fees", "slippage", "holding_bars",
        "entry_reason", "exit_reason", "stop_loss", "take_profit",
    ]
    if not trades:
        return pd.DataFrame(columns=_COLS)

    tf_sec = _TF_SECONDS.get(timeframe, 3600)
    rows = []
    for i, t in enumerate(trades):
        ep      = float(getattr(t, "entry_price", 0) or 0)
        xp      = float(getattr(t, "exit_price",  0) or 0)
        qty     = float(getattr(t, "shares",       0) or 0)
        pnl     = float(getattr(t, "pnl",          0) or 0)
        pnl_pct = float(getattr(t, "pnl_pct",      0) or 0)
        ets     = getattr(t, "entry_ts", None)
        xts     = getattr(t, "exit_ts",  None)

        gross_pnl = qty * (xp - ep) if xp else 0.0
        fees      = qty * ep * 0.001 + (qty * xp * 0.001 if xp else 0.0)

        holding = 0
        if ets is not None and xts is not None:
            try:
                holding = max(1, int(
                    (pd.Timestamp(xts) - pd.Timestamp(ets)).total_seconds() / tf_sec
                ))
            except Exception:
                pass

        rows.append({
            "trade_id":     i + 1,
            "symbol":       symbol,
            "timeframe":    timeframe,
            "side":         "long",
            "entry_time":   str(ets)[:19] if ets is not None else "",
            "entry_price":  round(ep,      8),
            "exit_time":    str(xts)[:19] if xts is not None else "",
            "exit_price":   round(xp,      8),
            "qty":          round(qty,     8),
            "gross_pnl":    round(gross_pnl, 4),
            "net_pnl":      round(pnl,     4),
            "pnl_pct":      round(pnl_pct, 4),
            "fees":         round(fees,    4),
            "slippage":     0,
            "holding_bars": holding,
            "entry_reason": "signal",
            "exit_reason":  getattr(t, "exit_reason", "") or "",
            "stop_loss":    "",
            "take_profit":  "",
        })
    return pd.DataFrame(rows)


def export_analysis_build_equity_df(
    df: pd.DataFrame,
    equity_curve: pd.DataFrame | None,
    initial_capital: float,
) -> pd.DataFrame:
    if equity_curve is not None and not equity_curve.empty and "equity" in equity_curve.columns:
        idx = equity_curve.index
        eq  = equity_curve["equity"]
    else:
        idx = df.index
        eq  = pd.Series(float(initial_capital), index=idx)

    close        = df["close"].reindex(idx) if "close" in df.columns else pd.Series(0.0, index=idx)
    drawdown_pct = ((eq / eq.cummax()) - 1.0) * 100.0

    return pd.DataFrame({
        "timestamp":    idx.astype(str),
        "equity":       eq.round(4).values,
        "drawdown_pct": drawdown_pct.round(4).values,
        "position":     0,
        "signal":       0,
        "close":        close.values,
    })


def export_analysis_build_summary(
    strategy_label: str,
    strategy_key: str,
    df: pd.DataFrame,
    trades_df: pd.DataFrame,
    equity_df: pd.DataFrame,
    metrics: dict,
    strategy_params: dict,
    backtest_params: dict,
    symbol: str,
    timeframe: str,
    source: str,
) -> dict:
    n       = len(trades_df)
    win_df  = trades_df[trades_df["pnl_pct"] > 0] if n else pd.DataFrame()
    loss_df = trades_df[trades_df["pnl_pct"] < 0] if n else pd.DataFrame()

    avg_profit = round(float(win_df["pnl_pct"].mean()),  4) if len(win_df)  else 0.0
    avg_loss   = round(float(loss_df["pnl_pct"].mean()), 4) if len(loss_df) else 0.0
    win_rate   = len(win_df) / n if n else 0.0
    expectancy = round(win_rate * avg_profit + (1 - win_rate) * avg_loss, 4) if n else 0.0

    gross_profit = float(trades_df.loc[trades_df["net_pnl"] > 0, "net_pnl"].sum()) if n else 0.0
    gross_loss   = float(trades_df.loc[trades_df["net_pnl"] < 0, "net_pnl"].sum()) if n else 0.0
    pf           = round(gross_profit / abs(gross_loss), 4) if gross_loss != 0 else None

    raw_pf      = metrics.get("profit_factor", 0)
    fallback_pf = None if str(raw_pf) == "∞" else (float(raw_pf) if raw_pf else None)
    max_dd      = float(metrics.get("max_drawdown", 0) or 0)

    return {
        "export_schema": {"name": "crypto_strategy_lab_backtest_analysis", "version": "1.0"},
        "strategy": {"name": strategy_key, "description": "", "class_name": strategy_label},
        "data": {
            "symbol":    symbol,
            "timeframe": timeframe,
            "source":    source,
            "bars":      len(df),
            "start":     str(df.index[0])[:10]  if len(df) else "",
            "end":       str(df.index[-1])[:10] if len(df) else "",
        },
        "strategy_params": strategy_params,
        "backtest_params": backtest_params,
        "metrics": {
            "total_return_pct": float(metrics.get("total_return",    0) or 0),
            "max_drawdown_pct": max_dd,
            "sharpe":           float(metrics.get("sharpe_ratio",    0) or 0),
            "sortino":          float(metrics.get("sortino_ratio",   0) or 0),
            "profit_factor":    pf if pf is not None else fallback_pf,
            "win_rate_pct":     round(win_rate * 100, 2),
            "total_trades":     n,
            "avg_trade_pct":    float(metrics.get("avg_trade_return", 0) or 0),
            "avg_profit_pct":   avg_profit,
            "avg_loss_pct":     avg_loss,
            "expectancy_pct":   expectancy,
            "final_capital":    float(
                metrics.get("final_equity", backtest_params.get("initial_capital", 0)) or 0
            ),
        },
        "quality_flags": {
            "enough_trades":       n >= 100,
            "profit_factor_valid": pf is not None and pf > 0,
            "expectancy_positive": expectancy > 0,
            "drawdown_acceptable": max_dd > -30,
        },
        "analysis_prompt": (
            "Оцени стратегию строго по метрикам. Дай вывод в формате: "
            "1. Вердикт A/B/C/D  2. Что хорошо  3. Что плохо  "
            "4. Главная проблема  5. Один следующий тест."
        ),
    }


def export_analysis_build_pack(
    summary: dict,
    trades_df: pd.DataFrame,
    equity_df: pd.DataFrame,
) -> dict:
    return {
        "export_schema": {"name": "crypto_strategy_lab_backtest_analysis_pack", "version": "1.0"},
        "summary": summary,
        "trades": trades_df.to_dict(orient="records"),
        "equity": equity_df.to_dict(orient="records"),
    }


def export_analysis_render_downloads(res: dict, df_source: pd.DataFrame) -> None:
    equity_curve    = res.get("equity_curve")
    trades_raw      = res.get("trades", [])
    metrics         = res.get("metrics", {})
    strategy_label  = res.get("strategy_label", "")
    strategy_key    = res.get("strategy_key") or res.get("strategy_label", "strategy")
    strategy_params = res.get("strategy_params", {})
    backtest_params = res.get("backtest_params", {})
    symbol          = res.get("symbol", st.session_state.get("data_symbol", "unknown"))
    timeframe       = res.get("timeframe", "")
    source          = res.get("source", st.session_state.get("data_source", ""))
    initial_capital = backtest_params.get("initial_capital", 10_000.0)

    if equity_curve is not None and not equity_curve.empty:
        df = df_source.reindex(equity_curve.index)
    else:
        df = df_source

    try:
        trades_df = export_analysis_build_trades_df(trades_raw, symbol, timeframe)
        equity_df = export_analysis_build_equity_df(df, equity_curve, initial_capital)
        summary   = export_analysis_build_summary(
            strategy_label, strategy_key, df, trades_df, equity_df,
            metrics, strategy_params, backtest_params, symbol, timeframe, source,
        )
        pack = export_analysis_build_pack(summary, trades_df, equity_df)
    except Exception as _e:
        st.warning(f"Нет данных для выгрузки. Сначала запустите бэктест. ({_e})")
        return

    s, sym, tf = (
        export_analysis_safe_filename(strategy_key),
        export_analysis_safe_filename(symbol),
        export_analysis_safe_filename(timeframe),
    )
    pfx = f"{s}_{sym}_{tf}"

    st.subheader("📤  Выгрузка для анализа стратегии",
                 help="Скачайте результаты для внешнего анализа, AI-чатов или таблиц Excel.")

    # One-click ZIP with all files
    import zipfile
    import io as _io_zip
    _zip_buf = _io_zip.BytesIO()
    with zipfile.ZipFile(_zip_buf, "w", zipfile.ZIP_DEFLATED) as _zf:
        _zf.writestr(f"{pfx}_summary.json",       export_analysis_to_json_bytes(summary))
        _zf.writestr(f"{pfx}_trades.csv",          export_analysis_to_csv_bytes(trades_df))
        _zf.writestr(f"{pfx}_equity.csv",          export_analysis_to_csv_bytes(equity_df))
        _zf.writestr(f"{pfx}_analysis_pack.json",  export_analysis_to_json_bytes(pack))
    _zip_bytes = _zip_buf.getvalue()

    st.download_button(
        "📦  Скачать всё одним архивом (.zip)",
        data=_zip_bytes,
        file_name=f"{pfx}_full_analysis.zip",
        mime="application/zip",
        use_container_width=True,
        type="primary",
        help="ZIP с 4 файлами: summary.json, trades.csv, equity.csv, analysis_pack.json",
    )
    d1, d2, d3, d4 = st.columns(4)
    d1.download_button(
        "📄 summary.json", export_analysis_to_json_bytes(summary),
        f"{pfx}_summary.json", "application/json", use_container_width=True,
        help="Сводка метрик и параметров в формате JSON",
    )
    d2.download_button(
        "📊 trades.csv", export_analysis_to_csv_bytes(trades_df),
        f"{pfx}_trades.csv", "text/csv", use_container_width=True,
        help="Все сделки с ценами входа/выхода, PnL и причиной закрытия",
    )
    d3.download_button(
        "📈 equity.csv", export_analysis_to_csv_bytes(equity_df),
        f"{pfx}_equity.csv", "text/csv", use_container_width=True,
        help="Кривая капитала: equity по каждому бару",
    )
    d4.download_button(
        "🤖 analysis_pack.json", export_analysis_to_json_bytes(pack),
        f"{pfx}_analysis_pack.json", "application/json", use_container_width=True,
        help="Полный пакет для AI-анализа: метрики + сделки + equity в одном JSON",
    )


_load_all_user_strategies()


def _fmt_cls_display(cls_name: str, cls_obj=None) -> str:
    """AdxGapStrategy → 'ADX Gap',  ThreeLittleIndiansStrategy → 'Three Little Indians'."""
    import re as _re_fmt
    if cls_obj is not None:
        attr_name = getattr(cls_obj, "name", None)
        if attr_name and isinstance(attr_name, str) and attr_name not in ("", "base"):
            return attr_name.replace("_", " ").strip().title()
    name = _re_fmt.sub(r"Strategy$", "", cls_name)
    name = _re_fmt.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = _re_fmt.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    return name.strip()


def _fmt_custom_label(cls_name: str, cls_obj=None) -> str:
    """Selectbox label: '🆕 Display Name — short description'."""
    display = _fmt_cls_display(cls_name, cls_obj)
    desc = (getattr(cls_obj, "description", "") or "").strip() if cls_obj else ""
    if desc:
        short = desc[:42] + "…" if len(desc) > 42 else desc
        return f"🆕 {display} — {short}"
    return f"🆕 {display}"


# ─── AI ОЦЕНКА СТРАТЕГИИ (офлайн, rule-based) ─────────────────────────────────

def _ai_eval_strategy(metrics: dict) -> dict:
    """Compute an offline rule-based verdict (A/B/C/D) + written evaluation."""
    ret    = float(metrics.get("total_return",     0) or 0)
    dd     = float(metrics.get("max_drawdown",     0) or 0)
    sharpe = float(metrics.get("sharpe_ratio",     0) or 0)
    pf_raw = metrics.get("profit_factor", 0)
    pf     = 99.0 if str(pf_raw) == "∞" else float(pf_raw or 0)
    wr     = float(metrics.get("win_rate",         0) or 0)
    trades = int(metrics.get("total_trades",       0) or 0)
    exp    = float(metrics.get("expectancy_pct",   0) or 0)
    sharpe = float(metrics.get("sharpe_ratio",     0) or 0)

    goods: list[str] = []
    bads:  list[str] = []

    if ret > 20:        goods.append(f"Высокая доходность {ret:+.1f}%")
    elif ret > 5:       goods.append(f"Положительная доходность {ret:+.1f}%")
    if sharpe > 1.5:    goods.append(f"Отличный коэф. Шарпа {sharpe:.2f}")
    elif sharpe > 1.0:  goods.append(f"Хороший коэф. Шарпа {sharpe:.2f}")
    if pf > 2.0:        goods.append(f"Высокий профит-фактор {pf:.2f}")
    elif pf > 1.5:      goods.append(f"Хороший профит-фактор {pf:.2f}")
    if wr > 60:         goods.append(f"Высокий % побед {wr:.1f}%")
    elif wr > 50:       goods.append(f"Больше половины сделок прибыльны {wr:.1f}%")
    if dd > -10:        goods.append(f"Низкая просадка {dd:.1f}%")
    elif dd > -20:      goods.append(f"Умеренная просадка {dd:.1f}%")
    if exp > 0.5:       goods.append(f"Положительное матожидание {exp:+.2f}%")
    if trades >= 50:    goods.append(f"Хорошая статистическая база ({trades} сделок)")
    elif trades >= 20:  goods.append(f"Достаточно сделок для оценки ({trades})")

    if ret < 0:         bads.append(f"Отрицательная доходность {ret:+.1f}%")
    elif ret < 5:       bads.append(f"Очень низкая доходность {ret:+.1f}%")
    if sharpe < 0:      bads.append(f"Отрицательный коэф. Шарпа {sharpe:.2f}")
    elif sharpe < 0.5:  bads.append(f"Низкий коэф. Шарпа {sharpe:.2f}")
    if pf < 1.0:        bads.append(f"Профит-фактор < 1 ({pf:.2f}) — стратегия убыточна в сумме")
    elif pf < 1.2:      bads.append(f"Низкий профит-фактор {pf:.2f}")
    if wr < 40:         bads.append(f"Низкий % побед {wr:.1f}%")
    if dd < -30:        bads.append(f"Критически большая просадка {dd:.1f}%")
    elif dd < -20:      bads.append(f"Значительная просадка {dd:.1f}%")
    if trades < 10:     bads.append(f"Слишком мало сделок ({trades}) — статистика ненадёжна")
    elif trades < 20:   bads.append(f"Мало сделок ({trades}) — нужно больше для уверенности")
    if exp < 0:         bads.append(f"Отрицательное матожидание {exp:+.2f}%")

    # Score
    score = 0
    if ret > 20:      score += 2
    elif ret > 5:     score += 1
    if sharpe > 1.5:  score += 2
    elif sharpe > 1.0: score += 1
    if pf > 2.0:      score += 2
    elif pf > 1.5:    score += 1
    if wr > 55:       score += 1
    if dd > -15:      score += 2
    elif dd > -25:    score += 1
    if trades >= 30:  score += 1

    if score >= 9:   grade, g_col, g_text = "A", "#4caf50", "Отличная стратегия"
    elif score >= 6: grade, g_col, g_text = "B", "#8bc34a", "Хорошая стратегия"
    elif score >= 3: grade, g_col, g_text = "C", "#ff9800", "Слабая стратегия"
    else:            grade, g_col, g_text = "D", "#f44336", "Нежизнеспособная стратегия"

    # Main problem
    if pf < 1.0:
        problem = "Профит-фактор < 1 — стратегия теряет деньги в сумме. Переработайте логику входа или добавьте фильтры тренда."
    elif trades < 10:
        problem = "Слишком мало сделок — любые выводы статистически ненадёжны. Увеличьте период данных."
    elif dd < -30:
        problem = f"Просадка {dd:.1f}% слишком велика для реального применения. Добавьте SL 3–5%."
    elif sharpe < 0.5 and ret > 0:
        problem = "Низкий Шарп — стратегия зарабатывает, но с избыточным риском. Сократите размер позиции или добавьте фильтры."
    elif ret < 0:
        problem = "Стратегия убыточна. Проверьте логику сигналов и попробуйте другой таймфрейм."
    elif exp < 0:
        problem = "Отрицательное матожидание — средний убыток превышает среднюю прибыль. Добавьте тейк-профит."
    else:
        problem = "Явных критичных проблем нет. Проверьте робастность на разных периодах и инструментах."

    # Next test
    if trades < 10:
        next_test = "Запустите на большем периоде данных (минимум 100+ сделок для надёжных выводов)."
    elif pf < 1.2:
        next_test = "Добавьте трейлинг-стоп 3–5% и тейк-профит 5–10% — это улучшит профит-фактор."
    elif dd < -25:
        next_test = "Добавьте стоп-лосс 2–5% для ограничения просадки. Используйте раздел «Параметры выхода»."
    elif wr < 45:
        next_test = "Запустите оптимизацию параметров по «Профит-фактор» — найдите комбинацию с лучшим win rate."
    elif sharpe < 1.0:
        next_test = "Запустите оптимизацию по «Шарп» — цель Шарп > 1."
    else:
        next_test = "Протестируйте на другом периоде / торговой паре для проверки робастности результатов."

    return {
        "grade": grade, "grade_color": g_col, "grade_text": g_text,
        "score": score, "goods": goods, "bads": bads,
        "problem": problem, "next_test": next_test,
    }

# ─── БОКОВАЯ ПАНЕЛЬ: ПОЛНАЯ ИНСТРУКЦИЯ ────────────────────────────────────────
with st.sidebar:
    st.markdown("# 📖 Инструкция")

    with st.expander("⚡ Быстрый старт", expanded=True):
        st.markdown("""
        1. **Период и таймфрейм** *(вверху)* — бары считаются автоматически
        2. **Стратегия** *(левая колонка)* — 9 готовых или своя
        3. **Данные** *(левая колонка)* — синтетика / CSV / биржа
        4. **Параметры и выходы** *(центр)* — капитал, SL/TP/Trail
        5. **Запуск** *(центр)* — нажать ▶️ Запустить бэктест
        """)


    with st.expander("📊 Расшифровка метрик"):
        st.markdown("""
        | Метрика | Хорошее значение | Ориентир |
        |---------|-----------------|----------|
        | **Общая доходность** | > +20% | выше Buy&Hold |
        | **Макс. просадка** | > -20% | < половины дохода |
        | **Коэф. Шарпа** | > 1.0 | > 2 = отлично |
        | **Profit Factor** | > 1.5 | > 2 = очень хорошо |
        | **Win Rate** | > 50% | зависит от R:R |
        | **Expectancy** | > 0% | чем выше — тем лучше |
        """)

    with st.expander("📝 Формат своей стратегии"):
        st.code("""from strategies.base import BaseStrategy
import pandas as pd

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "Описание"

    def __init__(self, period: int = 14):
        self.period = period

    def generate_signals(self, df: pd.DataFrame):
        signal = pd.Series(0, index=df.index)
        # signal = 1  → вход
        # signal = -1 → выход
        return signal

    def get_params(self):
        return {"period": self.period}
""", language="python")

    with st.expander("🤖  Промт для адаптации стратегии"):
        st.caption("Скопируй и вставь в любой ИИ-чат, чтобы адаптировать код стратегии под эту систему.")
        st.code("""Адаптируй стратегию для системы бэктестинга на Python.

Система ожидает класс, наследующий BaseStrategy:

from strategies.base import BaseStrategy
import pandas as pd
import numpy as np

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "Краткое описание"

    def __init__(self, period: int = 14, threshold: float = 0.02):
        self.period = period
        self.threshold = threshold

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signal = pd.Series(0, index=df.index)
        # signal[condition] = 1   → вход в лонг
        # signal[condition] = -1  → выход из лонга
        return signal

    def get_params(self):
        return {"period": self.period, "threshold": self.threshold}

Правила:
1. generate_signals принимает df с колонками: open, high, low, close, volume
   и DatetimeIndex. Возвращает pd.Series(int) с теми же индексами.
2. 1 = открыть лонг (игнорируется если уже в позиции)
   -1 = закрыть лонг (игнорируется если нет позиции)
   0 = ничего не делать
3. Параметры __init__ — только примитивы: int, float, bool, str.
   Обязательно с дефолтными значениями.
4. Не смотри вперёд: сигнал на баре i строится только по данным до i включительно.
5. ATR-стоп и сложные выходы встраивай в generate_signals() (выставляй -1).
   Движок также поддерживает SL/TP/Trail через UI — их можно оставить на движок.
6. Доступные библиотеки: pandas (pd), numpy (np).
   Сторонние (ta, talib и т.д.) не поддерживаются — реализуй индикаторы вручную.
7. Не используй глобальные переменные, файлы, сеть или async.

Вставь оригинальный код стратегии ниже и адаптируй под этот формат:
""", language="text")

    with st.expander("🔑 Об API ключах"):
        st.markdown("""
        API ключи **не отправляются на сервер** — всё выполняется локально.
        Ключи хранятся только в памяти сессии и не сохраняются на диск.

        **Без ключей:** публичные OHLCV — полная история постранично.

        **С ключами:**
        - Выше лимиты на запросы
        - Доступ к полной истории
        - Основа для бумажной торговли (в будущем)

        Создать ключи с правами **Read Only** → безопасно.
        """)

    with st.expander("🛑 Параметры выхода"):
        st.markdown("""
        **Стоп-лосс** — фиксированный. Срабатывает если open ≤ SL уровня.\n
        **Трейлинг-стоп** — X% ниже максимального close с момента входа.
        Уровень обновляется на каждом баре; срабатывает по open следующего.\n
        **Тейк-профит** — фиксированная цель. Срабатывает если open ≥ TP.\n
        **Hold Bars** — принудительное закрытие через N баров.\n
        Приоритет: `SL → Trail → TP → Hold Bars → Сигнал`
        """)

    with st.expander("📚  Глоссарий терминов"):
        st.markdown("""
| Термин | Значение |
|--------|----------|
| **OHLCV** | Open / High / Low / Close / Volume — стандартные данные бара |
| **Бар / Свеча** | Единица времени на графике (1H = 1 час, 1D = 1 день) |
| **Таймфрейм** | Длительность одного бара: 15M, 1H, 4H, 1D и т.д. |
| **Сигнал** | 1 = вход в лонг, -1 = выход, 0 = ничего |
| **Лонг** | Позиция «в покупку» — зарабатываем на росте цены |
| **Капитал** | Виртуальный портфель в USDT, меняется с каждой сделкой |
| **Комиссия** | % от суммы сделки, списывается за вход и выход |
| **Проскальзывание** | Разница между ценой сигнала и ценой исполнения |
| **Стоп-лосс (SL)** | Уровень ниже входа для ограничения убытка |
| **Тейк-профит (TP)** | Уровень выше входа для фиксации прибыли |
| **Трейлинг-стоп** | Подтягивающийся SL: следует за максимумом цены |
| **Hold Bars** | Принудительный выход через N баров после входа |
| **Sharpe Ratio** | Доход / Риск (стд. отклонение). >1 = хорошо, >2 = отлично |
| **Sortino Ratio** | Аналог Sharpe, но учитывает только отрицательную волатильность |
| **Profit Factor** | Валовая прибыль / Валовый убыток. >1.5 = хорошо |
| **Win Rate** | % прибыльных сделок от общего числа |
| **Expectancy** | Ожидаемый % PnL с одной сделки. Должна быть > 0 |
| **Max Drawdown** | Макс. падение капитала от пика. Чем меньше %, тем лучше |
| **Buy & Hold** | Бенчмарк: купить на старте, держать до конца периода |
| **IS / OOS** | In-Sample (обучение) / Out-of-Sample (тест) в Walk-Forward |
| **Robustness** | Коэф. устойчивости OOS/IS. ≥ 0.7 = стратегия не переобучена |
| **CLI** | Командная строка — запуск бэктеста без UI через терминал |
| **BaseStrategy** | Базовый класс для своих стратегий в этой системе |
        """)


    st.divider()

    # ── 🔑 API ключи бирж ────────────────────────────────────────────────────
    with st.expander("🔑  API ключи бирж", expanded=False):
        st.caption(
            "Ключи хранятся **только в памяти сессии** — не сохраняются на диск "
            "и не отправляются никуда. Создавайте ключи с правами **Read Only**."
        )
        for _exch in ["binance", "bybit"]:
            st.markdown(f"**{_exch.title()}**")
            _k_col, _s_col = st.columns(2)
            _new_key = _k_col.text_input(
                "API Key", type="password",
                value=st.session_state.api_keys[_exch]["key"],
                key=f"sb_api_key_{_exch}",
                placeholder="Вставьте API Key",
            )
            _new_sec = _s_col.text_input(
                "API Secret", type="password",
                value=st.session_state.api_keys[_exch]["secret"],
                key=f"sb_api_secret_{_exch}",
                placeholder="Вставьте API Secret",
            )
            st.session_state.api_keys[_exch]["key"] = _new_key
            st.session_state.api_keys[_exch]["secret"] = _new_sec
            if st.button(
                f"🔌  Проверить {_exch.title()}",
                key=f"sb_test_api_{_exch}",
                use_container_width=True,
            ):
                with st.spinner(f"Проверка {_exch}…"):
                    try:
                        dm = DataManager()
                        _k, _s = _get_api(_exch)
                        result = dm.check_connection(_exch, _k, _s)
                        st.session_state.api_status[_exch] = result
                        _log(f"API {_exch}: {result['message']}")
                    except Exception as exc:
                        st.session_state.api_status[_exch] = {"ok": False, "message": str(exc)}
            if _exch in st.session_state.api_status:
                _st = st.session_state.api_status[_exch]
                if _st["ok"]:
                    st.success(f"✅  {_st['message']}")
                else:
                    st.error(f"❌  {_st['message']}")
            if _exch != "bybit":
                st.markdown("---")

    st.divider()
    st.markdown("**Версия:** MVP · Локальный · Офлайн")


# ─── ЗАГОЛОВОК ────────────────────────────────────────────────────────────────
st.title("⚗️  Лаборатория тестирования стратегий")
st.markdown("*Локальный офлайн инструмент для бэктестинга крипто-стратегий*")

b1, b2, b3, b4, b5 = st.columns(5)
b1.success("MVP — Данные + Бэктест")
b2.info("Офлайн режим")
b3.info("Только исследования")
b4.warning("Торговля отключена")
b5.warning("API ключи — память")

if not _backend_ok:
    st.error(f"⚠️  Ошибка загрузки модулей: {_backend_error}")
    st.info("Запускайте из корня: `python3 -m streamlit run dashboard/strategy_testing_lab.py`")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# ПЕРИОД И ТАЙМФРЕЙМ
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📅  Период и таймфрейм")

pt1, pt2, pt3, pt4 = st.columns([1.2, 1.2, 0.8, 1.8])
with pt1:
    start_date = st.date_input(
        "Дата начала", value=pd.Timestamp("2026-01-01"),
        help="Начало периода тестирования. Данные загружаются с этой даты.",
    )
with pt2:
    end_date = st.date_input(
        "Дата окончания", value=pd.Timestamp("2026-06-22"),
        help="Конец периода тестирования. Чем дольше период — тем надёжнее результат.",
    )
with pt3:
    timeframe = st.selectbox(
        "Таймфрейм", ["15m", "1m", "5m", "30m", "1h", "4h", "1d"],
        help="Длительность одного бара. 15m = 15 минут, 1h = час, 1d = день. Влияет на число баров.",
    )
with pt4:
    _dates_ok = end_date >= start_date
    if _dates_ok:
        _n_bars = _calc_bars(pd.Timestamp(start_date), pd.Timestamp(end_date), timeframe)
        st.info(f"📊 Расчётный период: **{_n_bars:,} баров** ({timeframe})", icon=None)
    else:
        st.error("⚠️  Дата окончания раньше даты начала")

st.divider()

# ─── ТРЁХКОЛОНОЧНЫЙ МАКЕТ ─────────────────────────────────────────────────────
left_col, center_col, right_col = st.columns([1.2, 1.4, 1.1])

# ══════════════════════════════════════════════════════════════════════════════
# ЛЕВАЯ КОЛОНКА — Стратегия + Данные
# ══════════════════════════════════════════════════════════════════════════════
with left_col:

    # ── Выбор стратегии ───────────────────────────────────────────────────────
    st.subheader("🎯  Стратегия")

    STRATEGY_LABELS_RU: dict[str, str] = {
        "sma_cross":       "SMA Crossover",
        "ema_cross":       "EMA Crossover",
        "rsi":             "RSI Mean Reversion",
        "macd":            "MACD Momentum",
        "bollinger_scalp": "Bollinger Scalp",
        "stoch_ema_scalp": "Stochastic + EMA Scalp",
        "vwap_bounce":     "VWAP Bounce",
        "turtle_soup":     "Turtle Soup",
        "raschke_80_20":   "80-20 по Рашке",
    }
    STRATEGY_CATEGORIES_MAP: dict[str, str] = {
        "sma_cross":       "📈 Тренд",
        "ema_cross":       "📈 Тренд",
        "rsi":             "🔄 Возврат к среднему",
        "macd":            "🔄 Возврат к среднему",
        "bollinger_scalp": "⚡ Скальпинг",
        "stoch_ema_scalp": "⚡ Скальпинг",
        "vwap_bounce":     "⚡ Скальпинг",
        "turtle_soup":     "🔃 Контртренд",
        "raschke_80_20":   "🔃 Контртренд",
    }
    STRATEGY_TIMEFRAMES_REC: dict[str, str] = {
        "sma_cross":       "1H · 4H · 1D",
        "ema_cross":       "1H · 4H · 1D",
        "rsi":             "1H · 4H",
        "macd":            "1H · 4H",
        "bollinger_scalp": "15M · 1H",
        "stoch_ema_scalp": "15M",
        "vwap_bounce":     "15M · 1H",
        "turtle_soup":     "4H · 1D",
        "raschke_80_20":   "1H · 4H",
    }
    STRATEGY_DESC_FULL: dict[str, str] = {
        "sma_cross":       "Покупает при пересечении быстрой SMA вверх через медленную. Продаёт при обратном. Эффективна на трендовых рынках, даёт ложные сигналы в боковике.",
        "ema_cross":       "Аналог SMA Crossover с экспоненциальными MA. EMA придаёт больший вес последним ценам — реагирует быстрее, меньше запаздывания.",
        "rsi":             "Вход, когда RSI опускается ниже порога перепроданности. Расчёт на возврат к среднему. Риск «ловли ножей» на трендовых падениях.",
        "macd":            "Вход при пересечении MACD своей сигнальной линии снизу вверх. Lagging-индикатор: сигналы запаздывают, работает в направленных рынках.",
        "bollinger_scalp": "Покупает, когда цена касается нижней полосы Боллинджера, рассчитывая на возврат к средней. Оптимален на M15.",
        "stoch_ema_scalp": "Stochastic в зоне перепроданности + цена выше EMA(50). Комбинирует моментум и тренд-фильтр. Оптимален на M15.",
        "vwap_bounce":     "Вход при отклонении ниже VWAP на заданный %. VWAP — точка притяжения институциональных объёмов. M15–1H.",
        "turtle_soup":     "Ловит ложный пробой N-барового минимума: цена уходит ниже, затем возвращается — «медвежья ловушка». 4H–1D.",
        "raschke_80_20":   "Паттерн Линды Рашке: бар открывается в нижних 20% диапазона и закрывается в верхних 20% — разворотный сигнал смены настроения. 1H–4H.",
    }
    for _cn, _ci_entry in st.session_state.custom_strategies.items():
        _cls_entry = _ci_entry.get("cls")
        STRATEGY_LABELS_RU[f"custom__{_cn}"] = _fmt_custom_label(_cn, _cls_entry)
        STRATEGY_CATEGORIES_MAP[f"custom__{_cn}"] = "🆕 Своя"
        STRATEGY_TIMEFRAMES_REC[f"custom__{_cn}"] = "Любой"
        _desc_entry = (getattr(_cls_entry, "description", "") or "").strip() if _cls_entry else ""
        STRATEGY_DESC_FULL[f"custom__{_cn}"] = _desc_entry or f"Пользовательская стратегия: {_fmt_cls_display(_cn, _cls_entry)}"

    # Category filter (pills)
    _cat_all_opts = ["Все", "📈 Тренд", "🔄 Возврат к среднему", "⚡ Скальпинг", "🔃 Контртренд"]
    if st.session_state.custom_strategies:
        _cat_all_opts.append("🆕 Своя")
    _strat_cat = st.pills(
        "Категория", _cat_all_opts, default="Все", key="strat_cat_pills",
        help="Фильтр по типу стратегии",
    )

    # Filter strategies by category
    _filtered_keys = [
        k for k, v in STRATEGY_LABELS_RU.items()
        if _strat_cat == "Все" or STRATEGY_CATEGORIES_MAP.get(k) == _strat_cat
    ]
    if not _filtered_keys:
        _filtered_keys = list(STRATEGY_LABELS_RU.keys())

    strategy_label_to_key = {v: k for k, v in STRATEGY_LABELS_RU.items()}
    _labels_list = [STRATEGY_LABELS_RU[k] for k in _filtered_keys]
    _saved_key = st.session_state.get("selected_strategy", "sma_cross")
    if _saved_key not in _filtered_keys:
        _saved_key = _filtered_keys[0]
    selected_label = st.selectbox(
        "Стратегия",
        _labels_list,
        index=_filtered_keys.index(_saved_key),
        help="Выберите торговую стратегию из списка",
    )
    selected_key = strategy_label_to_key[selected_label]
    st.session_state.selected_strategy = selected_key

    # Description card
    _cat_badge = STRATEGY_CATEGORIES_MAP.get(selected_key, "")
    _tf_rec = STRATEGY_TIMEFRAMES_REC.get(selected_key, "")
    _desc_full = STRATEGY_DESC_FULL.get(selected_key, "")
    if _desc_full:
        _tf_html = (
            f"<span style='background:#0d3349;color:#42a5f5;border-radius:4px;padding:2px 7px;"
            f"font-size:11px;font-weight:600;margin-left:6px;'>⏱ {_tf_rec}</span>"
        ) if _tf_rec else ""
        _cat_html = (
            f"<span style='font-size:11px;color:#aaa;'>{_cat_badge}</span>"
        ) if _cat_badge else ""
        st.markdown(
            f"<div style='background:#0d1117;border:1px solid #21262d;border-radius:8px;"
            f"padding:10px 14px;margin:6px 0 10px;'>"
            f"<div style='margin-bottom:4px;'>{_cat_html}{_tf_html}</div>"
            f"<span style='font-size:13px;color:#c9d1d9;line-height:1.5;'>{_desc_full}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Динамические параметры стратегии
    strategy_params: dict = {}
    if selected_key == "sma_cross":
        pc1, pc2 = st.columns(2)
        strategy_params["fast"] = pc1.number_input("Быстрая MA", 3, 200, 20, step=1)
        strategy_params["slow"] = pc2.number_input("Медленная MA", 5, 500, 50, step=1)

    elif selected_key == "ema_cross":
        pc1, pc2 = st.columns(2)
        strategy_params["fast"] = pc1.number_input("Быстрая EMA", 3, 100, 12, step=1)
        strategy_params["slow"] = pc2.number_input("Медленная EMA", 5, 200, 26, step=1)

    elif selected_key == "rsi":
        pc1, pc2, pc3 = st.columns(3)
        strategy_params["period"] = pc1.number_input("Период", 5, 50, 14, step=1)
        strategy_params["oversold"] = float(pc2.number_input("Перепроданность", 10, 45, 30, step=1))
        strategy_params["overbought"] = float(pc3.number_input("Перекупленность", 55, 90, 70, step=1))

    elif selected_key == "macd":
        pc1, pc2, pc3 = st.columns(3)
        strategy_params["fast"] = pc1.number_input("Быстрая EMA", 5, 50, 12, step=1)
        strategy_params["slow"] = pc2.number_input("Медленная EMA", 10, 100, 26, step=1)
        strategy_params["signal_period"] = pc3.number_input("Сигнальная", 3, 20, 9, step=1)

    elif selected_key == "bollinger_scalp":
        pc1, pc2 = st.columns(2)
        strategy_params["period"] = pc1.number_input("Период BB", 5, 100, 20, step=1)
        strategy_params["num_std"] = float(
            pc2.number_input("Кол-во σ", 1.0, 4.0, 2.0, step=0.1, format="%.1f")
        )

    elif selected_key == "stoch_ema_scalp":
        pc1, pc2, pc3 = st.columns(3)
        strategy_params["k_period"] = pc1.number_input("Период %K", 5, 50, 14, step=1)
        strategy_params["d_period"] = pc2.number_input("Период %D", 2, 20, 3, step=1)
        strategy_params["trend_period"] = pc3.number_input("Тренд EMA", 10, 200, 50, step=1)
        pc4, pc5 = st.columns(2)
        strategy_params["oversold"] = float(pc4.number_input("Перепроданность", 5, 45, 25, step=1))
        strategy_params["overbought"] = float(pc5.number_input("Перекупленность", 55, 95, 75, step=1))

    elif selected_key == "vwap_bounce":
        pc1, pc2 = st.columns(2)
        strategy_params["window"] = pc1.number_input("Окно VWAP (баров)", 10, 200, 48, step=1)
        strategy_params["deviation"] = float(
            pc2.number_input("Отклонение %", 0.1, 5.0, 0.4, step=0.1, format="%.1f") / 100.0
        )

    elif selected_key == "turtle_soup":
        pc1, pc2 = st.columns(2)
        strategy_params["n_bars"] = pc1.number_input("Период минимума", 5, 100, 20, step=1)
        strategy_params["exit_ema"] = pc2.number_input("Выход EMA", 2, 50, 5, step=1)

    elif selected_key == "raschke_80_20":
        pc1, pc2 = st.columns(2)
        strategy_params["threshold"] = float(
            pc1.number_input("Порог 80-20 (%)", 5, 40, 20, step=1) / 100.0
        )
        strategy_params["exit_ema"] = pc2.number_input("Выход EMA", 2, 50, 5, step=1)

    elif selected_key.startswith("custom__"):
        _cn = selected_key[8:]
        _ci = st.session_state.custom_strategies.get(_cn, {})
        _cls = _ci.get("cls")
        _default_params = _ci.get("params", {})
        _strat_display_name = getattr(_cls, "name", None) or _cn
        st.caption(f"Параметры стратегии: **{_strat_display_name}**")
        if _default_params:
            _p_cols = st.columns(min(len(_default_params), 3))
            for idx, (pname, pdefault) in enumerate(_default_params.items()):
                col = _p_cols[idx % len(_p_cols)]
                if isinstance(pdefault, bool):
                    strategy_params[pname] = col.checkbox(pname, value=pdefault)
                elif isinstance(pdefault, int):
                    strategy_params[pname] = col.number_input(pname, value=pdefault, step=1)
                elif isinstance(pdefault, float):
                    _step = 0.001 if abs(pdefault) < 0.1 else 0.01
                    strategy_params[pname] = col.number_input(
                        pname, value=pdefault, step=_step, format="%.4f"
                    )
                elif isinstance(pdefault, str):
                    if pname == "direction" and pdefault in ("both", "long", "short"):
                        strategy_params[pname] = col.selectbox(
                            pname, ["both", "long", "short"],
                            index=["both", "long", "short"].index(pdefault),
                        )
                    else:
                        strategy_params[pname] = col.text_input(pname, value=pdefault)
                else:
                    strategy_params[pname] = col.text_input(pname, value=str(pdefault))
        else:
            st.caption("Параметры не обнаружены — используются значения по умолчанию.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # СВОЯ СТРАТЕГИЯ — исполняемый блок
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("📝  Своя стратегия", expanded=False):
        # ── Список загруженных стратегий ──────────────────────────────────────
        if st.session_state.custom_strategies:
            st.markdown("**Загруженные стратегии:**")
            for _cn, _ci in list(st.session_state.custom_strategies.items()):
                _cls_obj = _ci.get("cls")
                _desc_txt = getattr(_cls_obj, "description", "") or "—"
                _row_l, _row_r = st.columns([3, 1])
                _row_l.markdown(f"**{_cn}** — *{_desc_txt}*")
                if _row_r.button("🗑", key=f"del_strategy_{_cn}", help=f"Удалить {_cn}"):
                    del st.session_state.custom_strategies[_cn]
                    _delete_user_strategy_file(_cn)
                    if st.session_state.selected_strategy == f"custom__{_cn}":
                        st.session_state.selected_strategy = "sma_cross"
                    _log(f"Стратегия удалена: {_cn}")
                    st.rerun()
            st.divider()

        st.caption(
            "Загрузите один или несколько .py файлов или вставьте код. "
            "Класс должен наследовать `BaseStrategy` и реализовывать `generate_signals(df)`."
        )

        _uploads = st.file_uploader(
            "Загрузить .py файл(ы)",
            type=["py"],
            key="strategy_uploader",
            accept_multiple_files=True,
            help="Можно выбрать сразу несколько файлов — каждый будет загружен как отдельная стратегия.",
        )
        if _uploads:
            if len(_uploads) > 1:
                # Batch mode: auto-import all files directly
                _batch_ok = 0
                for _upload in _uploads:
                    _up_code = _upload.read().decode("utf-8")
                    try:
                        _cls_b, _params_b = _load_custom_strategy(_up_code)
                        st.session_state.custom_strategies[_cls_b.__name__] = {
                            "cls": _cls_b, "params": _params_b, "code": _up_code,
                        }
                        _save_user_strategy(_cls_b.__name__, _up_code)
                        st.session_state.selected_strategy = f"custom__{_cls_b.__name__}"
                        _log(f"Загружена: {_cls_b.__name__}  ({_upload.name})")
                        _batch_ok += 1
                    except Exception as _be:
                        st.error(f"❌ {_upload.name}: {_be}")
                if _batch_ok:
                    st.success(f"✅  Пакетная загрузка: {_batch_ok} стратегий добавлено")
                    st.rerun()
            else:
                # Single file: load into editor for preview
                _up_code_single = _uploads[0].read().decode("utf-8")
                st.session_state.custom_code = _up_code_single
                _log(f"Файл загружен: {_uploads[0].name}")

        _code_input = st.text_area(
            "Код стратегии",
            height=200,
            label_visibility="collapsed",
            placeholder=(
                "from strategies.base import BaseStrategy\nimport pandas as pd\n\n"
                "class MyStrategy(BaseStrategy):\n"
                "    def __init__(self, period: int = 14):\n"
                "        self.period = period\n\n"
                "    def generate_signals(self, df):\n"
                "        signal = pd.Series(0, index=df.index)\n"
                "        # signal[...] = 1  # вход\n"
                "        # signal[...] = -1 # выход\n"
                "        return signal\n\n"
                "    def get_params(self):\n"
                "        return {'period': self.period}"
            ),
            key="custom_code",
        )

        _activate_btn = st.button(
            "▶️  Добавить стратегию",
            use_container_width=True,
            type="primary",
            disabled=not bool(st.session_state.custom_code.strip()),
        )
        if _activate_btn:
            try:
                cls, params = _load_custom_strategy(st.session_state.custom_code)
                st.session_state.custom_strategies[cls.__name__] = {
                    "cls": cls, "params": params, "code": st.session_state.custom_code,
                }
                _save_user_strategy(cls.__name__, st.session_state.custom_code)
                st.session_state.selected_strategy = f"custom__{cls.__name__}"
                _log(f"Стратегия добавлена: {cls.__name__}  параметры: {params}")
                st.rerun()
            except ValueError as exc:
                st.error(f"❌  {exc}")
            except Exception as exc:
                st.error(f"❌  Неожиданная ошибка: {exc}")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # ИСТОЧНИК ДАННЫХ
    # ══════════════════════════════════════════════════════════════════════════
    st.subheader("📡  Источник данных")

    data_mode = st.radio(
        "Источник",
        ["Синтетические данные", "Загрузить CSV", "Получить с биржи"],
        label_visibility="collapsed",
    )

    df_loaded: pd.DataFrame | None = None

    if data_mode == "Синтетические данные":
        st.caption(
            f"Синтетические OHLCV · **{timeframe}** · **{_n_bars:,} баров** — данные сгенерируются при запуске бэктеста"
            if _dates_ok else "Исправьте диапазон дат выше."
        )

    elif data_mode == "Загрузить CSV":
        st.caption("Колонки: timestamp, open, high, low, close, volume")
        csv_file = st.file_uploader("Загрузить OHLCV CSV", type=["csv"])
        if csv_file is not None:
            try:
                dm = DataManager()
                df_loaded = dm.load_from_csv(csv_file.read())
                st.session_state.df = df_loaded
                st.session_state.data_label = f"CSV: {csv_file.name}  ({len(df_loaded):,} баров)"
                st.session_state.data_symbol = csv_file.name.rsplit(".", 1)[0]
                st.session_state.data_source = "csv"
                st.session_state.backtest_results = None
                _log(f"CSV: {csv_file.name}, {len(df_loaded)} баров")
                st.success(f"Загружено {len(df_loaded):,} баров")
            except Exception as exc:
                st.error(f"Ошибка CSV: {exc}")

    else:  # Получить с биржи
        exc_sel = st.selectbox("Биржа", ["binance", "bybit"])
        sym_sel = st.selectbox("Торговая пара", TOP_50_PAIRS, index=0)

        _api_k, _api_s = _get_api(exc_sel)
        _has_api = bool(_api_k and _api_s)

        if _has_api:
            st.success(f"🔑 API ключи {exc_sel.title()} активны")
        else:
            st.info("📌 Публичный режим — API ключ не нужен")

        if _dates_ok:
            st.caption(f"Будет загружено ~{_n_bars:,} баров ({timeframe}) постранично при запуске бэктеста.")

    # Статус данных
    if st.session_state.df is not None:
        _df = st.session_state.df
        st.success(
            f"✅  {st.session_state.data_label}\n\n"
            f"Период: {_df.index[0].date()} → {_df.index[-1].date()}"
        )
        _csv_fname = export_analysis_safe_filename(st.session_state.data_label) + ".csv"
        st.download_button(
            "💾  Скачать данные CSV",
            data=_df.reset_index().to_csv(index=False).encode("utf-8"),
            file_name=_csv_fname,
            mime="text/csv",
            use_container_width=True,
        )
    else:
        st.info("⏳  Данные не загружены")


# ─── ДИНАМИЧЕСКИЙ РАЗДЕЛ ИНСТРУКЦИИ: СТРАТЕГИИ ────────────────────────────────
# Runs after left_col so STRATEGY_LABELS_RU / STRATEGY_CATEGORIES_MAP are in scope
with st.sidebar:
    _dyn_cat_order = [
        "📈 Тренд",
        "🔄 Возврат к среднему",
        "⚡ Скальпинг",
        "🔃 Контртренд",
        "🆕 Своя",
    ]
    _dyn_cat_hints = {
        "📈 Тренд":             "Работают когда рынок направленно движется",
        "🔄 Возврат к среднему": "Расчёт на коррекцию после перепроданности",
        "⚡ Скальпинг":          "Быстрые сделки на волатильности (M15–1H)",
        "🔃 Контртренд":         "Ловля разворотов и ложных пробоев",
        "🆕 Своя":               "Ваши загруженные стратегии",
    }
    _dyn_cat_buckets: dict[str, list] = {c: [] for c in _dyn_cat_order}
    for _dk, _dlabel in STRATEGY_LABELS_RU.items():
        _dcat = STRATEGY_CATEGORIES_MAP.get(_dk, "")
        if _dcat in _dyn_cat_buckets:
            _ddesc = STRATEGY_DESC_FULL.get(_dk, "")
            _dtf   = STRATEGY_TIMEFRAMES_REC.get(_dk, "")
            _dyn_cat_buckets[_dcat].append((_dlabel, _ddesc, _dtf))

    _total_strats = sum(len(v) for v in _dyn_cat_buckets.values())
    with st.expander(f"🎯  Стратегии по категориям ({_total_strats})", expanded=False):
        for _dcat in _dyn_cat_order:
            _items = _dyn_cat_buckets[_dcat]
            if not _items:
                continue
            _hint = _dyn_cat_hints.get(_dcat, "")
            st.markdown(
                f"<div style='margin:8px 0 4px;'>"
                f"<span style='font-weight:700;font-size:13px;'>{_dcat}</span>"
                f"<span style='color:#666;font-size:11px;margin-left:6px;'>{_hint}</span></div>",
                unsafe_allow_html=True,
            )
            for _dlabel, _ddesc, _dtf in _items:
                _dtf_tag = (
                    f"<span style='color:#42a5f5;font-size:10px;margin-left:4px;'>⏱{_dtf}</span>"
                    if _dtf else ""
                )
                _desc_short = _ddesc[:55] + "…" if len(_ddesc) > 55 else _ddesc
                st.markdown(
                    f"<div style='padding:5px 0 5px 8px;border-left:2px solid #21262d;"
                    f"margin-bottom:4px;'>"
                    f"<span style='font-size:12px;font-weight:600;color:#c9d1d9;'>{_dlabel}</span>"
                    f"{_dtf_tag}<br>"
                    f"<span style='font-size:11px;color:#8b949e;'>{_desc_short}</span></div>",
                    unsafe_allow_html=True,
                )


# ══════════════════════════════════════════════════════════════════════════════
# ЦЕНТРАЛЬНАЯ КОЛОНКА — Настройка + Запуск
# ══════════════════════════════════════════════════════════════════════════════
with center_col:

    st.subheader("⚙️  Параметры бэктеста")

    cc1, cc2 = st.columns(2)
    with cc1:
        initial_capital = st.number_input(
            "Начальный капитал (USDT)", 100, 10_000_000, 10_000, step=500,
            help="Стартовый баланс портфеля в USDT. На него умножается % позиции.",
        )
    with cc2:
        fee_pct = st.number_input(
            "Комиссия (%)", 0.0, 5.0, 0.1, step=0.01, format="%.3f",
            help="Комиссия биржи за вход + выход (0.1% = 0.001). Списывается с каждой сделки.",
        )

    slippage_pct = st.number_input(
        "Проскальзывание (%)", 0.0, 5.0, 0.05, step=0.01, format="%.3f",
        help="Разница между ожидаемой и фактической ценой исполнения. Имитирует спред и задержку.",
    )
    st.selectbox(
        "Метод расчёта позиции",
        ["% от капитала", "Фиксированный", "На основе риска"],
        help="% от капитала — каждая сделка занимает фиксированный процент текущего баланса.",
    )
    st.text_input(
        "Исполнение", value="Маркет-ордер по открытию следующего бара", disabled=True,
        help="Сигнал фиксируется на закрытии бара i. Исполнение — по open бара i+1 (нет look-ahead).",
    )

    st.divider()

    # ── Параметры выхода ──────────────────────────────────────────────────────
    st.subheader("🛑  Выход из позиции")
    st.caption("Все параметры независимы. `0` = выключено.")

    ex1, ex2 = st.columns(2)
    with ex1:
        stop_loss_pct = st.number_input(
            "Стоп-лосс (%)", 0.0, 50.0, 0.0, step=0.1, format="%.1f",
            help="Фиксированный стоп ниже цены входа. Срабатывает если open ≤ SL.",
        )
    with ex2:
        take_profit_pct = st.number_input(
            "Тейк-профит (%)", 0.0, 200.0, 0.0, step=0.1, format="%.1f",
            help="Фиксированная цель выше цены входа. Срабатывает если open ≥ TP.",
        )

    ex3, ex4 = st.columns(2)
    with ex3:
        trailing_stop_pct = st.number_input(
            "Трейлинг-стоп (%)", 0.0, 50.0, 0.0, step=0.1, format="%.1f",
            help=(
                "X% ниже максимального close с момента входа. "
                "Уровень обновляется каждым баром, срабатывает по open следующего."
            ),
        )
    with ex4:
        hold_bars = st.number_input(
            "Hold Bars", 0, 10000, 0, step=1,
            help="Принудительно закрыть позицию через N баров после входа. 0 = выключено.",
        )

    _exits = []
    if stop_loss_pct > 0:
        _exits.append(f"🔴 SL {stop_loss_pct:.1f}%")
    if trailing_stop_pct > 0:
        _exits.append(f"🟠 Trail {trailing_stop_pct:.1f}%")
    if take_profit_pct > 0:
        _exits.append(f"🟢 TP {take_profit_pct:.1f}%")
    if hold_bars > 0:
        _exits.append(f"⏱ {hold_bars} баров")
    _exits.append("📊 Сигнал")
    st.caption("Приоритет: " + " → ".join(_exits))

    st.divider()

    # ── Кнопки управления ────────────────────────────────────────────────────
    st.subheader("🚀  Запуск")

    prepare_btn = st.button("📋  Подготовить команду CLI", use_container_width=True)

    _csv_mode = data_mode == "Загрузить CSV"
    _csv_ready = st.session_state.df is not None
    _run_disabled = not _dates_ok or (_csv_mode and not _csv_ready)
    run_btn = st.button(
        "▶️  Запустить бэктест",
        use_container_width=True,
        disabled=_run_disabled,
        type="primary",
        help=(
            "Загрузите CSV файл" if (_csv_mode and not _csv_ready)
            else "Исправьте диапазон дат" if not _dates_ok
            else "Запустить бэктест"
        ),
    )

    st.markdown(
        """
        <div style="
            background:#2a0a0a;border:1px solid #7a0000;border-radius:6px;
            padding:10px 16px;text-align:center;color:#ff6666;font-weight:600;
        ">
            🔒  Живая торговля — ЗАБЛОКИРОВАНА
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Генерация команды CLI ─────────────────────────────────────────────────
    if prepare_btn:
        cmd = (
            f"python -m backtesting.run_strategy \\\n"
            f"  --strategy {selected_key} \\\n"
            f"  --data-source local_csv \\\n"
            f"  --csv data/historical/btc_usdt.csv \\\n"
            f"  --start {start_date} --end {end_date} \\\n"
            f"  --capital {initial_capital} \\\n"
            f"  --fee {fee_pct / 100:.4f} --slippage {slippage_pct / 100:.4f}"
        )
        if stop_loss_pct > 0:
            cmd += f" \\\n  --stop-loss {stop_loss_pct / 100:.4f}"
        if take_profit_pct > 0:
            cmd += f" \\\n  --take-profit {take_profit_pct / 100:.4f}"
        if trailing_stop_pct > 0:
            cmd += f" \\\n  --trailing-stop {trailing_stop_pct / 100:.4f}"
        if hold_bars > 0:
            cmd += f" \\\n  --hold-bars {hold_bars}"
        for k, v in strategy_params.items():
            cmd += f" \\\n  --param {k}={v}"
        st.session_state.command_preview = cmd
        _log("CLI команда сформирована")

    # ── Запуск бэктеста ───────────────────────────────────────────────────────
    if run_btn and _dates_ok:
        _bt_df_raw: pd.DataFrame | None = None

        # 1. Load / generate data
        if data_mode == "Синтетические данные":
            _bt_df_raw = generate_sample_ohlcv(n_bars=_n_bars, start=str(start_date), freq=timeframe)
            st.session_state.df = _bt_df_raw
            st.session_state.data_label = f"Синтетика BTC/USDT {timeframe} — {_n_bars:,} баров"
            st.session_state.data_symbol = "BTC/USDT"
            st.session_state.data_source = "synthetic"
            _log(f"Синтетика: {_n_bars} баров, {timeframe}")

        elif data_mode == "Загрузить CSV":
            _bt_df_raw = st.session_state.df

        else:  # Получить с биржи
            _run_pb = st.progress(0, text="Подключение к бирже…")
            try:
                dm = DataManager()
                _bt_api_k, _bt_api_s = _get_api(exc_sel)

                def _on_run_progress(fetched: int, total: int) -> None:
                    _run_pb.progress(
                        min(fetched / max(total, 1), 1.0),
                        text=f"Загружено {fetched:,} / {total:,} баров…",
                    )

                _bt_df_raw = dm.load_from_exchange_all(
                    exc_sel, sym_sel, timeframe,
                    start=str(start_date), end=str(end_date),
                    api_key=_bt_api_k, api_secret=_bt_api_s,
                    on_progress=_on_run_progress,
                )
                _run_pb.empty()
                st.session_state.df = _bt_df_raw
                st.session_state.data_label = (
                    f"{exc_sel.title()} {sym_sel} {timeframe} — {len(_bt_df_raw):,} баров"
                )
                st.session_state.data_symbol = sym_sel
                st.session_state.data_source = exc_sel
                _log(f"Загружено {len(_bt_df_raw)} баров: {exc_sel} {sym_sel} {timeframe}")
            except Exception as _load_exc:
                _run_pb.empty()
                st.error(f"Ошибка загрузки данных: {_load_exc}")
                _log(f"Ошибка загрузки: {_load_exc}")

        # 2. Run backtest on loaded data
        if _bt_df_raw is not None:
            with st.spinner("Выполняется бэктест…"):
                try:
                    df_bt = _bt_df_raw[
                        (_bt_df_raw.index >= pd.Timestamp(start_date))
                        & (_bt_df_raw.index <= pd.Timestamp(end_date))
                    ]
                    if df_bt.empty:
                        st.error("Нет данных в выбранном диапазоне.")
                    else:
                        if selected_key.startswith("custom__"):
                            _cn = selected_key[8:]
                            _ci = st.session_state.custom_strategies.get(_cn, {})
                            if not _ci.get("cls"):
                                raise ValueError(f"Стратегия '{_cn}' не найдена.")
                            strategy = _ci["cls"](**strategy_params)
                        else:
                            strategy = get_strategy(selected_key, strategy_params)

                        signals = strategy.generate_signals(df_bt)
                        equity_curve, trades = run_backtest(
                            df_bt, signals,
                            initial_capital=float(initial_capital),
                            fee=fee_pct / 100.0,
                            slippage=slippage_pct / 100.0,
                            stop_loss=stop_loss_pct / 100.0,
                            take_profit=take_profit_pct / 100.0,
                            trailing_stop=trailing_stop_pct / 100.0,
                            hold_bars=int(hold_bars),
                        )
                        metrics = calculate_metrics(equity_curve, trades, float(initial_capital))
                        st.session_state.backtest_results = {
                            "equity_curve": equity_curve,
                            "trades": trades,
                            "metrics": metrics,
                            "df_bt": df_bt,
                            "strategy_label": selected_label,
                            "strategy_key": selected_key,
                            "strategy_params": strategy_params,
                            "bars": len(df_bt),
                            "symbol": st.session_state.data_symbol,
                            "timeframe": timeframe,
                            "source": st.session_state.data_source,
                            "backtest_params": {
                                "initial_capital": float(initial_capital),
                                "commission_pct": fee_pct,
                                "slippage_pct": slippage_pct,
                                "position_sizing": "% от капитала",
                                "stop_loss_pct": stop_loss_pct,
                                "take_profit_pct": take_profit_pct,
                                "trailing_stop_pct": trailing_stop_pct,
                                "hold_bars": int(hold_bars),
                            },
                        }
                        _log(
                            f"Бэктест — {selected_label}  "
                            f"сделок: {metrics['total_trades']}  "
                            f"доходность: {metrics['total_return']}%"
                        )
                except Exception as exc:
                    st.error(f"Ошибка бэктеста: {exc}")
                    _log(f"Ошибка: {exc}")

    # ── Предпросмотр команды CLI ──────────────────────────────────────────────
    st.divider()
    st.subheader(
        "💻  Команда CLI",
        help=(
            "Команда для запуска того же бэктеста из терминала (командной строки) "
            "без UI. Полезна для автоматизации, CI/CD и массовых тестов. "
            "Скопируйте команду и выполните в корне проекта."
        ),
    )
    if st.session_state.command_preview:
        st.code(st.session_state.command_preview, language="bash")
        st.caption("Запускать из корня репозитория: `cd /path/to/project && python -m backtesting.run_strategy …`")
    else:
        st.markdown(
            "<div style='background:#0d1117;border:1px solid #21262d;border-radius:8px;"
            "padding:14px 16px;font-family:monospace;font-size:12px;color:#8b949e;'>"
            "$ <span style='color:#3fb950'>ожидание конфигурации…</span><br>"
            "<span style='color:#555;'>→ нажмите 📋 Подготовить команду CLI</span></div>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ПРАВАЯ КОЛОНКА — Результаты + Журнал
# ══════════════════════════════════════════════════════════════════════════════
with right_col:

    st.subheader("📊  Результаты")

    res = st.session_state.backtest_results
    if res:
        m = res["metrics"]
        st.caption(f"{res['strategy_label']}  |  {res['bars']:,} баров")

        _df_bt_metric = res.get("df_bt")
        _bh_return_pct = None
        if _df_bt_metric is not None and len(_df_bt_metric) >= 2:
            _bh_p0 = float(_df_bt_metric["open"].iloc[0])
            _bh_p1 = float(_df_bt_metric["close"].iloc[-1])
            if _bh_p0 > 0:
                _bh_return_pct = (_bh_p1 / _bh_p0 - 1.0) * 100.0

        METRIC_DEFS = [
            ("Общая доходность", f"{m['total_return']:+.2f}%",
             "green" if m["total_return"] >= 0 else "red"),
            ("Макс. просадка", f"{m['max_drawdown']:.2f}%", "red"),
            ("Коэф. Шарпа", str(m["sharpe_ratio"]),
             "green" if m["sharpe_ratio"] >= 1 else "orange"),
            ("Коэф. Сортино", str(m["sortino_ratio"]),
             "green" if m["sortino_ratio"] >= 1 else "orange"),
            ("Профит-фактор", str(m["profit_factor"]),
             "green" if str(m["profit_factor"]) == "∞" or
             float(str(m["profit_factor"]).replace("∞", "99")) >= 1.5 else "orange"),
            ("% выигрышных", f"{m['win_rate']:.1f}%",
             "green" if m["win_rate"] >= 50 else "orange"),
            ("Всего сделок", str(m["total_trades"]), "default"),
            ("Ср. доход/сделка", f"{m['avg_trade_return']:+.2f}%",
             "green" if m["avg_trade_return"] >= 0 else "red"),
            ("Expectancy", f"{m.get('expectancy_pct', 0):+.3f}%",
             "green" if m.get("expectancy_pct", 0) >= 0 else "red"),
            ("Итог капитал", f"${m['final_equity']:,.2f}", "default"),
        ]
        if _bh_return_pct is not None:
            METRIC_DEFS.append((
                "B&H доходность",
                f"{_bh_return_pct:+.2f}%",
                "green" if _bh_return_pct >= 0 else "red",
            ))
        colour_map = {"green": "#4caf50", "red": "#f44336", "orange": "#ff9800", "default": "#aaaaaa"}
        for label, value, colour in METRIC_DEFS:
            mc, vc = st.columns([1.5, 1])
            mc.markdown(f"<small><b>{label}</b></small>", unsafe_allow_html=True)
            vc.markdown(
                f"<span style='font-family:monospace;color:{colour_map[colour]};font-size:13px;'>"
                f"{value}</span>",
                unsafe_allow_html=True,
            )
    else:
        for label in ["Общая доходность", "Макс. просадка", "Коэф. Шарпа",
                      "Профит-фактор", "% выигрышных", "Всего сделок"]:
            mc, vc = st.columns([1.5, 1])
            mc.markdown(f"<small><b>{label}</b></small>", unsafe_allow_html=True)
            vc.markdown("<code style='color:#444;font-size:11px;'>—</code>", unsafe_allow_html=True)

    st.divider()
    st.subheader("📋  Журнал")

    if st.button("🗑  Очистить", use_container_width=True):
        st.session_state.ui_logs = []
        st.rerun()

    if st.session_state.ui_logs:
        st.text_area("logs", value="\n".join(st.session_state.ui_logs),
                     height=220, label_visibility="collapsed", disabled=True)
    else:
        st.markdown("<small style='color:#555;'>Журнал пуст.</small>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# ПОЛНАЯ ШИРИНА — Кривая капитала + Журнал сделок
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.backtest_results:
    res = st.session_state.backtest_results
    equity_curve: pd.DataFrame = res["equity_curve"]
    trades = res["trades"]

    st.divider()
    st.subheader("📈  Кривая капитала")

    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=equity_curve.index, y=equity_curve["equity"],
            mode="lines", name="Капитал",
            line=dict(color="#00e676", width=1.5),
            fill="tozeroy", fillcolor="rgba(0,230,118,0.06)",
        ))
        fig.add_hline(
            y=float(equity_curve["equity"].iloc[0]),
            line_dash="dot", line_color="#555",
            annotation_text="Начальный капитал",
        )
        _df_bt_chart = res.get("df_bt")
        _bh_legend = False
        if _df_bt_chart is not None and len(_df_bt_chart) >= 2:
            _bh_p0 = float(_df_bt_chart["open"].iloc[0])
            if _bh_p0 > 0:
                _bh_qty = float(equity_curve["equity"].iloc[0]) / _bh_p0
                _bh_eq = _df_bt_chart["close"] * _bh_qty
                fig.add_trace(go.Scatter(
                    x=_bh_eq.index, y=_bh_eq,
                    mode="lines", name="Buy & Hold",
                    line=dict(color="#9e9e9e", width=1.2, dash="dash"),
                ))
                _bh_legend = True
        fig.update_layout(
            template="plotly_dark", height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title=None, yaxis_title="Портфель (USDT)", showlegend=_bh_legend,
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.line_chart(equity_curve["equity"])

    if trades:
        from collections import Counter
        _reason_labels = {
            "signal":        "📊 Сигнал",
            "stop_loss":     "🔴 Стоп-лосс",
            "trailing_stop": "🟠 Трейлинг",
            "take_profit":   "🟢 Тейк-профит",
            "hold_bars":     "⏱ Hold Bars",
            "end_of_data":   "⏹ Конец данных",
            "":              "—",
        }
        reason_counts = Counter(t.exit_reason for t in trades)
        _summary = "  |  ".join(
            f"{_reason_labels.get(r, r)}: {c}"
            for r, c in sorted(reason_counts.items())
        )
        with st.expander(f"📄  Журнал сделок  ({len(trades)})  —  {_summary}", expanded=False):
            rows = [
                {
                    "Вход": str(t.entry_ts)[:16],
                    "Выход": str(t.exit_ts)[:16] if t.exit_ts else "Открыта",
                    "Цена входа": f"{t.entry_price:,.2f}",
                    "Цена выхода": f"{t.exit_price:,.2f}" if t.exit_price else "—",
                    "PnL (USDT)": f"{t.pnl:+.2f}",
                    "Доходность": f"{t.pnl_pct:+.2f}%",
                    "Причина": _reason_labels.get(t.exit_reason, t.exit_reason),
                }
                for t in trades
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()
    if st.session_state.df is not None:
        export_analysis_render_downloads(res, st.session_state.df)

    # ── AI Оценка ─────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("🤖  AI Оценка стратегии")
    st.caption("Автоматический анализ по метрикам — работает офлайн, без внешних API.")

    _eval = _ai_eval_strategy(res["metrics"])

    _ev_grade_col, _ev_details_col = st.columns([1, 2.5])

    with _ev_grade_col:
        st.markdown(
            f"<div style='text-align:center;padding:16px 8px;background:#161b22;"
            f"border:2px solid {_eval['grade_color']};border-radius:12px;'>"
            f"<div style='font-size:64px;font-weight:900;color:{_eval['grade_color']};line-height:1;'>"
            f"{_eval['grade']}</div>"
            f"<div style='font-size:13px;color:{_eval['grade_color']};margin-top:6px;font-weight:600;'>"
            f"{_eval['grade_text']}</div>"
            f"<div style='font-size:11px;color:#666;margin-top:4px;'>счёт {_eval['score']}/11</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with _ev_details_col:
        if _eval["goods"]:
            st.markdown("**✅ Сильные стороны**")
            for g in _eval["goods"]:
                st.markdown(f"<span style='color:#4caf50;font-size:13px;'>▸ {g}</span>",
                            unsafe_allow_html=True)

        if _eval["bads"]:
            st.markdown("**❌ Слабые стороны**")
            for b in _eval["bads"]:
                st.markdown(f"<span style='color:#f44336;font-size:13px;'>▸ {b}</span>",
                            unsafe_allow_html=True)

        if not _eval["goods"] and not _eval["bads"]:
            st.info("Нет данных для оценки — запустите бэктест.")

    st.markdown(
        f"<div style='background:#161b22;border-left:3px solid #ff9800;"
        f"padding:10px 14px;border-radius:0 6px 6px 0;margin-top:10px;'>"
        f"<span style='color:#ff9800;font-size:12px;font-weight:600;'>⚠️ ГЛАВНАЯ ПРОБЛЕМА</span><br>"
        f"<span style='font-size:13px;'>{_eval['problem']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='background:#161b22;border-left:3px solid #42a5f5;"
        f"padding:10px 14px;border-radius:0 6px 6px 0;margin-top:8px;'>"
        f"<span style='color:#42a5f5;font-size:12px;font-weight:600;'>🔬 СЛЕДУЮЩИЙ ТЕСТ</span><br>"
        f"<span style='font-size:13px;'>{_eval['next_test']}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    with st.expander("📋  Промт для внешнего AI (ChatGPT / Claude)", expanded=False):
        _eval_prompt = (
            f"Оцени стратегию бэктеста по следующим метрикам:\n\n"
            f"- Стратегия: {res.get('strategy_label', '—')}\n"
            f"- Инструмент: {res.get('symbol', '—')} {res.get('timeframe', '')}\n"
            f"- Баров: {res.get('bars', '—')}\n\n"
            f"**Метрики:**\n"
            f"- Доходность: {res['metrics'].get('total_return', 0):+.2f}%\n"
            f"- Макс. просадка: {res['metrics'].get('max_drawdown', 0):.2f}%\n"
            f"- Шарп: {res['metrics'].get('sharpe_ratio', 0):.3f}\n"
            f"- Сортино: {res['metrics'].get('sortino_ratio', 0):.3f}\n"
            f"- Профит-фактор: {res['metrics'].get('profit_factor', 0)}\n"
            f"- Win Rate: {res['metrics'].get('win_rate', 0):.1f}%\n"
            f"- Сделок: {res['metrics'].get('total_trades', 0)}\n"
            f"- Ср. доход/сделка: {res['metrics'].get('avg_trade_return', 0):+.3f}%\n"
            f"- Матожидание: {res['metrics'].get('expectancy_pct', 0):+.3f}%\n"
            f"- Итог капитал: ${res['metrics'].get('final_equity', 0):,.2f}\n\n"
            f"Дай вывод:\n"
            f"1. Вердикт A/B/C/D\n"
            f"2. Что хорошо\n"
            f"3. Что плохо\n"
            f"4. Главная проблема\n"
            f"5. Один следующий тест"
        )
        st.code(_eval_prompt, language="text")


# ══════════════════════════════════════════════════════════════════════════════
# БУМАЖНАЯ ТОРГОВЛЯ
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📄  Бумажная торговля")
st.caption(
    "Симуляция торговли на реальных данных с биржи. "
    "Виртуальный капитал, сигналы и сделки — без реального исполнения. "
    "Считается только по закрытым барам (текущий формирующийся бар отбрасывается), "
    "с учётом комиссии и проскальзывания из раздела «Параметры бэктеста»."
)

_pt_cfg_col, _pt_stat_col = st.columns([1.5, 1])

with _pt_cfg_col:
    st.markdown("**Конфигурация**")
    _pt_c1, _pt_c2, _pt_c3 = st.columns(3)
    _pt_exchange = _pt_c1.selectbox("Биржа", ["binance", "bybit"], key="pt_exch_sel")
    _pt_symbol   = _pt_c2.selectbox("Пара", TOP_50_PAIRS, key="pt_sym_sel")
    _pt_tf       = _pt_c3.selectbox("Таймфрейм", list(_TF_SECONDS.keys()), key="pt_tf_sel")
    _pt_c4, _pt_c5 = st.columns(2)
    _pt_capital  = _pt_c4.number_input(
        "Виртуальный капитал (USDT)", 100, 1_000_000, 10_000, step=500, key="pt_cap_num"
    )
    _pt_lookback = _pt_c5.number_input(
        "Баров истории", 50, 1000, 200, step=50, key="pt_look_num"
    )
    _pt_strat_label = STRATEGY_LABELS_RU.get(selected_key, selected_key)
    st.caption(f"Стратегия: **{_pt_strat_label}** · параметры из раздела выше")

with _pt_stat_col:
    _pt_sess = st.session_state.pt_session
    if _pt_sess:
        _pt_status_color = "🟢" if _pt_sess["in_position"] else "⚪"
        _pt_status_text  = "В позиции" if _pt_sess["in_position"] else "Нет позиции"
        st.markdown(f"**Статус:** {_pt_status_color} {_pt_status_text}")
        if _pt_sess["in_position"]:
            _unr = _pt_sess["unrealized_pnl"]
            _pct = _pt_sess["unrealized_pct"]
            _col = "green" if _unr >= 0 else "red"
            st.markdown(
                f"Вход: **{_pt_sess['entry_time']}** @ **{_pt_sess['entry_price']:,}**\n\n"
                f"Текущая цена: **{_pt_sess['current_price']:,}**",
            )
            st.markdown(
                f"<span style='color:{'#4caf50' if _unr >= 0 else '#f44336'};font-size:16px;font-weight:600;'>"
                f"Нереализованный PnL: {_unr:+.2f} USDT ({_pct:+.2f}%)</span>",
                unsafe_allow_html=True,
            )
        st.metric(
            "Виртуальный капитал",
            f"${_pt_sess['capital']:,.2f}",
            delta=f"{_pt_sess['capital'] - _pt_capital:+.2f}",
        )
        st.caption(
            f"📊 Сделок: {len(_pt_sess['trades'])}  |  "
            f"Баров: {_pt_sess['bars_loaded']}  |  "
            f"Обновлено: {_pt_sess['last_updated']}"
        )
    else:
        st.info("Нажмите ▶️ Запустить для начала симуляции")

_pt_b1, _pt_b2, _pt_b3 = st.columns(3)
_pt_api_k, _pt_api_s = _get_api(_pt_exchange)

if _pt_b1.button(
    "▶️  Запустить / Обновить",
    key="pt_start_btn", use_container_width=True, type="primary",
):
    with st.spinner(f"Загружаем {_pt_lookback} баров {_pt_symbol} {_pt_tf} с {_pt_exchange}…"):
        try:
            _pt_result = _pt_run(
                _pt_exchange, _pt_symbol, _pt_tf,
                selected_key, strategy_params,
                _pt_capital, _pt_lookback,
                _pt_api_k, _pt_api_s,
                fee=fee_pct / 100.0, slippage=slippage_pct / 100.0,
            )
            st.session_state.pt_session = _pt_result
            st.session_state.pt_active = True
            _log(
                f"Бумажная торговля: {_pt_symbol} {_pt_tf}  "
                f"{'в позиции' if _pt_result['in_position'] else 'без позиции'}  "
                f"сделок: {len(_pt_result['trades'])}"
            )
            st.rerun()
        except Exception as _pt_exc:
            st.error(f"❌  {_pt_exc}")

_pt_b2.markdown("")

if _pt_b3.button(
    "⏹  Остановить",
    key="pt_stop_btn", use_container_width=True,
    disabled=st.session_state.pt_session is None,
):
    st.session_state.pt_session = None
    st.session_state.pt_active = False
    _log("Бумажная торговля остановлена")
    st.rerun()

if st.session_state.pt_session:
    _pt_s = st.session_state.pt_session

    # Trades table
    if _pt_s["trades"]:
        with st.expander(f"📄  Виртуальные сделки ({len(_pt_s['trades'])})", expanded=True):
            _pt_df_trades = pd.DataFrame(_pt_s["trades"])
            st.dataframe(_pt_df_trades, use_container_width=True, hide_index=True)
    else:
        st.info("Сделок ещё не было — стратегия не подала сигналов за выбранный период.")

    # Signals chart
    try:
        import plotly.graph_objects as _go2
        _pt_df_chart = _pt_s["df"].tail(min(150, len(_pt_s["df"])))
        _pt_sig = _pt_s["signals"].reindex(_pt_df_chart.index).fillna(0)
        _pt_fig = _go2.Figure()
        _pt_fig.add_trace(_go2.Scatter(
            x=_pt_df_chart.index, y=_pt_df_chart["close"],
            mode="lines", name="Цена", line=dict(color="#aaaaaa", width=1.2),
        ))
        _pt_entries = _pt_sig[_pt_sig == 1]
        _pt_exits   = _pt_sig[_pt_sig == -1]
        if not _pt_entries.empty:
            _pt_fig.add_trace(_go2.Scatter(
                x=_pt_entries.index,
                y=_pt_df_chart.loc[_pt_entries.index, "close"],
                mode="markers", name="Вход",
                marker=dict(symbol="triangle-up", color="#4caf50", size=12),
            ))
        if not _pt_exits.empty:
            _pt_fig.add_trace(_go2.Scatter(
                x=_pt_exits.index,
                y=_pt_df_chart.loc[_pt_exits.index, "close"],
                mode="markers", name="Выход",
                marker=dict(symbol="triangle-down", color="#f44336", size=12),
            ))
        _pt_fig.update_layout(
            template="plotly_dark", height=300,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title=None, yaxis_title="Цена",
            legend=dict(orientation="h", y=1.02, x=0),
        )
        st.plotly_chart(_pt_fig, use_container_width=True)
    except ImportError:
        st.line_chart(_pt_s["df"]["close"].tail(150))

    # Download virtual trades as CSV
    if _pt_s["trades"]:
        st.download_button(
            "💾  Скачать виртуальные сделки CSV",
            data=pd.DataFrame(_pt_s["trades"]).to_csv(index=False).encode("utf-8"),
            file_name=f"paper_{_pt_s['symbol'].replace('/', '_')}_{_pt_s['timeframe']}.csv",
            mime="text/csv",
        )


# ══════════════════════════════════════════════════════════════════════════════
# ОПТИМИЗАЦИЯ ПАРАМЕТРОВ
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("🔬  Оптимизация параметров")
st.caption(
    "Перебор комбинаций параметров выбранной стратегии на загруженных данных. "
    "Результаты сортируются по выбранной метрике."
)

if st.session_state.df is None:
    st.warning("⚠️  Сначала загрузите данные (левая колонка).")
else:
    _opt_df_src = st.session_state.df

    _opt_l, _opt_r = st.columns([1.6, 1])

    with _opt_l:
        st.markdown("**Стратегия и параметры**")

        _opt_strat_options = STRATEGY_LABELS_RU
        _opt_strat_labels  = list(_opt_strat_options.values())
        _opt_strat_keys    = list(_opt_strat_options.keys())
        _opt_saved = st.session_state.opt_strategy
        if _opt_saved not in _opt_strat_keys:
            _opt_saved = "sma_cross"
        _opt_saved_idx = _opt_strat_keys.index(_opt_saved)
        _opt_sel_label = st.selectbox(
            "Стратегия", _opt_strat_labels, index=_opt_saved_idx, key="opt_strat_sel"
        )
        _opt_sel_key = _opt_strat_keys[_opt_strat_labels.index(_opt_sel_label)]
        st.session_state.opt_strategy = _opt_sel_key

        # Param grid: built-in strategies use _OPT_DEFAULTS;
        # custom strategies derive ranges from __init__ default values
        if _opt_sel_key.startswith("custom__"):
            _cn_opt = _opt_sel_key[8:]
            _ci_opt = st.session_state.custom_strategies.get(_cn_opt, {})
            _raw_defaults = _ci_opt.get("params", {})
            _opt_param_defs: dict = {}
            for _pn, _pd in _raw_defaults.items():
                if isinstance(_pd, bool):
                    continue  # booleans can't be range-optimised
                elif isinstance(_pd, int):
                    _lo = max(1, _pd // 2)
                    _hi = max(_pd * 3, _lo + 1)
                    _st = max(1, (_hi - _lo) // 10)
                    _opt_param_defs[_pn] = (_lo, _hi, _st, "int")
                elif isinstance(_pd, float):
                    if _pd == 0.0:
                        _lo, _hi, _st = 0.0, 0.5, 0.05
                    else:
                        _lo = round(_pd * 0.5, 6)
                        _hi = round(_pd * 2.0, 6)
                        _st = max(0.001, round((_hi - _lo) / 10.0, 6))
                    _opt_param_defs[_pn] = (_lo, _hi, _st, "float")
        else:
            _opt_param_defs = _OPT_DEFAULTS.get(_opt_sel_key, {})
        _opt_grid: dict[str, list] = {}
        _opt_total_combos = 1

        if _opt_param_defs:
            st.markdown("**Диапазоны параметров:**")
            for _pname, (_pmin, _pmax, _pstep, _ptype) in _opt_param_defs.items():
                _pc1, _pc2, _pc3, _pc4 = st.columns([1.2, 1.2, 1.2, 0.6])
                _imin = _pc1.number_input(
                    f"{_pname} min", value=_pmin,
                    step=1 if _ptype == "int" else 0.01, format="%g", key=f"opt_{_pname}_min"
                )
                _imax = _pc2.number_input(
                    f"{_pname} max", value=_pmax,
                    step=1 if _ptype == "int" else 0.01, format="%g", key=f"opt_{_pname}_max"
                )
                _istep = _pc3.number_input(
                    f"{_pname} step", value=_pstep,
                    min_value=1 if _ptype == "int" else 0.001,
                    step=1 if _ptype == "int" else 0.01,
                    format="%g", key=f"opt_{_pname}_step"
                )
                _ivals = _build_param_values(float(_imin), float(_imax), float(_istep), _ptype)
                _pc4.markdown(f"<br><small>{len(_ivals)} зн.</small>", unsafe_allow_html=True)
                _opt_grid[_pname] = _ivals
                _opt_total_combos *= len(_ivals)
        else:
            st.info("Для этой стратегии диапазоны не заданы.")

        _comb_color = "red" if _opt_total_combos > 500 else ("orange" if _opt_total_combos > 100 else "green")
        st.markdown(
            f"<span style='color:{'#4caf50' if _comb_color=='green' else '#ff9800' if _comb_color=='orange' else '#f44336'};'>"
            f"Комбинаций: **{_opt_total_combos}**</span>"
            + (" — много, запуск займёт время" if _opt_total_combos > 200 else ""),
            unsafe_allow_html=True,
        )

    with _opt_r:
        st.markdown("**Параметры бэктеста**")
        _opt_capital  = st.number_input("Капитал (USDT)", 100, 10_000_000, 10_000, step=500, key="opt_cap")
        _opt_fee      = st.number_input("Комиссия (%)", 0.0, 5.0, 0.1, step=0.01, format="%.3f", key="opt_fee")
        _opt_slip     = st.number_input("Проскальзывание (%)", 0.0, 5.0, 0.05, step=0.01, format="%.3f", key="opt_slip")
        _opt_sl       = st.number_input("Стоп-лосс (%)", 0.0, 50.0, 0.0, step=0.5, format="%.1f", key="opt_sl")
        _opt_tp       = st.number_input("Тейк-профит (%)", 0.0, 200.0, 0.0, step=0.5, format="%.1f", key="opt_tp")
        _opt_trail    = st.number_input("Трейлинг-стоп (%)", 0.0, 50.0, 0.0, step=0.5, format="%.1f", key="opt_trail")
        _opt_hold     = st.number_input("Hold Bars", 0, 10000, 0, step=1, key="opt_hold",
                                        help="Принудительно закрыть через N баров. 0 = выключено.")
        st.markdown("**Метрика оптимизации**")
        _opt_metric = st.radio(
            "Оптимизировать по:",
            ["Доходность", "Шарп", "Профит-фактор"],
            horizontal=True, key="opt_metric_radio",
        )

    _opt_date_ok = end_date >= start_date
    _opt_df_bt = _opt_df_src[
        (_opt_df_src.index >= pd.Timestamp(start_date))
        & (_opt_df_src.index <= pd.Timestamp(end_date))
    ] if _opt_date_ok else _opt_df_src

    _opt_run_disabled = (not _opt_param_defs) or (_opt_total_combos == 0) or (_opt_total_combos > 5000)
    if _opt_total_combos > 5000:
        st.error("Слишком много комбинаций (> 5000). Уменьшите диапазоны.")

    if st.button(
        f"🔬  Запустить оптимизацию  ({_opt_total_combos} комб.)",
        use_container_width=True, type="primary",
        disabled=_opt_run_disabled, key="opt_run_btn",
    ):
        _opt_pb = st.progress(0, text="Оптимизация…")

        def _opt_prog(done: int, total: int) -> None:
            _opt_pb.progress(min(done / max(total, 1), 1.0), text=f"Обработано {done}/{total}…")

        with st.spinner("Запуск оптимизации…"):
            try:
                _opt_res_df = _opt_run(
                    _opt_df_bt, _opt_sel_key, _opt_grid, _opt_metric,
                    _opt_capital, _opt_fee, _opt_slip, _opt_sl, _opt_tp, _opt_trail,
                    hold_bars=int(_opt_hold),
                    progress_cb=_opt_prog,
                )
                _opt_pb.empty()
                st.session_state.opt_results = _opt_res_df
                _log(
                    f"Оптимизация {_opt_sel_key}: {_opt_total_combos} комб.  "
                    f"лучшая {_opt_metric}: "
                    + (str(_opt_res_df.iloc[0].to_dict()) if len(_opt_res_df) else "нет результатов")
                )
                st.rerun()
            except Exception as _opt_exc:
                _opt_pb.empty()
                st.error(f"❌  Ошибка оптимизации: {_opt_exc}")

    if st.session_state.opt_results is not None:
        _res = st.session_state.opt_results
        if _res.empty:
            st.warning("Оптимизация не дала результатов — все комбинации вызвали ошибки.")
        else:
            st.success(f"✅  Найдено {len(_res)} комбинаций. Сортировка по: **{_opt_metric}**")

            # Highlight best row
            _param_cols  = [c for c in _res.columns if c not in ("доходность_%", "шарп", "просадка_%", "проф_фактор", "побед_%", "сделок")]
            _metric_cols = [c for c in _res.columns if c in ("доходность_%", "шарп", "просадка_%", "проф_фактор", "побед_%", "сделок")]

            st.dataframe(
                _res,
                use_container_width=True,
                hide_index=False,
                column_config={
                    "доходность_%": st.column_config.NumberColumn("Доходность %", format="%.2f"),
                    "шарп":         st.column_config.NumberColumn("Шарп",         format="%.3f"),
                    "просадка_%":   st.column_config.NumberColumn("Просадка %",   format="%.2f"),
                    "проф_фактор":  st.column_config.NumberColumn("Проф.фактор",  format="%.3f"),
                    "побед_%":      st.column_config.NumberColumn("Побед %",       format="%.1f"),
                    "сделок":       st.column_config.NumberColumn("Сделок"),
                },
            )

            _best = _res.iloc[0]
            _best_params_str = "  |  ".join(
                f"**{k}** = `{_best[k]}`" for k in _param_cols if k in _best
            )
            st.success(f"🏆  Лучшие параметры:  {_best_params_str}")

            _opt_dl_col1, _opt_dl_col2 = st.columns(2)
            _opt_dl_col1.download_button(
                "💾  Скачать результаты CSV",
                data=_res.to_csv(index=True).encode("utf-8"),
                file_name=f"opt_{_opt_sel_key}_{_opt_metric.lower()}.csv",
                mime="text/csv",
                use_container_width=True,
            )
            if _opt_dl_col2.button("🗑  Очистить результаты", use_container_width=True, key="opt_clear_btn"):
                st.session_state.opt_results = None
                st.rerun()


# ─── СРАВНЕНИЕ СТРАТЕГИЙ ─────────────────────────────────────────────────────
st.divider()
st.subheader("📊  Сравнение стратегий")

if st.session_state.backtest_results is None:
    st.info("Сначала запустите бэктест — сравнение использует те же данные и параметры.")
else:
    _cmp_res_bt = st.session_state.backtest_results
    _cmp_df_full = _cmp_res_bt.get("df_bt")
    _cmp_bp = _cmp_res_bt["backtest_params"]

    # Build comparison labels from STRATEGY_LABELS_RU (includes custom strategies)
    _CMP_LABELS: dict[str, str] = {
        k: v for k, v in STRATEGY_LABELS_RU.items()
        if not k.startswith("custom__")   # built-ins only (custom use default params)
    }
    _cmp_defaults = list(_CMP_LABELS.values())[:4]

    _cmp_col1, _cmp_col2 = st.columns([3, 1])
    with _cmp_col1:
        _cmp_selected = st.multiselect(
            "Стратегии для сравнения",
            list(_CMP_LABELS.values()),
            default=_cmp_defaults,
            key="cmp_strategies",
            help="Каждая стратегия запускается с параметрами по умолчанию на тех же данных",
        )
    with _cmp_col2:
        _cmp_with_bh = st.checkbox("+ Buy & Hold", value=True, key="cmp_bh")

    if _cmp_df_full is None:
        st.warning("Данные недоступны — запустите бэктест снова.")
    elif st.button("▶  Запустить сравнение", key="cmp_run_btn", use_container_width=True):
        _label_to_key = {v: k for k, v in _CMP_LABELS.items()}
        _cmp_rows = []
        _cmp_curves: dict = {}
        _cmp_prog = st.progress(0, text="Сравнение стратегий...")
        _n_total = len(_cmp_selected) + (1 if _cmp_with_bh else 0)
        for _ci, _clabel in enumerate(_cmp_selected):
            _cmp_prog.progress((_ci + 1) / max(_n_total, 1), text=f"Тестирую: {_clabel}…")
            _ckey = _label_to_key[_clabel]
            try:
                _cstrat = get_strategy(_ckey, {})
                _csigs = _cstrat.generate_signals(_cmp_df_full)
                _ceq, _ctrades = run_backtest(
                    _cmp_df_full, _csigs,
                    initial_capital=_cmp_bp["initial_capital"],
                    fee=_cmp_bp["commission_pct"] / 100.0,
                    slippage=_cmp_bp["slippage_pct"] / 100.0,
                    stop_loss=_cmp_bp["stop_loss_pct"] / 100.0,
                    take_profit=_cmp_bp["take_profit_pct"] / 100.0,
                    trailing_stop=_cmp_bp["trailing_stop_pct"] / 100.0,
                    hold_bars=int(_cmp_bp["hold_bars"]),
                )
                _cmets = calculate_metrics(_ceq, _ctrades, _cmp_bp["initial_capital"])
                _cmp_rows.append({
                    "Стратегия": _clabel,
                    "Доходность %": _cmets["total_return"],
                    "Просадка %": _cmets["max_drawdown"],
                    "Шарп": _cmets["sharpe_ratio"],
                    "Profit Factor": str(_cmets["profit_factor"]),
                    "Win Rate %": _cmets["win_rate"],
                    "Сделок": _cmets["total_trades"],
                    "Итог $": _cmets["final_equity"],
                })
                _cmp_curves[_clabel] = _ceq["equity"]
            except Exception as _ce:
                _cmp_rows.append({
                    "Стратегия": _clabel,
                    "Доходность %": None,
                    "Просадка %": None,
                    "Шарп": None,
                    "Profit Factor": "Ошибка",
                    "Win Rate %": None,
                    "Сделок": 0,
                    "Итог $": None,
                })
        if _cmp_with_bh:
            _bh_p0 = float(_cmp_df_full["open"].iloc[0])
            _bh_p1 = float(_cmp_df_full["close"].iloc[-1])
            _bh_ret = (_bh_p1 / _bh_p0 - 1.0) * 100.0 if _bh_p0 > 0 else 0.0
            _bh_final = _cmp_bp["initial_capital"] * (_bh_p1 / _bh_p0) if _bh_p0 > 0 else _cmp_bp["initial_capital"]
            _cmp_rows.append({
                "Стратегия": "📈 Buy & Hold",
                "Доходность %": round(_bh_ret, 2),
                "Просадка %": None,
                "Шарп": None,
                "Profit Factor": "—",
                "Win Rate %": None,
                "Сделок": 1,
                "Итог $": round(_bh_final, 2),
            })
            if _bh_p0 > 0:
                _bh_qty = _cmp_bp["initial_capital"] / _bh_p0
                _cmp_curves["📈 Buy & Hold"] = _cmp_df_full["close"] * _bh_qty
        _cmp_prog.progress(1.0, text="Готово!")
        st.session_state.comparison_results = {"rows": _cmp_rows, "curves": _cmp_curves}

    if st.session_state.comparison_results:
        _cmp_data = st.session_state.comparison_results
        _cmp_tbl = pd.DataFrame(_cmp_data["rows"]).sort_values(
            "Доходность %", ascending=False, na_position="last"
        )
        st.dataframe(
            _cmp_tbl,
            use_container_width=True,
            column_config={
                "Доходность %":  st.column_config.NumberColumn(format="%.2f"),
                "Просадка %":    st.column_config.NumberColumn(format="%.2f"),
                "Шарп":          st.column_config.NumberColumn(format="%.3f"),
                "Win Rate %":    st.column_config.NumberColumn(format="%.1f"),
                "Итог $":        st.column_config.NumberColumn(format="$%.2f"),
            },
            hide_index=True,
        )
        try:
            import plotly.graph_objects as _go_cmp
            _cfig = _go_cmp.Figure()
            _CMP_PAL = ["#00e676","#2196f3","#ff9800","#e91e63","#9c27b0",
                        "#00bcd4","#ffeb3b","#f44336","#4caf50","#9e9e9e"]
            for _ci2, (_cl2, _ceq2) in enumerate(_cmp_data["curves"].items()):
                _dash = "dash" if _cl2 == "📈 Buy & Hold" else "solid"
                _cfig.add_trace(_go_cmp.Scatter(
                    x=_ceq2.index, y=_ceq2, mode="lines", name=_cl2,
                    line=dict(color=_CMP_PAL[_ci2 % len(_CMP_PAL)], width=1.2, dash=_dash),
                ))
            _cfig.update_layout(
                template="plotly_dark", height=360,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title=None, yaxis_title="Капитал (USDT)", showlegend=True,
            )
            st.plotly_chart(_cfig, use_container_width=True)
        except Exception:
            pass
        _cmp_dl_col, _cmp_clr_col = st.columns(2)
        _cmp_dl_col.download_button(
            "💾  Скачать CSV",
            data=_cmp_tbl.to_csv(index=False).encode("utf-8"),
            file_name="strategy_comparison.csv",
            mime="text/csv",
            use_container_width=True,
        )
        if _cmp_clr_col.button("🗑  Очистить", key="cmp_clear_btn", use_container_width=True):
            st.session_state.comparison_results = None
            st.rerun()


# ─── WALK-FORWARD АНАЛИЗ ──────────────────────────────────────────────────────
st.divider()
st.subheader("🔄  Walk-Forward Анализ")

if st.session_state.backtest_results is None:
    st.info("Сначала запустите бэктест — Walk-Forward использует ту же стратегию и данные.")
else:
    _wf_res_bt = st.session_state.backtest_results
    _wf_df_full = _wf_res_bt.get("df_bt")
    _wf_skey = _wf_res_bt["strategy_key"]
    _wf_sparams = _wf_res_bt["strategy_params"]
    _wf_slabel = _wf_res_bt["strategy_label"]
    _wf_bp = _wf_res_bt["backtest_params"]

    st.caption(f"Стратегия: **{_wf_slabel}**  |  параметры: {_wf_sparams}")
    _wf_c1, _wf_c2 = st.columns(2)
    with _wf_c1:
        _wf_is_pct = st.slider(
            "Доля In-Sample (%)", 50, 85, 70, step=5, key="wf_is_pct",
            help="Доля каждого окна, используемая как обучающая выборка.",
        )
    with _wf_c2:
        _wf_folds = st.number_input(
            "Количество фолдов", 1, 5, 3, step=1, key="wf_folds",
            help="На сколько равных окон разбить данные.",
        )

    if _wf_df_full is None:
        st.warning("Данные недоступны — запустите бэктест снова.")
    elif st.button("▶  Запустить Walk-Forward", key="wf_run_btn", use_container_width=True):
        _wf_n = len(_wf_df_full)
        _wf_fold_sz = _wf_n // int(_wf_folds)
        _wf_is_sz = int(_wf_fold_sz * int(_wf_is_pct) / 100)
        _wf_oos_sz = _wf_fold_sz - _wf_is_sz
        if _wf_oos_sz < 10:
            st.error("Слишком мало баров для OOS. Уменьшите количество фолдов или долю IS.")
        else:
            _wf_rows = []
            _wf_oos_curves: dict = {}
            _wf_prog = st.progress(0, text="Walk-Forward…")
            for _fi in range(int(_wf_folds)):
                _wf_prog.progress((_fi + 1) / int(_wf_folds), text=f"Фолд {_fi+1}/{int(_wf_folds)}…")
                _si = _fi * _wf_fold_sz
                _ie = _si + _wf_is_sz
                _oe = min(_si + _wf_fold_sz, _wf_n)
                _wf_is_df = _wf_df_full.iloc[_si:_ie]
                _wf_oos_df = _wf_df_full.iloc[_ie:_oe]
                try:
                    _wf_strat = get_strategy(_wf_skey, _wf_sparams)
                    _is_sigs = _wf_strat.generate_signals(_wf_is_df)
                    _is_eq, _is_tr = run_backtest(
                        _wf_is_df, _is_sigs,
                        initial_capital=_wf_bp["initial_capital"],
                        fee=_wf_bp["commission_pct"] / 100.0,
                        slippage=_wf_bp["slippage_pct"] / 100.0,
                        stop_loss=_wf_bp["stop_loss_pct"] / 100.0,
                        take_profit=_wf_bp["take_profit_pct"] / 100.0,
                        trailing_stop=_wf_bp["trailing_stop_pct"] / 100.0,
                        hold_bars=int(_wf_bp["hold_bars"]),
                    )
                    _is_mets = calculate_metrics(_is_eq, _is_tr, _wf_bp["initial_capital"])

                    _oos_strat = get_strategy(_wf_skey, _wf_sparams)
                    _oos_sigs = _oos_strat.generate_signals(_wf_oos_df)
                    _oos_eq, _oos_tr = run_backtest(
                        _wf_oos_df, _oos_sigs,
                        initial_capital=_wf_bp["initial_capital"],
                        fee=_wf_bp["commission_pct"] / 100.0,
                        slippage=_wf_bp["slippage_pct"] / 100.0,
                        stop_loss=_wf_bp["stop_loss_pct"] / 100.0,
                        take_profit=_wf_bp["take_profit_pct"] / 100.0,
                        trailing_stop=_wf_bp["trailing_stop_pct"] / 100.0,
                        hold_bars=int(_wf_bp["hold_bars"]),
                    )
                    _oos_mets = calculate_metrics(_oos_eq, _oos_tr, _wf_bp["initial_capital"])

                    _wf_rows.append({
                        "Фолд": _fi + 1,
                        "IS баров": len(_wf_is_df),
                        "IS доход %": _is_mets["total_return"],
                        "IS Шарп": _is_mets["sharpe_ratio"],
                        "IS сделок": _is_mets["total_trades"],
                        "OOS баров": len(_wf_oos_df),
                        "OOS доход %": _oos_mets["total_return"],
                        "OOS Шарп": _oos_mets["sharpe_ratio"],
                        "OOS сделок": _oos_mets["total_trades"],
                    })
                    _wf_oos_curves[f"Фолд {_fi + 1}"] = _oos_eq["equity"]
                except Exception as _we:
                    _wf_rows.append({
                        "Фолд": _fi + 1,
                        "IS баров": 0, "IS доход %": None, "IS Шарп": None, "IS сделок": 0,
                        "OOS баров": 0, "OOS доход %": None, "OOS Шарп": None, "OOS сделок": 0,
                    })
            _wf_prog.progress(1.0, text="Готово!")
            _is_rets = [r["IS доход %"] for r in _wf_rows if r["IS доход %"] is not None]
            _oos_rets = [r["OOS доход %"] for r in _wf_rows if r["OOS доход %"] is not None]
            _robustness = None
            if _is_rets and _oos_rets:
                _avg_is = sum(_is_rets) / len(_is_rets)
                _avg_oos = sum(_oos_rets) / len(_oos_rets)
                if _avg_is != 0:
                    _robustness = round(_avg_oos / _avg_is, 3)
            st.session_state.wf_results = {
                "rows": _wf_rows,
                "oos_curves": _wf_oos_curves,
                "robustness": _robustness,
                "strategy": _wf_slabel,
            }

    if st.session_state.wf_results:
        _wf_data = st.session_state.wf_results
        _rob = _wf_data.get("robustness")
        if _rob is not None:
            if _rob >= 0.7:
                _rc, _rl = "#4caf50", "Устойчивая"
            elif _rob >= 0.3:
                _rc, _rl = "#ff9800", "Средняя"
            else:
                _rc, _rl = "#f44336", "Слабая"
            st.markdown(
                f"<div style='text-align:center;padding:10px;border:1px solid {_rc};"
                f"border-radius:6px;margin-bottom:8px;'>"
                f"<span style='color:{_rc};font-size:24px;font-weight:bold;'>{_rob:+.3f}</span><br>"
                f"<small style='color:#aaa;'>Коэф. устойчивости (OOS/IS) — {_rl}</small></div>",
                unsafe_allow_html=True,
            )
            st.caption("≥ 0.7 = OOS близко к IS, стратегия не переобучена. < 0.3 = результаты не воспроизводятся.")
        _wf_tbl = pd.DataFrame(_wf_data["rows"])
        st.dataframe(
            _wf_tbl,
            use_container_width=True,
            column_config={
                "IS доход %":  st.column_config.NumberColumn(format="%.2f"),
                "IS Шарп":     st.column_config.NumberColumn(format="%.3f"),
                "OOS доход %": st.column_config.NumberColumn(format="%.2f"),
                "OOS Шарп":    st.column_config.NumberColumn(format="%.3f"),
            },
            hide_index=True,
        )
        if _wf_data["oos_curves"]:
            try:
                import plotly.graph_objects as _go_wf
                _wfig = _go_wf.Figure()
                _WF_PAL = ["#00e676","#2196f3","#ff9800","#e91e63","#9c27b0"]
                for _wci, (_wl, _weq) in enumerate(_wf_data["oos_curves"].items()):
                    _wfig.add_trace(_go_wf.Scatter(
                        x=_weq.index, y=_weq, mode="lines", name=_wl,
                        line=dict(color=_WF_PAL[_wci % len(_WF_PAL)], width=1.3),
                    ))
                _wfig.update_layout(
                    template="plotly_dark", height=300,
                    margin=dict(l=0, r=0, t=10, b=0),
                    xaxis_title=None, yaxis_title="OOS Капитал (USDT)", showlegend=True,
                )
                st.plotly_chart(_wfig, use_container_width=True)
            except Exception:
                pass
        if st.button("🗑  Очистить Walk-Forward", key="wf_clear_btn"):
            st.session_state.wf_results = None
            st.rerun()


# ─── РЕЖИМ РАБОТЫ ─────────────────────────────────────────────────────────────
st.divider()
with st.expander("🛡️  Режим работы", expanded=False):
    sm_col, ss_col, sf_col = st.columns(3)
    with sm_col:
        st.markdown(
            "**Текущий режим**\n"
            "- Локальный офлайн интерфейс\n"
            "- ✅ Движок бэктеста: **активен**\n"
            "- ✅ Кастомные стратегии: **активны**\n"
            "- 🔑 API ключи: **только в памяти**\n"
            "- 🔒 Живая торговля: **отключена**"
        )
    with ss_col:
        st.markdown(
            "**Доступно сейчас**\n"
            "- ✅ Синтетические данные\n"
            "- ✅ Загрузка CSV / Биржа\n"
            "- ✅ Скачать данные CSV\n"
            "- ✅ Бэктест 9 стратегий + свои\n"
            "- ✅ SL / TP / Трейлинг-стоп / Hold Bars\n"
            "- ✅ API ключи (Read Only)\n"
            "- ✅ Бумажная торговля\n"
            "- ✅ Оптимизация параметров\n"
            "- ✅ AI Оценка стратегии (офлайн)"
        )
    with sf_col:
        st.markdown(
            "**В будущих версиях**\n"
            "- ✅ Сравнение стратегий\n"
            "- ✅ Walk-Forward Анализ\n"
            "- ✅ Buy & Hold бенчмарк\n"
            "- 🔜 Живая торговля"
        )
