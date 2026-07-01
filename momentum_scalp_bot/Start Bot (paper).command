#!/usr/bin/env bash
# Double-click in Finder: launches the bot in PAPER mode (virtual balance,
# live data, no real orders) and opens the live dashboard in your browser.
cd "$(dirname "$0")"
CONFIG="${CONFIG:-config.yaml}" exec bash scripts/start.sh paper
