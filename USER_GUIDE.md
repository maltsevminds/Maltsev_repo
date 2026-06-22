# Strategy Testing Lab — Полное руководство

Локальная система для тестирования криптовалютных торговых стратегий. Без API ключей, без подключения к бирже, без риска.

---

## 🚀 Быстрый старт

### 1. Установка

```bash
# Клонировать репо
git clone https://github.com/maltsevminds/Maltsev_repo.git
cd Maltsev_repo

# Установить зависимости
python3 -m pip install -r requirements.txt
```

### 2. Запуск UI

```bash
python3 -m streamlit run dashboard/strategy_testing_lab.py
```

Откроется в браузере на `http://localhost:8501`

---

## 📊 Основной рабочий процесс

### Шаг 1: Выберите стратегию

В левой колонке **Strategy** — 4 готовые стратегии:

1. **SMA Crossover** (Trend Following)
   - Параметры: Fast MA (20), Slow MA (50)
   - Вход: когда быстрая скользящая пересекает медленную вверх
   - Выход: пересечение вниз
   - Хорошо на трендовых рынках

2. **EMA Crossover** (Trend Following)
   - Параметры: Fast EMA (12), Slow EMA (26)
   - Как SMA, но быстрее реагирует
   - Подходит для средних временных фреймов

3. **RSI Mean Reversion** (Mean Reversion)
   - Параметры: Period (14), Oversold (30), Overbought (70)
   - Вход: выход из перепроданности
   - Выход: выход из перекупленности
   - Хорош на флэтовых рынках

4. **MACD Momentum** (Momentum)
   - Параметры: Fast (12), Slow (26), Signal (9)
   - Следит за пересечением MACD и signal line
   - Много сделок, может быть нестабилен

---

### Шаг 2: Загрузите данные

В левой колонке **Data Source** — 3 варианта:

#### А) Синтетические данные (рекомендуется для тестов)
```
- "Sample Data (synthetic)"
- Регулятор "Number of bars" — от 500 до 5000
- Выбрать Timeframe: 1h, 4h, 1d, 15m
- Нажать "⚡ Generate Sample Data"
```
**Плюсы:** Быстро, не требует подключения, реальный движок для тестов

#### Б) Загрузить CSV
```
- "Upload CSV"
- CSV должен иметь колонки: timestamp, open, high, low, close, volume
- Примеры форматов:
  * Binance: https://www.binance.com/en/spot/trading/BTCUSDT?type=cross
  * Kraken, Coinbase eksport через их API
  * Любой формат с датой + OHLCV
```

**Формат CSV:**
```
timestamp,open,high,low,close,volume
2023-01-01 00:00:00,16500.50,16550.75,16450.25,16490.00,45.2
2023-01-01 01:00:00,16490.00,16550.00,16480.00,16540.00,38.9
...
```

#### В) Публичные данные с биржи (Binance/Bybit)
```
- "Fetch from Exchange"
- Выбрать биржу (binance/bybit)
- Symbol (BTC/USDT, ETH/USDT и т.д.)
- Timeframe (1m, 5m, 15m, 1h, 4h, 1d)
- Количество баров (100–1000)
- Нажать "📥 Fetch OHLCV"
```

**Ограничения:** Только публичные исторические данные, без API ключей, максимум ~1000 баров в одном запросе

---

### Шаг 3: Настройте параметры бэктеста

В центральной колонке **Backtest Configuration:**

```
Start Date       — с какого числа начать тест
End Date         — до какого числа
Initial Capital  — начальный капитал в USDT
Fee (%)          — комиссия биржи (0.1% = 0.001)
Slippage (%)     — проскальзывание (0.05% = 0.0005)
Risk per Trade   — не используется в простой версии
Position Sizing  — всегда "Percent of Equity"
```

**Примеры:**
- Реалистичные параметры для Binance: Fee 0.1%, Slippage 0.05%
- Для Bybit: Fee 0.1%, Slippage 0.05%
- Для симуляции: Fee 0.05%, Slippage 0%

---

### Шаг 4: Запустите бэктест

```
1. Нажмите "▶️ Run Backtest" (активна, когда загружены данные)
2. Подождите 1–5 секунд
3. Увидите результаты в правой колонке
```

---

## 📈 Чтение результатов

После запуска бэктеста вы увидите:

### Основные метрики

| Метрика | Что это | Хорошее значение |
|---------|---------|-----------------|
| **Total Return** | Общий процент прибыли | +20% — +50% |
| **Max Drawdown** | Максимальное падение от пика | −10% — −30% |
| **Sharpe Ratio** | Доход на единицу риска (годовой) | > 1.0 хорошо |
| **Sortino Ratio** | То же, но считает только вниз. потери | > 1.0 хорошо |
| **Profit Factor** | Брутто прибыль / брутто убытки | > 1.5 хорошо |
| **Win Rate** | Процент прибыльных сделок | > 50% хорошо |
| **Total Trades** | Количество сделок | Зависит от стратегии |
| **Avg Trade Return** | Средняя прибыль/убыток на сделку | Положительная хорошо |
| **Final Equity** | Конечный капитал | > Initial Capital |

### График (Equity Curve)

- **Зелёная линия** = ваш портфель с течением времени
- **Пунктир** = начальный капитал
- **Восходящий тренд** = стратегия растёт
- **Падающий тренд** = стратегия проигрывает

### Журнал сделок (Trade Log)

```
Entry Time      | Exit Time       | Entry Price | Exit Price | PnL (USDT) | Return (%)
2023-01-05...   | 2023-01-08...   | 16500.00    | 16600.00   | +100.00    | +0.60%
...
```

