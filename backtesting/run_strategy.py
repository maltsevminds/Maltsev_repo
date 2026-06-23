#!/usr/bin/env python3
"""
CLI entry point for running strategy backtests.

Usage:
    python -m backtesting.run_strategy \\
        --strategy sma_cross \\
        --market BTC/USDT \\
        --timeframe 1h \\
        --data-source local_csv \\
        --csv data/historical/btc_usdt_1h.csv \\
        --start 2023-01-01 --end 2024-01-01 \\
        --capital 10000 --fee 0.001 --slippage 0.0005
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.data_manager import DataManager
from strategies.registry import get_strategy, list_strategies
from backtesting.engine import run_backtest
from backtesting.performance import calculate_metrics


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run a strategy backtest from the command line"
    )
    p.add_argument("--strategy", required=True, help=f"One of: {list_strategies()}")
    p.add_argument("--market", default="BTC/USDT")
    p.add_argument("--timeframe", default="1h")
    p.add_argument(
        "--data-source",
        default="local_csv",
        choices=["local_csv", "binance", "bybit"],
    )
    p.add_argument("--csv", default=None, help="CSV path (required for local_csv mode)")
    p.add_argument("--start", default=None, help="e.g. 2023-01-01")
    p.add_argument("--end", default=None, help="e.g. 2024-01-01")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--fee", type=float, default=0.001, help="Fee as fraction (0.001 = 0.1%)")
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--stop-loss", type=float, default=0.0, help="Stop-loss as fraction (0.02 = 2%)")
    p.add_argument("--take-profit", type=float, default=0.0, help="Take-profit as fraction (0.05 = 5%)")
    p.add_argument("--trailing-stop", type=float, default=0.0, help="Trailing stop as fraction (0.03 = 3%)")
    p.add_argument("--hold-bars", type=int, default=0, help="Force-close after N bars (0 = disabled)")
    p.add_argument("--risk-per-trade", type=float, default=0.01)
    p.add_argument("--position-sizing", default="percent_of_equity")
    p.add_argument(
        "--output",
        default="console",
        choices=["console", "json"],
        help="Output format",
    )
    # Strategy-specific params (key=value pairs)
    p.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Strategy parameter, e.g. --param fast=20 --param slow=50",
    )
    return p


def parse_strategy_params(raw: list[str]) -> dict:
    params = {}
    for item in raw:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        try:
            params[k.strip()] = int(v)
        except ValueError:
            try:
                params[k.strip()] = float(v)
            except ValueError:
                params[k.strip()] = v.strip()
    return params


def main() -> None:
    args = build_parser().parse_args()
    dm = DataManager()

    print(f"\n[backtest] data-source={args.data_source}  market={args.market}  tf={args.timeframe}")

    if args.data_source == "local_csv":
        if args.csv is None:
            print("[error] --csv <path> is required for local_csv mode")
            sys.exit(1)
        df = dm.load_from_csv(args.csv)
    else:
        df = dm.load_from_exchange(
            args.data_source,
            args.market,
            args.timeframe,
            start=args.start,
            end=args.end,
        )

    df = dm.filter_dates(df, args.start, args.end)
    print(f"[backtest] bars={len(df)}  range={df.index[0]}  →  {df.index[-1]}")

    strategy_params = parse_strategy_params(args.param)
    strategy = get_strategy(args.strategy, strategy_params)
    print(f"[backtest] strategy={strategy}")

    signals = strategy.generate_signals(df)
    n_long = int((signals == 1).sum())
    n_exit = int((signals == -1).sum())
    print(f"[backtest] signals — entries={n_long}  exits={n_exit}")

    equity_curve, trades = run_backtest(
        df,
        signals,
        initial_capital=args.capital,
        fee=args.fee,
        slippage=args.slippage,
        stop_loss=args.stop_loss,
        take_profit=args.take_profit,
        trailing_stop=args.trailing_stop,
        hold_bars=args.hold_bars,
    )

    metrics = calculate_metrics(equity_curve, trades, args.capital)

    if args.output == "json":
        print(json.dumps(metrics, indent=2))
        return

    width = 42
    print("\n" + "=" * width)
    print("  BACKTEST RESULTS")
    print("=" * width)
    labels = {
        "total_return": "Total Return (%)",
        "max_drawdown": "Max Drawdown (%)",
        "sharpe_ratio": "Sharpe Ratio",
        "sortino_ratio": "Sortino Ratio",
        "profit_factor": "Profit Factor",
        "win_rate": "Win Rate (%)",
        "total_trades": "Total Trades",
        "avg_trade_return": "Avg Trade Return (%)",
        "final_equity": "Final Equity (USDT)",
    }
    for key, label in labels.items():
        print(f"  {label:<26} {metrics[key]}")
    print("=" * width + "\n")


if __name__ == "__main__":
    main()
