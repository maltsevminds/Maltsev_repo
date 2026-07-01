"""Live monitoring dashboard over the bot's SQLite DB.

Dependency-free (no Streamlit/Flask): renders a self-contained HTML page —
KPIs, the equity curve (reusing the SVG renderer), open positions, recent
closed trades and the event log. Either write it once (``--out``) or run the
built-in auto-refreshing server (``--serve PORT``, stdlib http.server) which
re-reads the DB on every request so it tracks a running bot live.

    python -m momentum_scalp.dashboard --db data/bot.sqlite --serve 8787
    python -m momentum_scalp.dashboard --db data/bot.sqlite --out dashboard.html
"""

from __future__ import annotations

import argparse
import html as _html
from typing import List, Optional

from .db import Database
from .reporting import render_equity_svg


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _equity_curve(db: Database, mode: Optional[str]):
    if mode:
        rows = list(db.conn.execute(
            "SELECT ts, equity FROM equity WHERE mode=? ORDER BY id", (mode,)))
    else:
        rows = list(db.conn.execute("SELECT ts, equity FROM equity ORDER BY id"))
    return [(r["ts"], float(r["equity"])) for r in rows]


def _kpis(db: Database, mode: Optional[str], curve) -> dict:
    last = db.last_equity(mode)
    closed = db.list_closed_trades(mode, limit=100000)
    pnls = [t["realized_pnl"] for t in closed]
    wins = [p for p in pnls if p > 0]
    return {
        "equity": last["equity"] if last else (curve[-1][1] if curve else 0.0),
        "high_water_mark": last["high_water_mark"] if last else 0.0,
        "open_positions": len(db.list_open_positions(mode)),
        "closed_trades": len(closed),
        "realized_pnl": sum(pnls),
        "win_rate": (len(wins) / len(pnls)) if pnls else 0.0,
    }


def _table(headers: List[str], rows: List[list]) -> str:
    if not rows:
        return '<p class="empty">— none —</p>'
    head = "".join(f"<th>{_html.escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_html.escape(str(c))}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _fmt(x, nd=2):
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def render_dashboard(db: Database, mode: Optional[str] = None, refresh: int = 0) -> str:
    curve = _equity_curve(db, mode)
    k = _kpis(db, mode, curve)
    initial = curve[0][1] if curve else k["equity"]
    svg = render_equity_svg(curve, initial)

    open_rows = [
        [p["symbol"], p["side"], _fmt(p["remaining_qty"], 4), _fmt(p["entry_price"], 4),
         _fmt(p["stop_price"], 4), p["leverage"], _fmt(p["realized_pnl"])]
        for p in db.list_open_positions(mode)
    ]
    closed_rows = [
        [t["symbol"], t["side"], _fmt(t["entry_price"], 4), _fmt(t["avg_exit_price"], 4),
         _fmt(t["realized_pnl"]), t["exit_reason"] or "-", t["exit_ts"] or "-"]
        for t in db.list_closed_trades(mode, limit=25)
    ]
    event_rows = [
        [e["ts"], e["level"], e["kind"], e["symbol"] or "-", e["message"]]
        for e in db.recent_events(limit=25)
    ]

    pnl_class = "pos" if k["realized_pnl"] >= 0 else "neg"
    meta_refresh = f'<meta http-equiv="refresh" content="{refresh}">' if refresh > 0 else ""
    scope = mode or "all modes"

    kpi_cards = "".join(f'<div class="card"><div class="k">{label}</div>'
                        f'<div class="v {cls}">{val}</div></div>'
                        for label, val, cls in [
        ("Equity", f"${_fmt(k['equity'])}", ""),
        ("High-water mark", f"${_fmt(k['high_water_mark'])}", ""),
        ("Realized PnL", f"${_fmt(k['realized_pnl'])}", pnl_class),
        ("Open positions", k["open_positions"], ""),
        ("Closed trades", k["closed_trades"], ""),
        ("Win rate", f"{k['win_rate']:.1%}", ""),
    ])

    return f"""<!doctype html>
<html><head><meta charset="utf-8">{meta_refresh}
<title>momentum-scalp — dashboard</title>
<style>
 body{{font-family:system-ui,Arial,sans-serif;margin:20px;color:#1c1c1c;background:#fafafa}}
 h1{{font-size:20px;margin:0 0 2px}} .sub{{color:#777;margin-bottom:16px;font-size:13px}}
 .cards{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:18px}}
 .card{{background:#fff;border:1px solid #eee;border-radius:8px;padding:10px 16px;min-width:130px}}
 .card .k{{font-size:12px;color:#888}} .card .v{{font-size:20px;font-weight:600}}
 .pos{{color:#1a9850}} .neg{{color:#d73027}}
 h2{{font-size:15px;margin:20px 0 6px}}
 table{{border-collapse:collapse;background:#fff;width:100%;font-size:13px}}
 th,td{{padding:5px 10px;border-bottom:1px solid #eee;text-align:left}}
 th{{background:#f4f4f4;font-weight:600}} .empty{{color:#999;font-size:13px}}
 svg{{background:#fff;border:1px solid #eee;border-radius:8px}}
</style></head><body>
<h1>momentum-scalp dashboard</h1>
<div class="sub">scope: {scope}{" · auto-refresh " + str(refresh) + "s" if refresh else ""}</div>
<div class="cards">{kpi_cards}</div>
{svg}
<h2>Open positions</h2>
{_table(["symbol","side","remaining","entry","stop","lev","realized"], open_rows)}
<h2>Recent closed trades</h2>
{_table(["symbol","side","entry","exit","pnl","reason","exit_ts"], closed_rows)}
<h2>Events</h2>
{_table(["ts","level","kind","symbol","message"], event_rows)}
</body></html>"""


def dashboard_html(db_path: str, mode: Optional[str] = None, refresh: int = 0) -> str:
    db = Database(db_path)
    try:
        return render_dashboard(db, mode, refresh)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Server (stdlib only)
# --------------------------------------------------------------------------- #
def serve(db_path: str, port: int, mode: Optional[str] = None, refresh: int = 5) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                body = dashboard_html(db_path, mode, refresh).encode("utf-8")
            except Exception as exc:  # noqa: BLE001
                body = f"<pre>dashboard error: {exc}</pre>".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence default logging
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"dashboard on http://localhost:{port}  (db={db_path}, refresh={refresh}s) — Ctrl-C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="momentum_scalp.dashboard",
                                description="Live monitoring dashboard for the bot's SQLite DB")
    p.add_argument("--db", default="data/bot.sqlite", help="path to the bot SQLite DB")
    p.add_argument("--mode", default=None, help="filter by run mode (paper/testnet/live)")
    p.add_argument("--out", default=None, help="write the HTML to this file and exit")
    p.add_argument("--serve", type=int, default=None, metavar="PORT",
                   help="serve an auto-refreshing dashboard on this port")
    p.add_argument("--refresh", type=int, default=5, help="auto-refresh seconds (serve mode)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.serve:
        serve(args.db, args.serve, args.mode, args.refresh)
        return 0
    html = dashboard_html(args.db, args.mode, refresh=0)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"wrote {args.out}")
    else:
        print(html)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
