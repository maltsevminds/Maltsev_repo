"""Лаборатория тестирования стратегий — локальный Streamlit UI для бэктестинга."""
from __future__ import annotations

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
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def _log(msg: str) -> None:
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    st.session_state.ui_logs.insert(0, f"[{ts}]  {msg}")
    st.session_state.ui_logs = st.session_state.ui_logs[:80]


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

# ─── БОКОВАЯ ПАНЕЛЬ: ПОЛНАЯ ИНСТРУКЦИЯ ────────────────────────────────────────
with st.sidebar:
    st.markdown("# 📖 Инструкция")

    with st.expander("⚡ Быстрый старт (2 минуты)", expanded=True):
        st.markdown("""
        **Шаг 1 — Выбрать стратегию** *(левая колонка)*
        Выберите одну из 9 готовых стратегий.
        При необходимости настройте параметры.

        **Шаг 2 — Загрузить данные** *(левая колонка)*
        - **Синтетические** — мгновенно, для первого теста
        - **CSV-файл** — ваши реальные OHLCV данные
        - **С биржи** — Binance или Bybit (без API ключа)

        **Шаг 3 — Настроить параметры** *(центр)*
        Укажите даты, начальный капитал, комиссию и проскальзывание.

        **Шаг 4 — Запустить** *(центр)*
        Нажмите **▶️ Запустить бэктест**.
        Результаты появятся справа и внизу.
        """)

    with st.expander("📊 Расшифровка метрик"):
        st.markdown("""
        | Метрика | Хорошее значение |
        |---------|-----------------|
        | **Общая доходность** | > +20% |
        | **Макс. просадка** | < -20% |
        | **Коэф. Шарпа** | > 1.0 |
        | **Коэф. Сортино** | > 1.0 |
        | **Профит-фактор** | > 1.5 |
        | **% выигрышных** | > 50% |

        **Коэф. Шарпа** — доходность / риск (в год).
        > 1.0 — хорошо, > 2.0 — отлично.

        **Макс. просадка** — наибольшее падение портфеля
        от пика до дна. Чем меньше по модулю — тем лучше.

        **Профит-фактор** — сумма выигрышей / сумма убытков.
        > 1.5 — прибыльная стратегия.

        **Кривая капитала** — растущий тренд = стратегия
        зарабатывает. Частые просадки = высокий риск.
        """)

    with st.expander("🎯 Описание стратегий"):
        st.markdown("""
        **Трендовые:**

        🔵 **SMA Crossover** — покупка когда быстрая
        скользящая пересекает медленную вверх.
        Хорош на сильных трендах. Таймфрейм: 1h–1d.

        🔵 **EMA Crossover** — то же, но EMA быстрее
        реагирует на изменение цены.
        Таймфрейм: 1h–4h.

        **Возврат к среднему:**

        🟢 **RSI Mean Reversion** — покупка при выходе
        RSI из зоны перепроданности (< 30).
        Хорош на флэте. Таймфрейм: 1h–1d.

        **Моментум:**

        🟡 **MACD Momentum** — пересечение линии MACD
        и сигнальной линии. Много сигналов.
        Таймфрейм: 15m–4h.

        **Скальпинг (M15):**

        🔴 **Bollinger Scalp** — отскок от нижней полосы
        Боллинджера, выход у средней линии.

        🔴 **Stochastic + EMA** — стохастик в зоне
        перепроданности + фильтр по тренду EMA(50).

        🔴 **VWAP Bounce** — отскок от нижней полосы
        отклонения VWAP, выход при возврате к VWAP.

        **Контртренд / Разворот:**

        🟣 **Turtle Soup** (Raschke/Connors) — ловля
        ложных пробоев N-барового минимума. Цена пробивает
        уровень и тут же разворачивается — входим на возврат.

        🟣 **80-20 по Рашке** (Linda Raschke) — вход
        после бара-разворота: открытие в нижних 20%
        диапазона, закрытие в верхних 20% (бычий разворот).
        """)

    with st.expander("📁 Формат CSV файла"):
        st.markdown("""
        Файл должен содержать колонки:
        `timestamp, open, high, low, close, volume`

        Допустимые форматы даты:
        - `2023-01-01 00:00:00`
        - `2023-01-01T00:00:00`
        - `2023-01-01`

        **Пример:**
        ```
        timestamp,open,high,low,close,volume
        2023-01-01 00:00:00,16500,16550,16450,16490,45.2
        2023-01-01 01:00:00,16490,16550,16480,16540,38.9
        ```

        Регистр колонок не важен (open = OPEN = Open).
        """)

    with st.expander("⚙️ Параметры бэктеста"):
        st.markdown("""
        **Начальный капитал** — сумма в USDT, с которой
        начинается тест. Рекомендуется: 10 000.

        **Комиссия (%)** — комиссия биржи за сделку.
        Binance Spot: 0.1%. Bybit: 0.1%.

        **Проскальзывание (%)** — разница между
        теоретической и реальной ценой исполнения.
        Рекомендуется: 0.05%.

        **Диапазон дат** — бэктест выполняется только
        на данных в указанном промежутке.
        Данные вне диапазона игнорируются.

        **Тип позиции:** только лонг (покупка).
        Шорты в текущей версии не поддерживаются.
        """)

    with st.expander("🔧 Советы и ограничения"):
        st.markdown("""
        **Советы:**
        - Для скальпинга (M15) берите 3000+ баров
        - Проверяйте Sharpe и MDD, не только доходность
        - Сравнивайте одну стратегию с разными параметрами
        - Тестируйте на данных не менее 1 года

        **Текущие ограничения:**
        - ❌ Только длинные позиции (no short)
        - ❌ Одна позиция одновременно
        - ❌ Исполнение по цене закрытия свечи
        - ❌ Равномерная комиссия без скидок

        **Не реализовано (будущее):**
        - 🔜 Живая торговля
        - 🔜 Бумажная торговля
        - 🔜 Оптимизация параметров
        - 🔜 Запуск пользовательского кода
        """)

    st.divider()
    st.markdown("**Версия:** MVP — Data + Backtest")
    st.markdown("**Режим:** Локальный · Офлайн · Только исследования")


