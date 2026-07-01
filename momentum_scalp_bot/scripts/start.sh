#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-command launcher: sets up the venv + deps, deploys the dashboard, and
# runs the bot. Used by the double-clickable *.command shortcuts and `make`.
#
#   ./scripts/start.sh [mode]        mode = paper (default) | testnet | live | backtest
#   MODE=testnet CONFIG=config.bybit.testnet.yaml ./scripts/start.sh
#
# Env knobs: MODE, CONFIG (default config.yaml), DASH_PORT (default 8787).
# ---------------------------------------------------------------------------
set -euo pipefail

# Project root = parent of this scripts/ dir, regardless of where we're called.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

MODE="${1:-${MODE:-paper}}"
CONFIG="${CONFIG:-config.yaml}"
PORT="${DASH_PORT:-8787}"

# Prefer python3.11 (what the bot targets) if present.
PY="python3"
command -v python3.11 >/dev/null 2>&1 && PY="python3.11"

if [ ! -d .venv ]; then
  echo "· creating virtualenv (.venv) with $PY ..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "· installing/refreshing dependencies ..."
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt

mkdir -p data logs results

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "· created .env from template — add your API keys before testnet/live."
fi

# --- backtest: run + open the equity report, no live dashboard needed --------
if [ "$MODE" = "backtest" ]; then
  echo "· running backtest ($CONFIG) -> ./results ..."
  python -m momentum_scalp.main --mode backtest --config "$CONFIG" --report-dir ./results
  if [ -f results/equity_curve.html ] && command -v open >/dev/null 2>&1; then
    open results/equity_curve.html
  fi
  exit 0
fi

# --- paper/testnet/live: deploy dashboard, then run the bot ------------------
DB="$(python -c "from momentum_scalp.config import load_config;print(load_config('$CONFIG').database.path)" 2>/dev/null || echo data/bot.sqlite)"

echo "· deploying dashboard on http://localhost:$PORT  (db=$DB) ..."
python -m momentum_scalp.dashboard --db "$DB" --serve "$PORT" --mode "$MODE" >logs/dashboard.log 2>&1 &
DASH_PID=$!
cleanup() { echo; echo "· stopping dashboard ..."; kill "$DASH_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
sleep 1
command -v open >/dev/null 2>&1 && open "http://localhost:$PORT" || true

echo "· starting bot in '$MODE' mode  (config=$CONFIG) — Ctrl-C to stop ..."
python -m momentum_scalp.main --mode "$MODE" --config "$CONFIG"
