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
) -> dict:
    """Fetch recent bars from exchange and simulate paper trades."""
    from datetime import datetime, timedelta, timezone
    dm = DataManager()
    tf_sec = _TF_SECONDS.get(timeframe, 3600)
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(seconds=tf_sec * (lookback + 50))

    df = dm.load_from_exchange_all(
        exchange, symbol, timeframe,
        start=start_dt.strftime("%Y-%m-%d %H:%M"),
        end=end_dt.strftime("%Y-%m-%d %H:%M"),
        api_key=api_key,
        api_secret=api_secret,
    )
    if df.empty:
        raise ValueError("Биржа не вернула данных — проверьте пару и таймфрейм.")
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
    entry_price = 0.0
    entry_time = None
    virtual_capital = float(capital)
    qty = 0.0
    trades: list[dict] = []

    for ts, sig in signals.items():
        price = float(df.loc[ts, "close"])
        if not in_position and int(sig) == 1:
            qty = virtual_capital / price
            entry_price = price
            entry_time = ts
            in_position = True
        elif in_position and int(sig) == -1:
            pnl = qty * (price - entry_price)
            virtual_capital += pnl
            trades.append({
                "Вход": str(entry_time)[:16],
                "Цена входа": round(entry_price, 4),
                "Выход": str(ts)[:16],
                "Цена выхода": round(price, 4),
                "PnL (USDT)": round(pnl, 2),
                "Доходность %": round((price - entry_price) / entry_price * 100, 2),
                "Капитал": round(virtual_capital, 2),
            })
            in_position = False
            qty = 0.0

    current_price = float(df["close"].iloc[-1])
    unrealized_pnl  = qty * (current_price - entry_price) if in_position else 0.0
    unrealized_pct  = (current_price - entry_price) / entry_price * 100 if (in_position and entry_price) else 0.0

    return {
        "df": df, "signals": signals,
        "in_position": in_position,
        "entry_price": round(entry_price, 4),
        "entry_time": str(entry_time)[:16] if entry_time else None,
        "qty": round(qty, 8),
        "trades": trades,
        "capital": round(virtual_capital, 2),
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

    st.subheader("📤  Выгрузка для анализа стратегии")
    d1, d2, d3, d4 = st.columns(4)
    d1.download_button(
        "📄 summary.json", export_analysis_to_json_bytes(summary),
        f"{pfx}_summary.json", "application/json", use_container_width=True,
    )
    d2.download_button(
        "📊 trades.csv", export_analysis_to_csv_bytes(trades_df),
        f"{pfx}_trades.csv", "text/csv", use_container_width=True,
    )
    d3.download_button(
        "📈 equity.csv", export_analysis_to_csv_bytes(equity_df),
        f"{pfx}_equity.csv", "text/csv", use_container_width=True,
    )
    d4.download_button(
        "🤖 analysis_pack.json", export_analysis_to_json_bytes(pack),
        f"{pfx}_analysis_pack.json", "application/json", use_container_width=True,
    )


_load_all_user_strategies()

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
        | Метрика | Хорошее значение |
        |---------|-----------------|
        | **Общая доходность** | > +20% |
        | **Макс. просадка** | < -20% |
        | **Коэф. Шарпа** | > 1.0 |
        | **Профит-фактор** | > 1.5 |
        | **% выигрышных** | > 50% |
        """)

    with st.expander("🎯 Стратегии"):
        st.markdown("""
        🔵 **SMA / EMA Crossover** — пересечение скользящих\n
        🟢 **RSI** — возврат из перепроданности\n
        🟡 **MACD** — пересечение MACD/сигнала\n
        🔴 **Bollinger / Stoch+EMA / VWAP** — скальп M15\n
        🟣 **Turtle Soup** — ложный пробой минимума\n
        🟣 **80-20 по Рашке** — бар разворота\n
        🆕 **Своя** — загрузи .py с классом BaseStrategy
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

    # ── 🔜 Бумажная торговля ─────────────────────────────────────────────────
    with st.expander("🔜  Бумажная торговля", expanded=False):
        st.markdown(
            "<div style='background:#0d1117;border:1px solid #30363d;"
            "border-radius:6px;padding:8px 12px;text-align:center;"
            "color:#8b949e;font-size:12px;margin-bottom:10px;'>"
            "🔜 &nbsp; В разработке</div>",
            unsafe_allow_html=True,
        )
        st.markdown("""
        **Что будет доступно:**
        - 📡 Подключение к бирже по API
        - ▶️ Запуск стратегии на живых данных
        - 📊 Виртуальный портфель без реальных сделок
        - 🔔 Логирование сигналов и виртуальных сделок
        - 📈 Equity curve в реальном времени
        """)
        st.caption("⚠️ Живая торговля остаётся заблокирована.")
        pt1, pt2 = st.columns(2)
        pt1.text_input("Биржа", value="binance", disabled=True, key="pt_exchange")
        pt2.text_input("Пара", value="BTC/USDT", disabled=True, key="pt_symbol")
        st.number_input(
            "Виртуальный капитал (USDT)", value=10000, step=500,
            disabled=True, key="pt_capital",
        )
        st.button(
            "▶️  Запустить бумажную торговлю",
            use_container_width=True, disabled=True,
            help="Функция в разработке",
        )

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
    start_date = st.date_input("Дата начала", value=pd.Timestamp("2026-01-01"))
with pt2:
    end_date = st.date_input("Дата окончания", value=pd.Timestamp("2026-06-22"))
with pt3:
    timeframe = st.selectbox("Таймфрейм", ["15m", "1m", "5m", "30m", "1h", "4h", "1d"])
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
        "sma_cross":      "SMA Crossover (тренд)",
        "ema_cross":      "EMA Crossover (тренд)",
        "rsi":            "RSI Mean Reversion (возврат)",
        "macd":           "MACD Momentum (моментум)",
        "bollinger_scalp": "Bollinger Scalp — M15",
        "stoch_ema_scalp": "Stochastic + EMA Scalp — M15",
        "vwap_bounce":    "VWAP Bounce Scalp — M15",
        "turtle_soup":    "Turtle Soup (контртренд)",
        "raschke_80_20":  "80-20 по Рашке (разворот)",
    }
    for _cn in st.session_state.custom_strategies:
        STRATEGY_LABELS_RU[f"custom__{_cn}"] = f"🆕 {_cn}"

    strategy_label_to_key = {v: k for k, v in STRATEGY_LABELS_RU.items()}
    _labels_list = list(STRATEGY_LABELS_RU.values())
    _keys_list = list(STRATEGY_LABELS_RU.keys())
    _saved_key = st.session_state.get("selected_strategy", "sma_cross")
    if _saved_key not in STRATEGY_LABELS_RU:
        _saved_key = "sma_cross"
    selected_label = st.selectbox(
        "Выберите стратегию", _labels_list, index=_keys_list.index(_saved_key)
    )
    selected_key = strategy_label_to_key[selected_label]
    st.session_state.selected_strategy = selected_key

    STRATEGY_CAPTIONS_RU: dict[str, str] = {
        "sma_cross":      "Трендовая — покупка при пересечении быстрой MA вверх.",
        "ema_cross":      "Трендовая — то же что SMA, EMA быстрее реагирует.",
        "rsi":            "Возврат к среднему — вход при RSI < порога перепроданности.",
        "macd":           "Моментум — пересечение линии MACD и сигнальной.",
        "bollinger_scalp": "Скальп M15 — отскок от нижней полосы Боллинджера.",
        "stoch_ema_scalp": "Скальп M15 — стохастик в перепроданности + тренд EMA(50).",
        "vwap_bounce":    "Скальп M15 — отскок от нижней полосы VWAP.",
        "turtle_soup":    "Контртренд — ловля ложного пробоя N-барового минимума.",
        "raschke_80_20":  "Разворот — вход после бара: открытие внизу, закрытие вверху.",
    }
    for _cn, _ci in st.session_state.custom_strategies.items():
        _cd = getattr(_ci.get("cls"), "description", "") or ""
        STRATEGY_CAPTIONS_RU[f"custom__{_cn}"] = f"Своя: {_cn}" + (f" — {_cd}" if _cd else "")
    st.caption(STRATEGY_CAPTIONS_RU.get(selected_key, ""))

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
            "Загрузите .py файл или вставьте код. "
            "Класс должен наследовать `BaseStrategy` и реализовывать `generate_signals(df)`."
        )

        _upload = st.file_uploader("Загрузить .py файл", type=["py"], key="strategy_uploader")
        if _upload is not None:
            _uploaded_code = _upload.read().decode("utf-8")
            st.session_state.custom_code = _uploaded_code
            _log(f"Файл загружен: {_upload.name}")

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