---

## 💻 Использование CLI (продвинутое)

Команда вида:
```bash
python -m backtesting.run_strategy \
  --strategy sma_cross \
  --data-source local_csv \
  --csv data.csv \
  --start 2023-01-01 --end 2024-01-01 \
  --capital 10000 --fee 0.001 --slippage 0.0005 \
  --param fast=20 --param slow=50
```

**Параметры:**
```
--strategy          sma_cross | ema_cross | rsi | macd
--data-source       local_csv | binance | bybit
--csv               путь к CSV файлу
--start, --end      дата (YYYY-MM-DD)
--capital           начальный капитал
--fee               комиссия дробью (0.001 = 0.1%)
--slippage          проскальзывание дробью
--param KEY=VALUE   параметр стратегии (может быть несколько)
--output            console | json
```

**Вывод JSON:**
```bash
python -m backtesting.run_strategy --strategy sma_cross ... --output json
```

---

## 📚 Примеры использования

### Пример 1: Быстрый тест SMA на синтетике

```
1. Launch UI
2. Strategy: "SMA Crossover" (defaults: fast=20, slow=50)
3. Data Source: "Sample Data (synthetic)" → 2000 bars, 1h
   Нажать "⚡ Generate Sample Data"
4. Configuration: Default (Capital 10000, Fee 0.1%, Slippage 0.05%)
5. Нажать "▶️ Run Backtest"
→ Результат за 2 сек
```

### Пример 2: RSI на реальных данных с Binance

```
1. Strategy: "RSI Mean Reversion" (defaults: period=14, oversold=30, overbought=70)
2. Data Source: "Fetch from Exchange"
   - Exchange: binance
   - Symbol: BTC/USDT
   - Timeframe: 1h
   - Bars: 500
   Нажать "📥 Fetch OHLCV"
3. Configuration:
   - Start: 2024-01-01
   - End: 2024-06-01
   - Initial Capital: 5000
   - Fee: 0.001
   - Slippage: 0.0005
4. Нажать "▶️ Run Backtest"
→ Результат за 3–5 сек
```

### Пример 3: MACD с CSV (ваши данные)

```
1. Скачать CSV с Binance (торговая пара → 1D свечи → экспорт)
2. Strategy: "MACD Momentum" (defaults: fast=12, slow=26, signal=9)
3. Data Source: "Upload CSV" → выбрать файл
4. Configuration: Ваши параметры
5. Нажать "▶️ Run Backtest"
```

---

## ⚠️ Что нужно знать

### Ограничения бэктеста

1. **Исполнение:** Сделки исполняются по цене close (закрытию свечи)
2. **Позиции:** Только длинные (buy and hold до exit signal)
3. **Размер:** Все свободные деньги идят в одну позицию, нет переоценки между сделками
4. **Комиссии:** Единообразные, не зависят от объёма
5. **Нет слэйпэджа на вход:** Проскальзывание только считается в цене исполнения

### Что НЕ РЕАЛИЗОВАНО (будущие версии)

- ❌ Живая торговля (Live trading)
- ❌ Бумажная торговля (Paper trading)
- ❌ Пользовательские стратегии (Custom code execution)
- ❌ Оптимизация параметров (Optimization)
- ❌ Множественные позиции одновременно
- ❌ Шорты (short selling)
- ❌ Фьючерсы с кредитом
- ❌ Лимитные ордера (только маркет)

---

## 🔧 Модификация стратегий (для разработчиков)

### Структура стратегии

```python
# strategies/my_strategy.py
from strategies.base import BaseStrategy
import pandas as pd

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "Your description"
    
    def __init__(self, param1=20, param2=50):
        self.param1 = param1
        self.param2 = param2
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # df имеет колонки: open, high, low, close, volume
        # Вернуть Series с индексом как df.index
        # Значения: 1 = вход, -1 = выход, 0 = ничего
        
        signal = pd.Series(0, index=df.index)
        # ваша логика сигналов
        return signal
    
    def get_params(self):
        return {"param1": self.param1, "param2": self.param2}
```

### Регистрация новой стратегии

1. Создать файл `strategies/my_strategy.py`
2. Добавить в `strategies/registry.py`:

```python
from strategies.my_strategy import MyStrategy

STRATEGY_REGISTRY["my_strategy"] = MyStrategy
STRATEGY_LABELS["my_strategy"] = "My Strategy Label"
```

3. Перезагрузить UI — новая стратегия будет в выпадающем меню

---

## 🐛 Решение проблем

### Ошибка: "No module named streamlit"
```bash
python3 -m pip install -r requirements.txt
```

### Ошибка при загрузке CSV
```
Проверить:
- Колонки: timestamp, open, high, low, close, volume (case-insensitive)
- Нет пустых строк в конце
- Дата в формате: YYYY-MM-DD или YYYY-MM-DD HH:MM:SS
```

### Бэктест занимает долго
- Скоротить диапазон дат
- Уменьшить количество баров
- Выбрать более крупный таймфрейм (1h вместо 1m)

### Стратегия не производит сделок
- Может быть, выбрана неподходящая для рынка стратегия
- Попробуйте изменить параметры
- Проверьте временной диапазон (может нет нужных условий)

---

## 📞 Поддержка

Проблемы или предложения?
- Issues: https://github.com/maltsevminds/Maltsev_repo/issues
- Email: maltsevig@gmail.com

---

## 📄 Лицензия

MIT — используйте в образовательных целях.

**Дисклеймер:** Это инструмент для исследований и обучения. Результаты бэктеста не гарантируют будущие результаты. Не используйте для реальной торговли без риск-менеджмента и стрессивных тестов.
