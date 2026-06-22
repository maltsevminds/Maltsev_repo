from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "historical"
RESULTS_DIR = BASE_DIR / "results"

DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CAPITAL = 10_000.0
DEFAULT_FEE = 0.001       # 0.1% as fraction
DEFAULT_SLIPPAGE = 0.0005  # 0.05% as fraction
DEFAULT_RISK_PER_TRADE = 0.01

SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
SUPPORTED_EXCHANGES = ["binance", "bybit"]
