"""Лаборатория тестирования стратегий — локальный Streamlit UI для бэктестинга."""
from __future__ import annotations

import inspect
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
    "custom_strategy_cls": None,       # загруженный класс стратегии
    "custom_strategy_params": {},      # параметры по умолчанию из __init__
    "custom_strategy_name": "",        # имя класса
    "api_keys": {
        "binance": {"key": "", "secret": ""},
        "bybit":   {"key": "", "secret": ""},
    },
    "api_status": {},                  # {"binance": {"ok": bool, "message": str}}
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
_MAX_EXCHANGE_BARS = 1500


def _calc_bars(start: pd.Timestamp, end: pd.Timestamp, freq: str) -> int:
    seconds = (end - start).total_seconds()
    bar_sec = _TF_SECONDS.get(freq, 3600)
    return max(100, int(seconds / bar_sec) + 1)


def _get_api(exchange_id: str) -> tuple[str | None, str | None]:
    keys = st.session_state.api_keys.get(exchange_id, {})
    k = keys.get("key", "").strip() or None
    s = keys.get("secret", "").strip() or None
    return k, s


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

    with st.expander("🔑 Об API ключах"):
        st.markdown("""
        API ключи **не отправляются на сервер** — всё выполняется локально.
        Ключи хранятся только в памяти сессии и не сохраняются на диск.

        **Без ключей:** только публичные OHLCV (до 1500 баров за запрос).

        **С ключами:**
        - Выше лимиты на запросы
        - Доступ к полной истории
        - Основа для бумажной торговли (в будущем)

        Создать ключи с правами **Read Only** → безопасно.
        """)

    with st.expander("🛑 Параметры выхода"):
        st.markdown("""
        **Стоп-лосс** — фиксированный, по low свечи.\n
        **Тейк-профит** — фиксированная цель, по high свечи.\n
        **Трейлинг-стоп** — X% ниже пикового high с момента входа.
        Подтягивается вверх автоматически.\n
        Приоритет: `SL → Trail → TP → Сигнал`
        """)

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
    # Добавляем кастомную если загружена
    if st.session_state.custom_strategy_cls is not None:
        _cname = st.session_state.custom_strategy_name
        STRATEGY_LABELS_RU["custom"] = f"🆕 Своя: {_cname}"

    strategy_label_to_key = {v: k for k, v in STRATEGY_LABELS_RU.items()}
    selected_label = st.selectbox("Выберите стратегию", list(STRATEGY_LABELS_RU.values()))
    selected_key = strategy_label_to_key[selected_label]

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
        "custom":         f"Своя стратегия: {st.session_state.custom_strategy_name}",
    }
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

    elif selected_key == "custom":
        _default_params = st.session_state.custom_strategy_params
        if _default_params:
            st.caption("Параметры стратегии (из __init__):")
            _p_cols = st.columns(min(len(_default_params), 3))
            for idx, (pname, pdefault) in enumerate(_default_params.items()):
                col = _p_cols[idx % len(_p_cols)]
                if isinstance(pdefault, bool):
                    strategy_params[pname] = col.checkbox(pname, value=pdefault)
                elif isinstance(pdefault, int):
                    strategy_params[pname] = col.number_input(pname, value=pdefault, step=1)
                elif isinstance(pdefault, float):
                    strategy_params[pname] = col.number_input(pname, value=pdefault, format="%.4f")
                else:
                    strategy_params[pname] = col.text_input(pname, value=str(pdefault))
        else:
            st.caption("Параметры не обнаружены — используются значения по умолчанию.")

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # СВОЯ СТРАТЕГИЯ — исполняемый блок
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("📝  Своя стратегия", expanded=False):
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
            "▶️  Активировать стратегию",
            use_container_width=True,
            type="primary",
            disabled=not bool(st.session_state.custom_code.strip()),
        )
        if _activate_btn:
            try:
                cls, params = _load_custom_strategy(st.session_state.custom_code)
                st.session_state.custom_strategy_cls = cls
                st.session_state.custom_strategy_params = params
                st.session_state.custom_strategy_name = cls.__name__
                _log(f"Стратегия активирована: {cls.__name__}  параметры: {params}")
                st.rerun()
            except ValueError as exc:
                st.error(f"❌  {exc}")
            except Exception as exc:
                st.error(f"❌  Неожиданная ошибка: {exc}")

        if st.session_state.custom_strategy_cls is not None:
            _cls = st.session_state.custom_strategy_cls
            _desc = getattr(_cls, "description", "—")
            st.success(
                f"✅  **{_cls.__name__}** активна\n\n"
                f"{_desc}\n\n"
                f"Параметры: `{st.session_state.custom_strategy_params}`"
            )
            if st.button("🗑  Удалить кастомную стратегию", use_container_width=True):
                st.session_state.custom_strategy_cls = None
                st.session_state.custom_strategy_params = {}
                st.session_state.custom_strategy_name = ""
                st.rerun()

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # API КЛЮЧИ БИРЖ
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("🔑  API ключи бирж", expanded=False):
        st.caption(
            "Ключи хранятся **только в памяти сессии** — не сохраняются на диск и не отправляются "
            "никуда. Создавайте ключи с правами **Read Only** для безопасности."
        )

        for _exch in ["binance", "bybit"]:
            st.markdown(f"**{_exch.title()}**")
            _k_col, _s_col = st.columns(2)
            _new_key = _k_col.text_input(
                "API Key", type="password",
                value=st.session_state.api_keys[_exch]["key"],
                key=f"api_key_{_exch}",
                placeholder="Вставьте API Key",
            )
            _new_sec = _s_col.text_input(
                "API Secret", type="password",
                value=st.session_state.api_keys[_exch]["secret"],
                key=f"api_secret_{_exch}",
                placeholder="Вставьте API Secret",
            )
            st.session_state.api_keys[_exch]["key"] = _new_key
            st.session_state.api_keys[_exch]["secret"] = _new_sec

            _test_btn = st.button(
                f"🔌  Проверить {_exch.title()}",
                key=f"test_api_{_exch}",
                use_container_width=True,
            )
            if _test_btn:
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
            f"Синтетические OHLCV · **{timeframe}** · **{_n_bars:,} баров**"
            if _dates_ok else "Исправьте диапазон дат выше."
        )
        if st.button("⚡  Сгенерировать данные", use_container_width=True, disabled=not _dates_ok):
            df_loaded = generate_sample_ohlcv(n_bars=_n_bars, start=str(start_date), freq=timeframe)
            st.session_state.df = df_loaded
            st.session_state.data_label = f"Синтетика BTC/USDT {timeframe} — {_n_bars:,} баров"
            st.session_state.backtest_results = None
            _log(f"Синтетика: {_n_bars} баров, {timeframe}")

    elif data_mode == "Загрузить CSV":
        st.caption("Колонки: timestamp, open, high, low, close, volume")
        csv_file = st.file_uploader("Загрузить OHLCV CSV", type=["csv"])
        if csv_file is not None:
            try:
                dm = DataManager()
                df_loaded = dm.load_from_csv(csv_file.read())
                st.session_state.df = df_loaded
                st.session_state.data_label = f"CSV: {csv_file.name}  ({len(df_loaded):,} баров)"
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
        _fetch_bars = min(_n_bars, _MAX_EXCHANGE_BARS) if _dates_ok else 500

        if _has_api:
            st.success(f"🔑 API ключи {exc_sel.title()} активны")
        else:
            st.info("📌 Публичный режим — API ключ не нужен")

        if _dates_ok and _n_bars > _MAX_EXCHANGE_BARS:
            st.warning(
                f"⚠️  Период требует {_n_bars:,} баров, лимит запроса — {_MAX_EXCHANGE_BARS}. "
                "Будут загружены последние бары в диапазоне."
            )

        if st.button("📥  Загрузить с биржи", use_container_width=True, disabled=not _dates_ok):
            with st.spinner(f"Загрузка {sym_sel} с {exc_sel}…"):
                try:
                    dm = DataManager()
                    df_loaded = dm.load_from_exchange(
                        exc_sel, sym_sel, timeframe,
                        start=str(start_date),
                        end=str(end_date),
                        limit=_fetch_bars,
                        api_key=_api_k,
                        api_secret=_api_s,
                    )
                    st.session_state.df = df_loaded
                    st.session_state.data_label = (
                        f"{exc_sel.title()} {sym_sel} {timeframe} — {len(df_loaded):,} баров"
                    )
                    st.session_state.backtest_results = None
                    _log(f"Загружено {len(df_loaded)} баров: {exc_sel} {sym_sel} {timeframe}")
                    st.success(f"Загружено {len(df_loaded):,} баров")
                except Exception as exc:
                    st.error(f"Ошибка загрузки: {exc}")

    # Статус данных
    if st.session_state.df is not None:
        _df = st.session_state.df
        st.success(
            f"✅  {st.session_state.data_label}\n\n"
            f"Период: {_df.index[0].date()} → {_df.index[-1].date()}"
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
    st.text_input("Исполнение", value="Маркет-ордер по цене закрытия свечи", disabled=True)

    st.divider()

    # ── Параметры выхода ──────────────────────────────────────────────────────
    st.subheader("🛑  Выход из позиции")
    st.caption("Все три независимы. `0` = выключено.")

    ex1, ex2 = st.columns(2)
    with ex1:
        stop_loss_pct = st.number_input(
            "Стоп-лосс (%)", 0.0, 50.0, 0.0, step=0.1, format="%.1f",
            help="Фиксированный стоп ниже цены входа. По low свечи.",
        )
    with ex2:
        take_profit_pct = st.number_input(
            "Тейк-профит (%)", 0.0, 200.0, 0.0, step=0.1, format="%.1f",
            help="Фиксированная цель выше цены входа. По high свечи.",
        )

    trailing_stop_pct = st.number_input(
        "Трейлинг-стоп (%)", 0.0, 50.0, 0.0, step=0.1, format="%.1f",
        help="X% ниже максимального high с момента входа. Подтягивается вверх автоматически.",
    )

    _exits = []
    if stop_loss_pct > 0:
        _exits.append(f"🔴 SL {stop_loss_pct:.1f}%")
    if trailing_stop_pct > 0:
        _exits.append(f"🟠 Trail {trailing_stop_pct:.1f}%")
    if take_profit_pct > 0:
        _exits.append(f"🟢 TP {take_profit_pct:.1f}%")
    _exits.append("📊 Сигнал")
    st.caption("Приоритет: " + " → ".join(_exits))

    st.divider()

    # ── Кнопки управления ────────────────────────────────────────────────────
    st.subheader("🚀  Запуск")

    prepare_btn = st.button("📋  Подготовить команду CLI", use_container_width=True)

    data_ready = st.session_state.df is not None
    _run_disabled = not data_ready or not _dates_ok
    run_btn = st.button(
        "▶️  Запустить бэктест",
        use_container_width=True,
        disabled=_run_disabled,
        type="primary",
        help=(
            "Сначала загрузите данные" if not data_ready
            else "Исправьте диапазон дат" if not _dates_ok
            else "Запустить бэктест"
        ),
    )

    st.button("📄  Бумажная торговля — скоро", disabled=True, use_container_width=True)
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
        for k, v in strategy_params.items():
            cmd += f" \\\n  --param {k}={v}"
        st.session_state.command_preview = cmd
        _log("CLI команда сформирована")

    # ── Запуск бэктеста ───────────────────────────────────────────────────────
    if run_btn and data_ready and _dates_ok:
        with st.spinner("Выполняется бэктест…"):
            try:
                df_bt = st.session_state.df.copy()
                df_bt = df_bt[
                    (df_bt.index >= pd.Timestamp(start_date))
                    & (df_bt.index <= pd.Timestamp(end_date))
                ]
                if df_bt.empty:
                    st.error("Нет данных в выбранном диапазоне.")
                else:
                    # Создаём объект стратегии
                    if selected_key == "custom" and st.session_state.custom_strategy_cls is not None:
                        strategy = st.session_state.custom_strategy_cls(**strategy_params)
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
            "- ✅ Загрузка CSV\n"
            "- ✅ Публичные OHLCV с биржи\n"
            "- ✅ Бэктест 9 стратегий + свои\n"
            "- ✅ SL / TP / Трейлинг-стоп\n"
            "- ✅ API ключи (Read Only)"
        )
    with sf_col:
        st.markdown(
            "**В будущих версиях**\n"
            "- 🔜 Сравнение стратегий\n"
            "- 🔜 Оптимизация параметров\n"
            "- 🔜 Бумажная торговля\n"
            "- 🔜 Живая торговля\n"
            "- 🔜 AI оценка стратегий"
        )