# ─── ЗАГОЛОВОК ────────────────────────────────────────────────────────────────
st.title("⚗️  Лаборатория тестирования стратегий")
st.markdown("*Локальный офлайн инструмент для бэктестинга крипто-стратегий*")

b1, b2, b3, b4, b5 = st.columns(5)
b1.success("MVP — Данные + Бэктест")
b2.info("Офлайн режим")
b3.info("Только исследования")
b4.warning("Торговля отключена")
b5.warning("Без API ключей")

# ─── ИНФОРМАЦИОННАЯ ПАНЕЛЬ ────────────────────────────────────────────────────
st.info("""
**📖 КАК ПОЛЬЗОВАТЬСЯ:**

1️⃣ **Выберите стратегию** *(левая колонка)* — выберите из 9 стратегий, настройте параметры
2️⃣ **Загрузите данные** *(левая колонка)* — синтетика / CSV / биржа (без API ключа)
3️⃣ **Настройте бэктест** *(центр)* — даты, капитал, комиссия, проскальзывание
4️⃣ **Запустите** *(центр)* — нажмите **▶️ Запустить бэктест**, результаты появятся справа и внизу

**📊 Хорошие значения:** Шарп > 1.0 ✓ | % выигрышных > 50% ✓ | Профит-фактор > 1.5 ✓
**📚 Полная инструкция:** нажмите на **">"** слева для открытия боковой панели
""")

if not _backend_ok:
    st.error(f"⚠️  Ошибка загрузки модулей: {_backend_error}")
    st.info("Запускайте из корня репозитория: `python3 -m streamlit run dashboard/strategy_testing_lab.py`")
    st.stop()

