"""Backtest reporting — CSV exports + a self-contained equity chart.

Deliberately dependency-free: the chart is hand-rendered SVG embedded in a
standalone HTML file (no plotly/matplotlib), so a backtest report opens in any
browser and the package stays lean. Writes:

    trades.csv         closed positions
    equity_curve.csv   ts, equity
    equity_curve.html  interactive-free SVG line chart + summary
    summary.json       the stats dict + metadata
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .backtester import BacktestResult


def write_reports(result: BacktestResult, out_dir: str | Path, meta: Dict | None = None) -> Dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta = meta or {}

    paths: Dict[str, str] = {}

    # trades.csv
    trades_path = out / "trades.csv"
    _write_trades_csv(result.trades, trades_path)
    paths["trades"] = str(trades_path)

    # equity_curve.csv
    eq_path = out / "equity_curve.csv"
    with eq_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "equity"])
        for ts, eq in result.equity_curve:
            w.writerow([_ts_str(ts), f"{eq:.2f}"])
    paths["equity_csv"] = str(eq_path)

    # summary.json
    sj = out / "summary.json"
    sj.write_text(json.dumps({
        "meta": meta,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "stats": result.stats,
    }, indent=2), encoding="utf-8")
    paths["summary"] = str(sj)

    # equity_curve.html
    html_path = out / "equity_curve.html"
    html_path.write_text(render_html(result, meta), encoding="utf-8")
    paths["chart"] = str(html_path)

    return paths


def _write_trades_csv(trades: List[dict], path: Path) -> None:
    cols = [
        "id", "symbol", "side", "entry_ts", "exit_ts", "entry_price",
        "avg_exit_price", "qty", "leverage", "realized_pnl", "fees",
        "exit_reason", "r", "risk_usd",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow(t)


def _ts_str(ts) -> str:
    if isinstance(ts, datetime):
        return ts.isoformat()
    return str(ts)


# --------------------------------------------------------------------------- #
# SVG equity chart
# --------------------------------------------------------------------------- #
def render_equity_svg(curve: List[Tuple], initial: float, width: int = 900, height: int = 360) -> str:
    """Render the equity curve as an SVG (equity line + initial-equity baseline)."""
    pad_l, pad_r, pad_t, pad_b = 60, 20, 20, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    if len(curve) < 2:
        return (f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
                f'<text x="{width/2}" y="{height/2}" text-anchor="middle" '
                f'fill="#888">no equity data</text></svg>')

    ys = [eq for _ts, eq in curve]
    y_min, y_max = min(ys + [initial]), max(ys + [initial])
    if y_max == y_min:
        y_max += 1.0
    n = len(curve)

    def px(i: int) -> float:
        return pad_l + plot_w * (i / (n - 1))

    def py(v: float) -> float:
        return pad_t + plot_h * (1 - (v - y_min) / (y_max - y_min))

    points = " ".join(f"{px(i):.1f},{py(eq):.1f}" for i, (_ts, eq) in enumerate(curve))
    baseline_y = py(initial)

    # y-axis gridlines / labels (5 ticks)
    ticks = []
    for k in range(5):
        v = y_min + (y_max - y_min) * k / 4
        y = py(v)
        ticks.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" y2="{y:.1f}" '
            f'stroke="#eee" stroke-width="1"/>'
            f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#666">{v:,.0f}</text>'
        )
    final = ys[-1]
    line_color = "#1a9850" if final >= initial else "#d73027"

    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="system-ui,Arial,sans-serif">'
        f'<rect width="{width}" height="{height}" fill="white"/>'
        + "".join(ticks)
        + f'<line x1="{pad_l}" y1="{baseline_y:.1f}" x2="{width - pad_r}" '
          f'y2="{baseline_y:.1f}" stroke="#bbb" stroke-dasharray="4 3" stroke-width="1"/>'
        + f'<polyline fill="none" stroke="{line_color}" stroke-width="2" points="{points}"/>'
        + '</svg>'
    )


def render_html(result: BacktestResult, meta: Dict) -> str:
    s = result.stats
    svg = render_equity_svg(result.equity_curve, result.initial_equity)
    symbols = ", ".join(meta.get("symbols", []))
    window = f"{meta.get('start', '')} → {meta.get('end', '')}"
    rows = "".join(
        f"<tr><td>{k}</td><td style='text-align:right'>{v}</td></tr>"
        for k, v in [
            ("Trades", s["trades"]),
            ("Win rate", f"{s['win_rate']:.1%}"),
            ("Profit factor", f"{s['profit_factor']:.2f}"),
            ("Total return", f"{s['total_return_pct']:.2f}%"),
            ("Max drawdown", f"{s['max_drawdown_pct']:.2f}%"),
            ("Avg trade", f"{s['avg_trade']:.2f}"),
            ("Initial equity", f"${result.initial_equity:,.2f}"),
            ("Final equity", f"${result.final_equity:,.2f}"),
        ]
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Backtest — equity curve</title>
<style>
 body{{font-family:system-ui,Arial,sans-serif;margin:24px;color:#222}}
 h1{{font-size:20px}} .sub{{color:#666;margin-bottom:16px}}
 table{{border-collapse:collapse;margin-top:12px}}
 td{{padding:4px 14px;border-bottom:1px solid #eee}}
</style></head><body>
<h1>Momentum-scalp backtest</h1>
<div class="sub">{symbols} &nbsp;|&nbsp; {window}</div>
{svg}
<table>{rows}</table>
</body></html>"""
