#!/usr/bin/env bash
# Double-click in Finder: runs a backtest and opens the equity-curve report.
cd "$(dirname "$0")"
CONFIG="${CONFIG:-config.yaml}" exec bash scripts/start.sh backtest