st.divider()

# ─── ТРЁХКОЛОНОЧНЫЙ МАКЕТ ─────────────────────────────────────────────────────
left_col, center_col, right_col = st.columns([1.2, 1.4, 1.1])

# ══════════════════════════════════════════════════════════════════════════════
# ЛЕВАЯ КОЛОНКА — Стратегия + Источник данных
# ══════════════════════════════════════════════════════════════════════════════
with left_col:

    # ── Выбор стратегии ───────────────────────────────────────────────────────
    st.subheader("🎯  Стратегия")

    STRATEGY_LABELS_RU = {
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

    strategy_label_to_key = {v: k for k, v in STRATEGY_LABELS_RU.items()}
    selected_label = st.selectbox(
        "Выберите стратегию",
        list(STRATEGY_LABELS_RU.values()),
    )
    selected_key = strategy_label_to_key[selected_label]

    STRATEGY_CAPTIONS_RU = {
        "sma_cross":      "Трендовая — покупка при пересечении быстрой MA вверх через медленную MA.",
        "ema_cross":      "Трендовая — то же что SMA, но EMA быстрее реагирует на изменения цены.",
        "rsi":            "Возврат к среднему — вход при выходе RSI из перепроданности (< 30).",
        "macd":           "Моментум — пересечение линии MACD и сигнальной линии.",
        "bollinger_scalp": "Скальп M15 — отскок от нижней полосы Боллинджера, выход у средней линии.",
        "stoch_ema_scalp": "Скальп M15 — стохастик в зоне перепроданности, фильтр по тренду EMA(50).",
        "vwap_bounce":    "Скальп M15 — отскок от нижней полосы VWAP, выход при возврате к VWAP.",
        "turtle_soup":    "Контртренд (Raschke/Connors) — ловля ложного пробоя N-барового минимума.",
        "raschke_80_20":  "Разворот (Linda Raschke) — вход после бара с открытием внизу и закрытием вверху диапазона.",
    }
    st.caption(STRATEGY_CAPTIONS_RU[selected_key])

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
        strategy_params["n_bars"] = pc1.number_input(
            "Период минимума (баров)", 5, 100, 20, step=1,
            help="Количество баров для вычисления N-барового минимума. Классика: 20.",
        )
        strategy_params["exit_ema"] = pc2.number_input(
            "Выход EMA (баров)", 2, 50, 5, step=1,
            help="Период быстрой EMA для выхода. Если цена падает ниже — выход.",
        )

    elif selected_key == "raschke_80_20":
        pc1, pc2 = st.columns(2)
        strategy_params["threshold"] = float(
            pc1.number_input(
                "Порог 80-20 (%)", 5, 40, 20, step=1,
                help="Доля диапазона свечи (%). Вход: открытие в нижних X%, закрытие в верхних X%.",
            ) / 100.0
        )
        strategy_params["exit_ema"] = pc2.number_input(
            "Выход EMA (баров)", 2, 50, 5, step=1,
            help="Период быстрой EMA для выхода из позиции.",
        )

    st.divider()

    # ── Пользовательская стратегия (только просмотр) ──────────────────────────
    with st.expander("📝  Загрузить свой код стратегии (только просмотр)", expanded=False):
        st.caption(
            "Загрузите или вставьте код для справки. "
            "Код **не выполняется** в текущей версии."
        )
        uploaded_file = st.file_uploader("Загрузить .py файл", type=["py"])
        if uploaded_file is not None:
            st.session_state.custom_code = uploaded_file.read().decode("utf-8")
            _log(f"Файл стратегии загружен: {uploaded_file.name}")
            st.success(f"Загружен: {uploaded_file.name}")

        st.text_area(
            "Вставить код стратегии",
            height=160,
            label_visibility="collapsed",
            placeholder="# Вставьте код стратегии для справки...\n# Код не выполняется.",
            key="custom_code",
        )

    st.divider()

    # ── Источник данных ───────────────────────────────────────────────────────
    st.subheader("📡  Источник данных")

    data_mode = st.radio(
        "Источник",
        ["Синтетические данные", "Загрузить CSV", "Получить с биржи"],
        label_visibility="collapsed",
    )

    df_loaded: pd.DataFrame | None = None

    if data_mode == "Синтетические данные":
        st.caption("Синтетические OHLCV данные BTC/USDT для быстрого тестирования.")
        n_bars = st.slider("Количество баров", 500, 5000, 2000, step=100)
        sample_freq = st.selectbox(
            "Таймфрейм",
            ["15m", "1m", "5m", "1h", "4h", "1d"],
            index=0,
        )
        if st.button("⚡  Сгенерировать данные", use_container_width=True):
            df_loaded = generate_sample_ohlcv(
                n_bars=n_bars, start="2023-01-01", freq=sample_freq
            )
            st.session_state.df = df_loaded
            st.session_state.data_label = f"Синтетика BTC/USDT {sample_freq} — {n_bars} баров"
            st.session_state.backtest_results = None
            _log(f"Синтетические данные: {n_bars} баров, таймфрейм {sample_freq}")

    elif data_mode == "Загрузить CSV":
        st.caption("CSV должен содержать колонки: timestamp, open, high, low, close, volume")
        csv_file = st.file_uploader("Загрузить OHLCV CSV", type=["csv"])
        if csv_file is not None:
            try:
                dm = DataManager()
                df_loaded = dm.load_from_csv(csv_file.read())
                st.session_state.df = df_loaded
                st.session_state.data_label = f"CSV: {csv_file.name}  ({len(df_loaded)} баров)"
                st.session_state.backtest_results = None
                _log(f"CSV загружен: {csv_file.name}, баров: {len(df_loaded)}")
                st.success(f"Загружено {len(df_loaded):,} баров")
            except Exception as exc:
                st.error(f"Ошибка CSV: {exc}")

    else:  # Получить с биржи
        exc_sel = st.selectbox("Биржа", ["binance", "bybit"])
        sym_sel = st.selectbox(
            "Торговая пара",
            TOP_50_PAIRS,
            index=0,
            help="Топ-50 торговых пар по капитализации (USDT)",
        )
        tf_sel = st.selectbox("Таймфрейм", ["1m", "5m", "15m", "1h", "4h", "1d"], index=2)
        limit_sel = st.slider("Количество баров", 100, 1000, 500)
        st.info("📌 Только публичные OHLCV данные — API ключ не нужен")
        if st.button("📥  Загрузить с биржи", use_container_width=True):
            with st.spinner(f"Загрузка с {exc_sel}…"):
                try:
                    dm = DataManager()
                    df_loaded = dm.load_from_exchange(exc_sel, sym_sel, tf_sel, limit=limit_sel)
                    st.session_state.df = df_loaded
                    st.session_state.data_label = (
                        f"{exc_sel.title()} {sym_sel} {tf_sel} — {len(df_loaded)} баров"
                    )
                    st.session_state.backtest_results = None
                    _log(f"Загружено {len(df_loaded)} баров: {exc_sel} {sym_sel} {tf_sel}")
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
        st.info("⏳  Данные не загружены — выберите источник выше")


# ══════════════════════════════════════════════════════════════════════════════
# ЦЕНТРАЛЬНАЯ КОЛОНКА — Настройка + Запуск
# ══════════════════════════════════════════════════════════════════════════════
with center_col:

    st.subheader("⚙️  Параметры бэктеста")

    dc1, dc2 = st.columns(2)
    with dc1:
        start_date = st.date_input("Дата начала", value=pd.Timestamp("2026-01-01"))
    with dc2:
        end_date = st.date_input("Дата окончания", value=pd.Timestamp("2026-06-22"))

    # Валидация дат
    _dates_ok = end_date >= start_date
    if not _dates_ok:
        st.error("⚠️  Дата окончания не может быть раньше даты начала.")

    cc1, cc2 = st.columns(2)
    with cc1:
        initial_capital = st.number_input(
            "Начальный капитал (USDT)", 100, 10_000_000, 10_000, step=500
        )
    with cc2:
        fee_pct = st.number_input(
            "Комиссия (%)", 0.0, 5.0, 0.1, step=0.01, format="%.3f"
        )

    sc1, sc2 = st.columns(2)
    with sc1:
        slippage_pct = st.number_input(
            "Проскальзывание (%)", 0.0, 5.0, 0.05, step=0.01, format="%.3f"
        )
    with sc2:
        st.number_input(
            "Риск на сделку (%)", 0.1, 100.0, 1.0, step=0.1, format="%.1f"
        )

    st.selectbox(
        "Метод расчёта позиции",
        ["% от капитала", "Фиксированный размер", "На основе риска"],
    )
    st.text_input("Модель комиссии", value="Taker/Maker фиксированная", disabled=True)
    st.text_input(
        "Исполнение ордеров", value="Маркет-ордер по цене закрытия свечи", disabled=True
    )

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
            else "Нажмите для запуска бэктеста"
        ),
    )

    st.button(
        "📄  Бумажная торговля — отключена",
        disabled=True,
        use_container_width=True,
    )
    st.markdown(
        """
        <div style="
            background:#2a0a0a;border:1px solid #7a0000;border-radius:6px;
            padding:10px 16px;text-align:center;color:#ff6666;font-weight:600;
        ">
            🔒  Живая торговля — ЗАБЛОКИРОВАНА / ОТКЛЮЧЕНА
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Генерация команды CLI ─────────────────────────────────────────────────
    if prepare_btn:
        fee_frac = fee_pct / 100.0
        slip_frac = slippage_pct / 100.0
        cmd = (
            f"python -m backtesting.run_strategy \\\n"
            f"  --strategy {selected_key} \\\n"
            f"  --data-source local_csv \\\n"
            f"  --csv data/historical/btc_usdt.csv \\\n"
            f"  --start {start_date} \\\n"
            f"  --end {end_date} \\\n"
            f"  --capital {initial_capital} \\\n"
            f"  --fee {fee_frac} \\\n"
            f"  --slippage {slip_frac}"
        )
        for k, v in strategy_params.items():
            cmd += f" \\\n  --param {k}={v}"
        st.session_state.command_preview = cmd
        _log("Команда CLI сформирована")

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
                    st.error("Нет данных в выбранном диапазоне дат.")
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
                        f"Бэктест завершён — {selected_label}  "
                        f"сделок: {metrics['total_trades']}  "
                        f"доходность: {metrics['total_return']}%"
                    )
            except Exception as exc:
                st.error(f"Ошибка бэктеста: {exc}")
                _log(f"Ошибка: {exc}")

    # ── Предпросмотр команды CLI ──────────────────────────────────────────────
    st.divider()
    st.subheader("💻  Команда CLI")
    st.caption("Только предпросмотр — команда не выполняется")

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
                $ <span style="color:#3fb950">ожидание конфигурации…</span><br>
                &gt; нажмите <strong style="color:#e6edf3">📋 Подготовить команду CLI</strong>
            </div>
            """,
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
        st.caption(
            f"{res['strategy_label']}  |  {res['bars']:,} баров  |  "
            f"параметры: {res['strategy_params']}"
        )

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

        for label, value, colour in METRIC_DEFS:
            mc, vc = st.columns([1.5, 1])
            mc.markdown(f"<small><b>{label}</b></small>", unsafe_allow_html=True)
            colour_map = {
                "green": "#4caf50", "red": "#f44336",
                "orange": "#ff9800", "default": "#aaaaaa",
            }
            hex_c = colour_map.get(colour, "#aaaaaa")
            vc.markdown(
                f"<span style='font-family:monospace;color:{hex_c};font-size:13px;'>"
                f"{value}</span>",
                unsafe_allow_html=True,
            )
    else:
        PENDING_METRICS_RU = [
            "Общая доходность", "Макс. просадка", "Коэф. Шарпа", "Коэф. Сортино",
            "Профит-фактор", "% выигрышных", "Всего сделок", "Ср. доход/сделка",
        ]
        for label in PENDING_METRICS_RU:
            mc, vc = st.columns([1.5, 1])
            mc.markdown(f"<small><b>{label}</b></small>", unsafe_allow_html=True)
            vc.markdown(
                "<code style='color:#444;font-size:11px;'>—</code>",
                unsafe_allow_html=True,
            )

    st.divider()

    # ── Журнал действий ───────────────────────────────────────────────────────
    st.subheader("📋  Журнал")
    st.caption("Локальные логи сессии — без данных биржи и API")

    if st.button("🗑  Очистить журнал", use_container_width=True):
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
            "<small style='color:#555;'>Журнал пуст.</small>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ПОЛНАЯ ШИРИНА — Кривая капитала и журнал сделок (после бэктеста)
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
        fig.add_trace(
            go.Scatter(
                x=equity_curve.index,
                y=equity_curve["equity"],
                mode="lines",
                name="Капитал",
                line=dict(color="#00e676", width=1.5),
                fill="tozeroy",
                fillcolor="rgba(0,230,118,0.06)",
            )
        )
        fig.add_hline(
            y=float(equity_curve["equity"].iloc[0]),
            line_dash="dot",
            line_color="#555",
            annotation_text="Начальный капитал",
        )
        fig.update_layout(
            template="plotly_dark",
            height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title=None,
            yaxis_title="Портфель (USDT)",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.line_chart(equity_curve["equity"])

    # ── Журнал сделок ─────────────────────────────────────────────────────────
    if trades:
        with st.expander(f"📄  Журнал сделок  ({len(trades)} сделок)", expanded=False):
            trade_rows = []
            for t in trades:
                trade_rows.append(
                    {
                        "Вход (время)": str(t.entry_ts)[:16],
                        "Выход (время)": str(t.exit_ts)[:16] if t.exit_ts else "Открыта",
                        "Цена входа": f"{t.entry_price:,.2f}",
                        "Цена выхода": f"{t.exit_price:,.2f}" if t.exit_price else "—",
                        "PnL (USDT)": f"{t.pnl:+.2f}",
                        "Доходность (%)": f"{t.pnl_pct:+.2f}%",
                    }
                )
            st.dataframe(
                pd.DataFrame(trade_rows),
                use_container_width=True,
                hide_index=True,
            )


# ─── РЕЖИМ РАБОТЫ (спойлер) ───────────────────────────────────────────────────
st.divider()
with st.expander("🛡️  Режим работы", expanded=False):
    sm_col, ss_col, sf_col = st.columns(3)
    with sm_col:
        st.markdown(
            "**Текущий режим**\n"
            "- Локальный офлайн интерфейс\n"
            "- ✅ Движок бэктеста: **активен**\n"
            "- 🔒 API запросы: **отключены**\n"
            "- 🔒 Живая торговля: **отключена**"
        )
    with ss_col:
        st.markdown(
            "**Доступно сейчас**\n"
            "- ✅ Синтетические данные\n"
            "- ✅ Загрузка CSV\n"
            "- ✅ Публичные OHLCV с биржи\n"
            "- ✅ Бэктест 9 стратегий\n"
            "- ✅ Кривая капитала + сделки\n"
            "- ✅ Предпросмотр команды CLI"
        )
    with sf_col:
        st.markdown(
            "**В будущих версиях**\n"
            "- 🔜 Запуск своего кода стратегии\n"
            "- 🔜 Сравнение стратегий\n"
            "- 🔜 Оптимизация параметров\n"
            "- 🔜 Бумажная торговля\n"
            "- 🔜 Живая торговля\n"
            "- 🔜 AI оценка стратегий"
        )
