#!/usr/bin/env bash
# Double-click in Finder: launches the bot on BYBIT TESTNET (real orders on
# fake money) and opens the live dashboard. Needs BYBIT_TESTNET_API_KEY /
# BYBIT_TESTNET_API_SECRET in .env (create keys at https://testnet.bybit.com).
cd "$(dirname "$0")"
exec env CONFIG=config.bybit.yaml bash scripts/start.sh testnet