# ══════════════════════════════════════════════════════════════════════════════
# ЦЕНТРАЛЬНАЯ КОЛОНКА — Настройка + Запуск
# ══════════════════════════════════════════════════════════════════════════════
with center_col:

    st.subheader("⚙️  Параметры бэктеста")

    cc1, cc2 = st.columns(2)
    with cc1:
        initial_capital = st.number_input(
            "Начальный капитал (USDT)", 100, 10_000_000, 10_000, step=500
        )
    with cc2:
        fee_pct = st.number_input("Комиссия (%)", 0.0, 5.0, 0.1, step=0.01, format="%.3f")

    slippage_pct = st.number_input(
        "Проскальзывание (%)", 0.0, 5.0, 0.05, step=0.01, format="%.3f"
    )
    st.selectbox("Метод расчёта позиции", ["% от капитала", "Фиксированный", "На основе риска"])
    st.text_input("Исполнение", value="Маркет-ордер по открытию следующего бара", disabled=True)

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
    st.subheader("💻  Команда CLI")
    if st.session_state.command_preview:
        st.code(st.session_state.command_preview, language="bash")
    else:
        st.markdown(
            "<div style='background:#0d1117;border:1px solid #30363d;border-radius:6px;"
            "padding:14px;font-family:monospace;font-size:12px;color:#8b949e;'>"
            "$ <span style='color:#3fb950'>ожидание конфигурации…</span><br>"
            "&gt; нажмите 📋 Подготовить команду CLI</div>",
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
        fig.update_layout(
            template="plotly_dark", height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title=None, yaxis_title="Портфель (USDT)", showlegend=False,
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


# ══════════════════════════════════════════════════════════════════════════════
# БУМАЖНАЯ ТОРГОВЛЯ
# ══════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📄  Бумажная торговля")
st.caption(
    "Симуляция торговли на реальных данных с биржи. "
    "Виртуальный капитал, сигналы и сделки — без реального исполнения."
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
                    min_value=0.001, step=1 if _ptype == "int" else 0.01,
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
                    _opt_capital, _opt_fee, _opt_slip, _opt_sl, _opt_tp, 0.0,
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
            "- ✅ Оптимизация параметров"
        )
    with sf_col:
        st.markdown(
            "**В будущих версиях**\n"
            "- 🔜 Сравнение стратегий\n"
            "- 🔜 Живая торговля\n"
            "- 🔜 AI оценка стратегий"
        )
