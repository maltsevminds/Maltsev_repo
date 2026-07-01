#!/usr/bin/env bash
# Double-click in Finder: PAPER mode on Bybit (live Bybit data, virtual
# balance, NO real orders — no keys needed) + live dashboard.
cd "$(dirname "$0")"
exec env CONFIG=config.bybit.yaml bash scripts/start.sh paper
